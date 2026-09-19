#!/usr/bin/env python3
"""
캐논 검증기 — 「요르문간드 연대기」

  python3 tools/canon-check.py            # 전체 검사
  python3 tools/canon-check.py --verbose  # 통과 항목도 출력

검사 항목
  1. 링크 무결성      상대링크가 실제 파일을 가리키는가
  2. 메타 진실 누출   라그나로크·아우터 플레인 등이 인물 입에 올랐는가
  3. 등급·RSU 정합    19번 특성 표의 등급이 RSU 구간과 맞는가
  4. 요르문간드 금지  기원 목록에 요르문간드가 올라갔는가

종료 코드 0 = 통과, 1 = 위반 있음.
"""
import os, re, sys, json, urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(ROOT)
WB   = os.path.join(REPO, 'worldbuilding')
NOVEL= os.path.join(REPO, 'novel')

VERBOSE = '--verbose' in sys.argv

# ── 메타 진실 기본 금칙어 ───────────────────────────────────────────────
# 등장인물이 도달할 수 있는 최대치는 "질량을 가진 무언가가 있고 셀 수 있는
# 개수의 덩어리다"뿐이다(→ CLAUDE.md 1절).
BASE_TERMS = [
    '라그나로크', '아우터 플레인', '요르문간드', '다중우주',
    '신들의 사체', '기생차원', '신들의 정신 파편',
]

# 작품 제목은 금칙어가 아니다 — 이 표현 안의 「요르문간드」는 넘어간다.
TITLE_SAFE = ['요르문간드 연대기']

# ── 엄격도 ─────────────────────────────────────────────────────────────
#   strict : 서술자 포함 전면 금지 — 독자가 읽는 글
#   quoted : 인용·대사 안에서만 금지 — 작가만 보는 자료
#   free   : 검사하지 않음 — 정본과 작가 블록
STRICT_DIRS = ['novel']
# 분리한 물리 문서 — 작가 블록 밖에 기원 용어가 남으면 분리가 무너진 것이다
STRICT_FILES = ['worldbuilding/20-세계의물리.md']
QUOTED_DIRS = ['worldbuilding/events', 'worldbuilding/countries',
               'worldbuilding/characters', 'worldbuilding/monthly']

AUTHOR_OPEN, AUTHOR_CLOSE = '<!-- 작가용:시작 -->', '<!-- 작가용:끝 -->'
ALLOW_MARK = '<!-- 메타허용'          # 이 주석이 붙은 줄과 바로 다음 줄은 건너뛴다

problems, checked = [], {}

def add(kind, path, line, msg):
    problems.append((kind, os.path.relpath(path, REPO), line, msg))

def md_files(base, skip_common=True):
    for root, dirs, files in os.walk(base):
        if '.git' in root or 'node_modules' in root: continue
        if skip_common and os.path.relpath(root, WB).startswith('공통'): continue
        for f in sorted(files):
            if f.endswith('.md'): yield os.path.join(root, f)

def strip_noise(text):
    """링크 대상·인라인 코드·HTML 주석을 지운다 — 파일명 오탐 제거."""
    text = re.sub(r'\]\([^)]*\)', '](#)', text)   # 링크 대상
    text = re.sub(r'`[^`\n]*`', '``', text)       # 인라인 코드
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    return text

QUOTE_RE = re.compile(r'"[^"\n]*"|“[^”\n]*”|\'[^\'\n]*\'|‘[^’\n]*’|「[^」\n]*」|《[^》\n]*》')

def quoted_spans(line):
    return [m.group(0) for m in QUOTE_RE.finditer(line)]

# ── 1. 링크 무결성 ─────────────────────────────────────────────────────
def check_links():
    n = 0
    for p in md_files(WB):
        d = os.path.dirname(p)
        for i, line in enumerate(open(p, encoding='utf-8'), 1):
            for m in re.finditer(r'\]\((\.{1,2}/[^)#]+?)(?:#[^)]*)?\)', line):
                t = urllib.parse.unquote(m.group(1)); n += 1
                if not os.path.exists(os.path.normpath(os.path.join(d, t))):
                    add('링크', p, i, f'대상 없음: {t}')
    checked['링크'] = n

# ── 2. 메타 진실 누출 ──────────────────────────────────────────────────
def origin_rows(path):
    """「기원」 열을 가진 표의 데이터 행만 골라 (행번호, 기원칸)을 내놓는다.
    표 헤더를 보고 판정하므로 다른 표의 마지막 칸을 잘못 긁지 않는다."""
    col = None
    for i, line in enumerate(open(path, encoding='utf-8'), 1):
        if not line.lstrip().startswith('|'):
            col = None; continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        # 헤더 칸이 정확히 「기원」일 때만 잡는다 — "기원을 아는가" 같은 칸은 제외
        hdr = [k for k, c in enumerate(cells)
               if re.fullmatch(r'[⚠️\s*]*기원[\s*]*', c)]
        if hdr:
            col = hdr[0]
            continue                       # 헤더 행 자체는 건너뛴다
        if col is None: continue
        if all(set(c) <= set('-: ') for c in cells): continue   # 구분선
        if col < len(cells): yield i, cells[col]

