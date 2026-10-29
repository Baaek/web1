import sys, re, statistics

OPEN_D = '“'; CLOSE_D = '”'
OPEN_S = '‘'; CLOSE_S = '’'

for path in sys.argv[1:]:
    raw = open(path, encoding='utf-8').read()
    # 본문만: 첫 '---' 구분선 이후
    body = raw.split('\n---\n', 1)[-1]
    lines = [l.strip() for l in body.split('\n')]
    paras = [l for l in lines if l and l != '---']
    # 절 번호 표시(**1**) 제외
    paras = [p for p in paras if not re.fullmatch(r'\*+\d+\*+', p)]

    def strip_md(s):
        return re.sub(r'\*+', '', s).strip()

    plain = [strip_md(p) for p in paras]
    nospace = sum(len(re.sub(r'\s', '', p)) for p in plain)

    def is_dialog(p):
        return p[:1] in (OPEN_D, '"')
    def is_inner(p):
        return p[:1] in (OPEN_S, "'")

    d = [p for p in plain if is_dialog(p)]
    i = [p for p in plain if is_inner(p)]
    narr = [p for p in plain if not is_dialog(p) and not is_inner(p)]

    dc = sum(len(re.sub(r'\s','',p)) for p in d)
    ic = sum(len(re.sub(r'\s','',p)) for p in i)

    print(f'=== {path.split("/")[-1]}')
    print(f'공백제외 {nospace}자   문단 {len(paras)}   평균 {nospace/len(paras):.1f}자')
    print(f'대사 {dc/nospace*100:.1f}%  속말 {ic/nospace*100:.1f}%  (대사문단 {len(d)} / 속말문단 {len(i)})')
    lens = [len(re.sub(r'\s','',p)) for p in narr]
    print(f'서술문단 중앙 {statistics.median(lens):.0f}자  최장 {max(lens)}자')
    print()
