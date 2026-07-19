# web1 — 국내주식 데이터 수집 & 백테스트 연구

키움증권 Open API(`kiwoomcli`)와 `pykrx`를 이용해 코스피/코스닥 시세 데이터를 수집하고,
갭 상승·하락, 캔들 패턴, 추격매수 등 다양한 단기 트레이딩 전략을 백테스트/분석하는
개인 퀀트 리서치 프로젝트입니다.

## 구성 파일

| 파일 | 내용 |
| --- | --- |
| `collect_data.ipynb` | `kiwoomcli`로 15분봉 데이터를 종목별로 병렬 수집 |
| `kiwoom1.ipynb` | 시간봉(1시간) 데이터 수집 및 CSV 병합, 캔들 패턴("캔들해부학")·세이프존·궤적 분석 |
| `backtest.ipynb` | 일봉 데이터 기반 갭 상승/하락 백테스트, 임계값별 승률·수익률·누적수익률 분석, 수급(기관/개인/외국인) 데이터 결합 |
| `추가분석.ipynb` | RSI/MACD 등 기술적 지표 계산, 장중 추격매수 전략에 대한 가설 검증 및 추가 분석 |
| `imagee.jpg` | 분석 참고용 캡처 이미지 |

## 데이터 흐름

1. **수집**: `collect_data.ipynb` / `kiwoom1.ipynb`에서 `pykrx`로 전체 종목 리스트를 가져오고,
   `kiwoomcli`(키움 Open API CLI)로 종목별 분봉/시간봉 시세를 병렬(`ThreadPoolExecutor`)로 수집해
   `data_dir` 하위에 종목코드별 CSV로 저장합니다.
2. **병합**: 종목별 CSV를 하나로 병합해 `merged_*.csv` / `.parquet` 형태로 저장합니다.
3. **백테스트/분석**: `backtest.ipynb`, `추가분석.ipynb`에서 병합 데이터를 로드해
   전략 가설(임계값별 갭 전략, 거래량·이격도 필터, RSI/MACD 등)을 검증합니다.

## 사전 준비

- Python 3.x
- 키움증권 Open API 인증 및 `kiwoomcli` CLI 설치·로그인 (`kiwoomcli auth refresh`로 토큰 갱신)
- `.env` 파일에 키움 API 관련 인증 정보 설정 (`python-dotenv`로 로드)

### 주요 의존 라이브러리

```
pandas
numpy
pykrx
python-dotenv
tqdm
matplotlib
ta
```

```bash
pip install pandas numpy pykrx python-dotenv tqdm matplotlib ta
```

## 사용 방법

1. `.env` 파일에 키움 Open API 인증 정보를 설정합니다.
2. `collect_data.ipynb` 또는 `kiwoom1.ipynb`를 실행해 원하는 주기(15분봉/시간봉)의 시세 데이터를 수집합니다.
3. 수집된 CSV를 병합해 `merged_*.parquet` 파일을 생성합니다.
4. `backtest.ipynb`, `추가분석.ipynb`를 열어 전략 백테스트 및 지표 분석을 진행합니다.

## 참고

- 한글 폰트 깨짐 방지를 위해 `matplotlib`의 `font.family`를 환경에 맞는 한글 폰트(예: `Malgun Gothic`, macOS/Linux는 `AppleGothic`, `NanumGothic` 등)로 설정해야 합니다.
- API 호출량이 많으므로 요청 간 딜레이(`REQUEST_DELAY`)와 토큰 재발급 주기(`REFRESH_EVERY_N`)를 상황에 맞게 조정하세요.