def origin_terms():
    """19번 「기원」 열에서 신 이름을 자동 수집한다.
    새 특성을 추가해도 금칙어 목록이 저절로 늘어난다."""
    f = os.path.join(WB, '19-특성과권능.md')
    if not os.path.exists(f): return []
    out = set()
    for _, cell in origin_rows(f):
        cell = re.sub(r'\*|\(.*?\)|（.*?）|\[.*?\]', '', cell).strip()
        if cell and re.match(r'^[가-힣A-Za-z· ]{2,20}$', cell):
            out.add(cell)
    return sorted(out)

def check_meta():
    terms = BASE_TERMS + origin_terms()
    if VERBOSE:
        print(f"  금칙어 {len(terms)}개 (기본 {len(BASE_TERMS)} + 기원 자동수집 {len(terms)-len(BASE_TERMS)})")
    n = 0
    targets = [(os.path.join(REPO, d), 'strict') for d in STRICT_DIRS] + \
              [(os.path.join(REPO, d), 'quoted') for d in QUOTED_DIRS]
    files = [(os.path.join(REPO, f), 'strict') for f in STRICT_FILES]
    for base, mode in targets:
        if not os.path.isdir(base): continue
        files += [(q, mode) for q in md_files(base, skip_common=False)]
    for p, mode in files:
        if os.path.exists(p):
            raw = open(p, encoding='utf-8').read()
            # 작가 블록은 빈 줄로 바꾼다 — 지우면 줄 번호가 밀린다
            body = re.sub(re.escape(AUTHOR_OPEN)+r'.*?'+re.escape(AUTHOR_CLOSE),
                          lambda m: '\n' * m.group(0).count('\n'), raw, flags=re.S)
            lines = body.split('\n')
            for i, line in enumerate(lines, 1):
                # 허용 마크는 자기 줄과 바로 다음 줄에 적용된다
                if ALLOW_MARK in line: continue
                if i >= 2 and ALLOW_MARK in lines[i-2]: continue
                clean = strip_noise(line)
                for safe in TITLE_SAFE:
                    clean = clean.replace(safe, '')
                if not clean.strip(): continue
                n += 1
                scan = clean if mode == 'strict' else ' '.join(quoted_spans(clean))
                for t in terms:
                    if t in scan:
                        where = '서술자 포함 전면 금지' if mode=='strict' else '인물 발화·인용'
                        add('메타', p, i, f'「{t}」 — {where} 구역')
                        break
    checked['메타'] = n

# ── 3. 등급·RSU 정합 ───────────────────────────────────────────────────
BANDS = {'C': (0, 10), 'B': (10, 100), 'A': (100, 1000), 'S': (1000, None)}

def check_rsu():
    f = os.path.join(WB, '19-특성과권능.md')
    if not os.path.exists(f): return
    n = 0
    for i, line in enumerate(open(f, encoding='utf-8'), 1):
        for g, v in re.findall(r'\|\s*\*{0,2}([CBAS])\*{0,2}\s*·\s*\*{0,2}([\d,~ +]+)', line):
            n += 1
            if '~' in v: continue          # 범위형은 중앙값 기준이라 건너뛴다
            num = int(v.replace(',', '').replace('+', '').strip())
            lo, hi = BANDS[g]
            ok = num > lo and (hi is None or num <= hi)
            if not ok:
                add('RSU', f, i, f'{g}등급인데 {num} RSU — 구간 밖')
    checked['RSU'] = n

# ── 4. 요르문간드 금지 ─────────────────────────────────────────────────
def check_jormungand():
    """요르문간드는 신이 아니라 시공간 그 자체다. 기원으로 올리면 메타 진실이 샌다."""
    f = os.path.join(WB, '19-특성과권능.md')
    if not os.path.exists(f): return
    for i, cell in origin_rows(f):
        if '요르문간드' in cell:
            add('금지', f, i, '기원 열에 「요르문간드」 — 신이 아니라 시공간 그 자체다')

# ── 실행 ───────────────────────────────────────────────────────────────
def main():
    print('캐논 검증기 — 「요르문간드 연대기」\n')
    for fn in (check_links, check_meta, check_rsu, check_jormungand):
        fn()
    for k, v in checked.items():
        print(f'  {k:4s} 검사 대상 {v}건')
    print()
    if not problems:
        print('✅ 위반 0건')
        return 0
    by = {}
    for kind, path, line, msg in problems:
        by.setdefault(kind, []).append((path, line, msg))
    for kind, items in by.items():
        print(f'❌ [{kind}] {len(items)}건')
        for path, line, msg in items[:40]:
            print(f'   {path}:{line}  {msg}')
        if len(items) > 40:
            print(f'   … 외 {len(items)-40}건')
        print()
    return 1

if __name__ == '__main__':
    sys.exit(main())
