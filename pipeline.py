"""
국내주식 단기 트레이딩 전략 리서치 파이프라인.

collect_data.ipynb / kiwoom1.ipynb / backtest.ipynb / 추가분석.ipynb 에 흩어져 있던
중복 정의와 1회성 탐색 코드를 정리해, 데이터 수집 -> 병합 -> 지표 계산 -> 전략
백테스트로 이어지는 재사용 가능한 함수들로 통합했다.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from pykrx import stock
from tqdm import tqdm

load_dotenv()

MAX_WORKERS = 3
REQUEST_DELAY = 0.3
REFRESH_EVERY_N = 150

_refresh_lock = threading.Lock()
_api_call_count = 0


# ---------------------------------------------------------------------------
# 1. 데이터 수집 (키움 Open API CLI)
# ---------------------------------------------------------------------------

def refresh_token() -> bool:
    result = subprocess.run(["kiwoomcli", "auth", "refresh"], capture_output=True, text=True, encoding="utf-8")
    return result.returncode == 0


def get_all_tickers() -> list[tuple[str, str]]:
    today = datetime.today().strftime("%Y%m%d")
    kospi = stock.get_market_ticker_list(today, market="KOSPI")
    kosdaq = stock.get_market_ticker_list(today, market="KOSDAQ")
    all_tickers = [(t, "KOSPI") for t in kospi] + [(t, "KOSDAQ") for t in kosdaq]
    valid = [(t, m) for t, m in all_tickers if t.isdigit()]
    print(f"전체 {len(all_tickers)}개 중 유효 코드 {len(valid)}개")
    return valid


def _parse_price(s):
    if s in (None, ""):
        return None
    return abs(int(s))


def fetch_candles(ticker_market: tuple[str, str], interval: str, data_dir: str, retry: bool = True):
    global _api_call_count
    ticker, _market = ticker_market
    filepath = os.path.join(data_dir, f"{ticker}.csv")
    if os.path.exists(filepath):
        return ticker, "skip"

    result = subprocess.run(
        ["kiwoomcli", "domestic", "candles", "stock-minute",
         "--code", ticker, "--interval", interval, "--pages", "0", "--format", "json"],
        capture_output=True, text=True, encoding="utf-8",
    )

    auth_error_in_body = False
    if result.returncode == 0 and result.stdout:
        try:
            preview = json.loads(result.stdout, strict=False)
            if preview.get("return_code") != 0:
                auth_error_in_body = True
        except Exception:
            pass

    if result.returncode != 0 or auth_error_in_body:
        err = result.stderr or result.stdout or ""
        if "429" in err or "허용된" in err:
            time.sleep(2.0)
            if retry:
                return fetch_candles(ticker_market, interval, data_dir, retry=False)
            return ticker, f"error: {err[:200]}"
        if retry and any(k in err for k in ["토큰", "인증", "expired", "unauthorized", "401", "유효하지"]):
            with _refresh_lock:
                refresh_token()
            return fetch_candles(ticker_market, interval, data_dir, retry=False)
        return ticker, f"error: {err[:200]}"

    try:
        data = json.loads(result.stdout, strict=False)
    except Exception as e:
        return ticker, f"parse_error: {e}"

    records = data.get("stk_min_pole_chart_qry")
    if not records:
        return ticker, "empty"

    df = pd.DataFrame(records)
    for col in ["cur_prc", "open_pric", "high_pric", "low_pric"]:
        df[col] = df[col].apply(_parse_price)
    df["cntr_tm"] = pd.to_datetime(df["cntr_tm"], format="%Y%m%d%H%M%S", errors="coerce")
    df = df.dropna(subset=["cntr_tm"]).sort_values("cntr_tm").reset_index(drop=True)
    df.to_csv(filepath, index=False, encoding="utf-8-sig")

    time.sleep(REQUEST_DELAY)
    with _refresh_lock:
        _api_call_count += 1
        if _api_call_count % REFRESH_EVERY_N == 0:
            refresh_token()

    return ticker, "ok"


def run_collection(interval: str, data_dir: str, max_workers: int = MAX_WORKERS) -> None:
    os.makedirs(data_dir, exist_ok=True)
    tickers = get_all_tickers()

    ok = skip = fail = 0
    failed: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_candles, tm, interval, data_dir): tm for tm in tickers}
        with tqdm(total=len(tickers), desc=f"{interval}분봉 수집") as pbar:
            for future in as_completed(futures):
                ticker, result = future.result()
                if result == "ok":
                    ok += 1
                elif result == "skip":
                    skip += 1
                else:
                    fail += 1
                    failed.append((ticker, result))
                pbar.set_postfix(성공=ok, 스킵=skip, 실패=fail)
                pbar.update(1)

    print(f"완료: 성공 {ok} / 스킵 {skip} / 실패 {fail}")
    if failed:
        print("실패 샘플:", failed[:10])


# ---------------------------------------------------------------------------
# 2. 종목별 CSV 병합
# ---------------------------------------------------------------------------

def merge_csv_files(data_dir: str, output_csv: str, output_parquet: str | None = None) -> pd.DataFrame:
    files = glob.glob(os.path.join(data_dir, "*.csv"))
    print(f"총 {len(files)}개 파일 병합 시작...")

    dfs = []
    for filepath in files:
        ticker = os.path.splitext(os.path.basename(filepath))[0]
        try:
            df = pd.read_csv(filepath)
            df.insert(0, "종목코드", ticker)
            dfs.append(df)
        except Exception as e:
            print(f"  읽기 실패: {filepath} - {e}")

    merged = pd.concat(dfs, ignore_index=True)
    merged["cntr_tm"] = pd.to_datetime(merged["cntr_tm"], errors="coerce")
    merged = merged.dropna(subset=["cntr_tm"])
    merged["종목코드"] = merged["종목코드"].astype(str).str.zfill(6)
    merged = merged.sort_values(["종목코드", "cntr_tm"]).reset_index(drop=True)

    print(f"병합 완료: 총 {len(merged):,}행, {merged['종목코드'].nunique()}종목")
    merged.to_csv(output_csv, index=False, encoding="utf-8-sig")
    if output_parquet:
        merged.to_parquet(output_parquet, index=False)
    return merged


# ---------------------------------------------------------------------------
# 3. 일봉 기술적 지표
# ---------------------------------------------------------------------------

def compute_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(length).mean()
    avg_loss = loss.rolling(length).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_stoch_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    rsi = compute_rsi(series, length)
    min_rsi = rsi.rolling(length).min()
    max_rsi = rsi.rolling(length).max()
    return (rsi - min_rsi) / (max_rsi - min_rsi)


def compute_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line


def compute_cmf(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, length: int = 20) -> pd.Series:
    range_safe = (high - low).replace(0, np.nan)
    mfm = ((close - low) - (high - close)) / range_safe
    mfv = mfm * volume
    return mfv.rolling(length).sum() / volume.rolling(length).sum()


def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def add_daily_indicators(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("날짜").copy()
    g["거래대금_근사"] = g["종가"] * g["거래량"]

    g["RSI"] = compute_rsi(g["종가"])
    g["StochRSI"] = compute_stoch_rsi(g["종가"])
    g["MACD"], g["MACD_signal"], g["MACD_hist"] = compute_macd(g["종가"])
    g["CMF"] = compute_cmf(g["고가"], g["저가"], g["종가"], g["거래량"])
    g["OBV"] = compute_obv(g["종가"], g["거래량"])

    g["MA20"] = g["종가"].rolling(20).mean()
    g["MA60"] = g["종가"].rolling(60).mean()
    g["MA20_이격도"] = (g["종가"] - g["MA20"]) / g["MA20"] * 100

    rolling_high = g["고가"].rolling(60).max()
    rolling_low = g["저가"].rolling(60).min()
    fib_range = (rolling_high - rolling_low).replace(0, np.nan)
    g["피보나치위치"] = (g["종가"] - rolling_low) / fib_range

    g["거래량_20일평균대비"] = g["거래량"] / g["거래량"].rolling(20).mean()
    g["거래대금_20일평균대비"] = g["거래대금_근사"] / g["거래대금_근사"].rolling(20).mean()

    return g


def load_daily_enriched(path: str) -> pd.DataFrame:
    daily_df = pd.read_csv(path)
    daily_df["종목코드"] = daily_df["종목코드"].astype(str).str.zfill(6)
    daily_df["날짜"] = pd.to_datetime(daily_df["날짜"])

    enriched = [add_daily_indicators(g) for _, g in daily_df.groupby("종목코드") if len(g) >= 60]
    daily_enriched = pd.concat(enriched, ignore_index=True)
    daily_enriched = daily_enriched.sort_values(["종목코드", "날짜"]).reset_index(drop=True)

    daily_enriched["전일종가"] = daily_enriched.groupby("종목코드")["종가"].shift(1)
    daily_enriched["등락률"] = (daily_enriched["종가"] - daily_enriched["전일종가"]) / daily_enriched["전일종가"] * 100
    daily_enriched["다음날_시가"] = daily_enriched.groupby("종목코드")["시가"].shift(-1)
    daily_enriched["다음날_날짜"] = daily_enriched.groupby("종목코드")["날짜"].shift(-1)
    daily_enriched["날짜차이"] = (daily_enriched["다음날_날짜"] - daily_enriched["날짜"]).dt.days
    daily_enriched["갭퍼센트"] = (daily_enriched["다음날_시가"] - daily_enriched["종가"]) / daily_enriched["종가"] * 100

    return daily_enriched


def clean_gap_universe(daily_enriched: pd.DataFrame) -> pd.DataFrame:
    df = daily_enriched[
        (daily_enriched["날짜차이"] <= 5)
        & (daily_enriched["다음날_시가"] > 0)
        & (daily_enriched["등락률"].notna())
    ].copy()
    df = df[~df["종목명"].str.contains("우$|우B$|1우|2우", regex=True, na=False)]
    df = df[df["종가"] >= 1000]
    df = df[(df["갭퍼센트"] >= -30) & (df["갭퍼센트"] <= 30)]
    return df


# ---------------------------------------------------------------------------
# 4. 갭 상승 전략 백테스트 (일봉 기준)
# ---------------------------------------------------------------------------

def gap_threshold_summary(df_clean: pd.DataFrame, thresholds=(18, 20, 23, 25, 27)) -> pd.DataFrame:
    rows = []
    for th in thresholds:
        subset = df_clean[df_clean["등락률"] >= th]
        total_cum = 1.0
        for r in subset["갭퍼센트"]:
            total_cum *= 1 + r / 100
        rows.append({
            "임계값(%)": th,
            "트레이드수": len(subset),
            "평균수익률(%)": round(subset["갭퍼센트"].mean(), 3),
            "전체기간누적수익률(%)": round((total_cum - 1) * 100, 1),
        })
    return pd.DataFrame(rows)


def gap_strategy_with_daily_filter(df_clean: pd.DataFrame, threshold: float = 23,
                                    volume_ratio_max: float = 5, ma20_gap_min: float = 40) -> pd.DataFrame:
    """전일 '거래량 상대적으로 적음 + MA20 이격도 큼(강한 기존추세)' 필터로 익일 갭 상승 후보를 추린다."""
    events = df_clean[df_clean["등락률"] >= threshold]
    return events[
        (events["거래량_20일평균대비"] < volume_ratio_max)
        & (events["MA20_이격도"] > ma20_gap_min)
    ]


def merge_investor_flow(df_clean: pd.DataFrame, target_cases: pd.DataFrame) -> pd.DataFrame:
    """target_cases의 (종목코드, 날짜)에 대해 기관/개인/외국인 순매수 데이터를 결합한다."""
    investor_data = []
    for ticker in tqdm(target_cases["종목코드"].unique(), desc="투자자 데이터 조회"):
        dates_needed = target_cases[target_cases["종목코드"] == ticker]["날짜"]
        start, end = dates_needed.min().strftime("%Y%m%d"), dates_needed.max().strftime("%Y%m%d")
        try:
            df_inv = stock.get_market_trading_value_by_date(start, end, ticker)
            df_inv = df_inv.reset_index()
            df_inv.columns = ["날짜"] + list(df_inv.columns[1:])
            df_inv["종목코드"] = ticker
            investor_data.append(df_inv)
        except Exception as e:
            print(f"  실패: {ticker} - {e}")

    investor_df = pd.concat(investor_data, ignore_index=True)
    investor_df["날짜"] = pd.to_datetime(investor_df["날짜"])

    investor_filtered = investor_df.merge(target_cases, on=["종목코드", "날짜"], how="inner")
    cols_to_merge = ["종목코드", "날짜", "기관합계", "개인", "외국인", "전체"]
    return df_clean.merge(investor_filtered[cols_to_merge], on=["종목코드", "날짜"], how="left", suffixes=("", "_투자자"))


# ---------------------------------------------------------------------------
# 5. 분봉(시간봉) 데이터 로딩 및 당일 누적등락률
# ---------------------------------------------------------------------------

def load_intraday(path: str) -> tuple[pd.DataFrame, dict]:
    df = pd.read_csv(path)
    df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
    df["cntr_tm"] = pd.to_datetime(df["cntr_tm"], errors="coerce")
    df = df.dropna(subset=["cntr_tm"])
    df["날짜"] = df["cntr_tm"].dt.date
    df = df.sort_values(["종목코드", "cntr_tm"]).reset_index(drop=True)

    daily_close = df.groupby(["종목코드", "날짜"])["cur_prc"].last().reset_index()
    daily_close = daily_close.sort_values(["종목코드", "날짜"])
    daily_close["전일종가"] = daily_close.groupby("종목코드")["cur_prc"].shift(1)
    df = df.merge(daily_close[["종목코드", "날짜", "전일종가"]], on=["종목코드", "날짜"], how="left")
    df["누적등락률"] = (df["cur_prc"] - df["전일종가"]) / df["전일종가"] * 100

    df_sorted = df.sort_values(["종목코드", "날짜", "cntr_tm"]).reset_index(drop=True)
    day_groups = {k: v for k, v in df_sorted.groupby(["종목코드", "날짜"])}
    return df_sorted, day_groups


def first_threshold_hit(df_sorted: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """장중 처음으로 누적등락률이 threshold 이상 도달한 (종목코드, 날짜)별 첫 봉."""
    hit = df_sorted[df_sorted["누적등락률"] >= threshold].copy()
    return hit.groupby(["종목코드", "날짜"]).first().reset_index()


# ---------------------------------------------------------------------------
# 6. 캔들/거래량 패턴 분류
# ---------------------------------------------------------------------------

def classify_candle_shape(o: float, h: float, l: float, c: float) -> str:
    range_ = h - l
    if range_ == 0:
        return "변동없음"
    body = c - o
    body_ratio = abs(body) / range_
    upper_wick = (h - max(o, c)) / range_
    lower_wick = (min(o, c) - l) / range_

    if body_ratio < 0.1:
        return "도지"
    if body > 0 and body_ratio >= 0.6:
        return "강한양봉"
    if body > 0 and lower_wick >= 0.4:
        return "아랫꼬리양봉"
    if body > 0:
        return "약한양봉"
    if body < 0 and body_ratio >= 0.6:
        return "강한음봉"
    if body < 0 and upper_wick >= 0.4:
        return "윗꼬리음봉"
    return "약한음봉"


def classify_volume_level(current_vol: float, recent_avg_vol: float) -> str:
    if recent_avg_vol == 0 or pd.isna(recent_avg_vol):
        return "평범"
    ratio = current_vol / recent_avg_vol
    if ratio >= 2.0:
        return "거래량폭증"
    if ratio >= 1.3:
        return "거래량증가"
    if ratio <= 0.5:
        return "거래량급감"
    return "거래량평범"


def build_combo_labels(bars: pd.DataFrame) -> list[str]:
    labels = []
    for i in range(len(bars)):
        row = bars.iloc[i]
        shape = classify_candle_shape(row["open_pric"], row["high_pric"], row["low_pric"], row["cur_prc"])
        recent_avg = bars["trde_qty"].iloc[:1].mean() if i == 0 else bars["trde_qty"].iloc[max(0, i - 3):i].mean()
        vol_level = classify_volume_level(row["trde_qty"], recent_avg)
        labels.append(f"{shape}_{vol_level}")
    return labels


def combo_features(bars: pd.DataFrame) -> dict:
    labels = build_combo_labels(bars)
    total = len(labels)
    if total == 0:
        return {}
    return {
        "강한양봉_거래량폭증_횟수": labels.count("강한양봉_거래량폭증"),
        "강한음봉_거래량폭증_횟수": labels.count("강한음봉_거래량폭증"),
        "도지_거래량급감_횟수": labels.count("도지_거래량급감"),
        "아랫꼬리양봉_거래량폭증_횟수": labels.count("아랫꼬리양봉_거래량폭증"),
        "윗꼬리음봉_거래량폭증_횟수": labels.count("윗꼬리음봉_거래량폭증"),
        "강한양봉_거래량폭증_비율": labels.count("강한양봉_거래량폭증") / total,
        "강한음봉_거래량폭증_비율": labels.count("강한음봉_거래량폭증") / total,
    }


# ---------------------------------------------------------------------------
# 7. 오전 구간 피처 엔지니어링 (임계값 도달 이벤트 기준)
# ---------------------------------------------------------------------------

def morning_features(day_groups: dict, ticker: str, date, event_time, t_price: float) -> dict | None:
    day_data = day_groups.get((ticker, date))
    if day_data is None:
        return None
    morning = day_data[day_data["cntr_tm"].dt.hour < 14].reset_index(drop=True)
    if len(morning) == 0:
        return None

    day_open = day_data["open_pric"].iloc[0]
    net_change = t_price - day_open
    total_range = (morning["high_pric"] - morning["low_pric"]).sum()
    efficiency = net_change / total_range if total_range > 0 else 0

    volumes = morning["trde_qty"].values
    vol_slope = np.polyfit(np.arange(len(volumes)), volumes, 1)[0] if len(volumes) >= 2 else 0

    is_bullish = morning["cur_prc"] > morning["open_pric"]
    bullish_ratio = is_bullish.mean()

    range_safe = (morning["high_pric"] - morning["low_pric"]).replace(0, np.nan)
    close_pos = (morning["cur_prc"] - morning["low_pric"]) / range_safe
    avg_close_pos = close_pos.mean()

    cummax = morning["high_pric"].cummax()
    drawdown = ((cummax - morning["low_pric"]) / cummax * 100).max()

    body = (morning["cur_prc"] - morning["open_pric"]).abs()
    body_ratio_series = body / range_safe
    avg_body_ratio = body_ratio_series.mean()

    upper_wick = morning["high_pric"] - morning[["open_pric", "cur_prc"]].max(axis=1)
    lower_wick = morning[["open_pric", "cur_prc"]].min(axis=1) - morning["low_pric"]
    avg_upper_wick_ratio = (upper_wick / range_safe).mean()
    avg_lower_wick_ratio = (lower_wick / range_safe).mean()
    doji_ratio = (body_ratio_series < 0.1).mean()

    max_consec_bull = 0
    cur_streak = 0
    for b in is_bullish:
        if b:
            cur_streak += 1
            max_consec_bull = max(max_consec_bull, cur_streak)
        else:
            cur_streak = 0

    before_reach = morning[morning["cntr_tm"] < event_time]
    after_reach = morning[morning["cntr_tm"] >= event_time]
    vol_before = before_reach["trde_qty"].mean() if len(before_reach) > 0 else np.nan
    vol_after = after_reach["trde_qty"].mean() if len(after_reach) > 0 else np.nan
    vol_persist_ratio = vol_after / vol_before if (vol_before and vol_before > 0) else np.nan

    down_bars = morning[~is_bullish]
    up_bars = morning[is_bullish]
    down_vol_avg = down_bars["trde_qty"].mean() if len(down_bars) > 0 else np.nan
    up_vol_avg = up_bars["trde_qty"].mean() if len(up_bars) > 0 else np.nan
    down_vol_ratio = down_vol_avg / (up_vol_avg + 1e-6) if not np.isnan(down_vol_avg) else np.nan

    typical_price = (morning["high_pric"] + morning["low_pric"] + morning["cur_prc"]) / 3
    vwap = (typical_price * morning["trde_qty"]).sum() / morning["trde_qty"].sum()
    vwap_gap = (t_price - vwap) / vwap * 100

    feat = {
        "오전효율성": efficiency, "오전거래량기울기": vol_slope, "오전양봉비율": bullish_ratio,
        "오전평균마감위치": avg_close_pos, "오전최대되돌림": drawdown,
        "오전평균몸통비율": avg_body_ratio, "오전평균윗꼬리비율": avg_upper_wick_ratio,
        "오전평균아랫꼬리비율": avg_lower_wick_ratio, "오전도지비율": doji_ratio,
        "오전최대연속양봉": max_consec_bull, "도달시각": event_time.hour,
        "거래량유지비율": vol_persist_ratio, "하락시거래량비율": down_vol_ratio,
        "VWAP대비이격도": vwap_gap,
    }
    feat.update(combo_features(morning))
    return feat


# ---------------------------------------------------------------------------
# 8. 이벤트 이후 궤적(trajectory) 분석
# ---------------------------------------------------------------------------

def extract_trajectory(day_groups: dict, ticker: str, date, event_time) -> dict | None:
    day_data = day_groups.get((ticker, date))
    if day_data is None:
        return None
    day_data = day_data.sort_values("cntr_tm").reset_index(drop=True)
    pre = day_data[day_data["cntr_tm"] < event_time].reset_index(drop=True)
    event_bar = day_data[day_data["cntr_tm"] == event_time]
    post = day_data[day_data["cntr_tm"] > event_time].reset_index(drop=True)
    if len(event_bar) == 0:
        return None
    return {"pre": pre, "event": event_bar.iloc[0], "post": post}


def summarize_trajectory(pre: pd.DataFrame, event: pd.Series, post: pd.DataFrame) -> dict:
    result: dict = {}

    if len(pre) > 0:
        day_open = pre["open_pric"].iloc[0]
        net_change_pre = event["cur_prc"] - day_open
        total_range_pre = (pre["high_pric"] - pre["low_pric"]).sum()
        result["이전_효율성"] = net_change_pre / total_range_pre if total_range_pre > 0 else 0
        result["이전_양봉비율"] = (pre["cur_prc"] > pre["open_pric"]).mean()
        range_safe_pre = (pre["high_pric"] - pre["low_pric"]).replace(0, np.nan)
        result["이전_평균마감위치"] = ((pre["cur_prc"] - pre["low_pric"]) / range_safe_pre).mean()
        vols_pre = pre["trde_qty"].values
        result["이전_거래량기울기"] = np.polyfit(np.arange(len(vols_pre)), vols_pre, 1)[0] if len(vols_pre) >= 2 else 0
    else:
        result.update({"이전_효율성": 0, "이전_양봉비율": np.nan, "이전_평균마감위치": np.nan, "이전_거래량기울기": 0})

    if len(post) > 0:
        event_price = event["cur_prc"]
        cummax_post = post["high_pric"].cummax()
        result["이후_최대되돌림"] = ((cummax_post - post["low_pric"]) / cummax_post * 100).max()
        is_bull_post = post["cur_prc"] > post["open_pric"]
        result["이후_양봉비율"] = is_bull_post.mean()
        first_bear_idx = next((i for i, b in enumerate(is_bull_post) if not b), len(post))
        result["이후_첫음봉까지봉수"] = first_bear_idx
        result["이후_최종수익률"] = (post["cur_prc"].iloc[-1] - event_price) / event_price * 100
        vols_post = post["trde_qty"].values
        result["이후_거래량기울기"] = np.polyfit(np.arange(len(vols_post)), vols_post, 1)[0] if len(vols_post) >= 2 else 0
    else:
        result.update({"이후_최대되돌림": np.nan, "이후_양봉비율": np.nan, "이후_첫음봉까지봉수": np.nan,
                        "이후_최종수익률": np.nan, "이후_거래량기울기": np.nan})

    return result


def build_trajectory_df(day_groups: dict, events: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in events.iterrows():
        traj = extract_trajectory(day_groups, row["종목코드"], row["날짜"], row["cntr_tm"])
        if traj is None or len(traj["post"]) == 0:
            continue
        feat = summarize_trajectory(traj["pre"], traj["event"], traj["post"])
        feat["종목코드"] = row["종목코드"]
        feat["날짜"] = row["날짜"]
        rows.append(feat)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 9. 매매 규칙 시뮬레이션
# ---------------------------------------------------------------------------

def rolling_stop_rule(day_groups: dict, ticker: str, date, event_time, event_price: float) -> float | None:
    """이벤트 발생 이후 첫 음봉이 나오는 즉시 그 종가로 손절, 끝까지 양봉이면 마지막 가격으로 청산."""
    day_data = day_groups.get((ticker, date))
    if day_data is None:
        return None
    post = day_data[day_data["cntr_tm"] > event_time].reset_index(drop=True)
    if len(post) == 0:
        return None
    for _, bar in post.iterrows():
        if bar["cur_prc"] < bar["open_pric"]:
            return (bar["cur_prc"] - event_price) / event_price * 100
    return (post["cur_prc"].iloc[-1] - event_price) / event_price * 100


def pullback_entry_return(day_groups: dict, ticker: str, date, event_time) -> float | None:
    """조정(첫 음봉) 확인 다음 봉의 시가에 진입해, 당일 종가까지 보유했을 때의 수익률."""
    day_data = day_groups.get((ticker, date))
    if day_data is None:
        return None
    post = day_data[day_data["cntr_tm"] > event_time].reset_index(drop=True)
    if len(post) < 2:
        return None

    entry_price = None
    for i in range(len(post) - 1):
        bar = post.iloc[i]
        if bar["cur_prc"] < bar["open_pric"]:
            entry_price = post.iloc[i + 1]["open_pric"]
            break
    if entry_price is None:
        return None

    final_price = day_data["cur_prc"].iloc[-1]
    return (final_price - entry_price) / entry_price * 100


# ---------------------------------------------------------------------------
# 10. 세이프존(하한선 유지) 전략
# ---------------------------------------------------------------------------

def build_safezone_analysis(df_sorted: pd.DataFrame, day_groups: dict, threshold: float, floor: float) -> pd.DataFrame:
    """threshold(%) 도달 후 14시까지 유지된 종목이, 15시 종가까지 floor(%) 이상을 지켰는지 검증."""
    hit = df_sorted[df_sorted["누적등락률"] >= threshold].copy()
    first_hit = hit.groupby(["종목코드", "날짜"]).first().reset_index()

    records = []
    for _, row in first_hit.iterrows():
        ticker, date, event_time = row["종목코드"], row["날짜"], row["cntr_tm"]
        day_data = day_groups.get((ticker, date))
        if day_data is None:
            continue
        bar_1400 = day_data[day_data["cntr_tm"].dt.hour == 14]
        bar_1500 = day_data[day_data["cntr_tm"].dt.hour == 15]
        if len(bar_1400) == 0 or len(bar_1500) == 0:
            continue

        t1400_price = bar_1400.iloc[0]["cur_prc"]
        t1400_return = (t1400_price - row["전일종가"]) / row["전일종가"] * 100
        if t1400_return < threshold:
            continue

        close_1500 = bar_1500.iloc[0]["cur_prc"]
        daily_return_1500 = (close_1500 - row["전일종가"]) / row["전일종가"] * 100
        target_return = (close_1500 - t1400_price) / t1400_price * 100

        feat = morning_features(day_groups, ticker, date, event_time, t1400_price)
        if feat is None:
            continue
        feat.update({
            "종목코드": ticker, "날짜": date, "T시점가격": t1400_price, "전일종가": row["전일종가"],
            "당일종가등락률": daily_return_1500, "타겟수익률": target_return,
            "세이프존성공": int(daily_return_1500 >= floor),
        })
        records.append(feat)

    return pd.DataFrame(records)


def apply_floor_stop(row: pd.Series, floor_return: float) -> float:
    if row["세이프존성공"] == 1:
        return row["타겟수익률"]
    entry_price = row["T시점가격"]
    floor_price = row["전일종가"] * (1 + floor_return / 100)
    return (floor_price - entry_price) / entry_price * 100


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="키움 데이터 수집/병합 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="분봉 데이터 수집")
    p_collect.add_argument("--interval", required=True, help="분봉 주기 (예: 15, 60)")
    p_collect.add_argument("--data-dir", required=True, help="종목별 CSV 저장 경로")
    p_collect.add_argument("--max-workers", type=int, default=MAX_WORKERS)

    p_merge = sub.add_parser("merge", help="종목별 CSV를 하나로 병합")
    p_merge.add_argument("--data-dir", required=True)
    p_merge.add_argument("--output-csv", required=True)
    p_merge.add_argument("--output-parquet", default=None)

    args = parser.parse_args()
    if args.command == "collect":
        run_collection(args.interval, args.data_dir, args.max_workers)
    elif args.command == "merge":
        merge_csv_files(args.data_dir, args.output_csv, args.output_parquet)


if __name__ == "__main__":
    main()
