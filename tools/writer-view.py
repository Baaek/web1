#!/usr/bin/env python3
"""
집필용 보기 — 작가 전용 내용을 걷어낸 worldbuilding 사본을 만든다.

  python3 tools/writer-view.py              # .writer-view/ 에 생성
  python3 tools/writer-view.py --report     # 생성 + 걷어내지 못한 메타 용어 보고
  python3 tools/writer-view.py --out DIR

하는 일
  1. <!-- 작가용:시작 --> … <!-- 작가용:끝 --> 블록을 지운다
  2. 21-기원-작가전용.md 와 공통/ 스냅샷을 아예 빼고 만든다
  3. 빠진 문서로 가는 링크는 링크를 풀고 〔작가 전용〕 표시만 남긴다

집필 세션은 원본 worldbuilding/ 대신 이 사본을 읽는다(→ CLAUDE.md 5절).
사본은 git에 올리지 않는다 — 필요할 때마다 다시 만든다.
"""
import os, re, sys, shutil, importlib.util

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(ROOT)
WB   = os.path.join(REPO, 'worldbuilding')

AUTHOR_BLOCK = re.compile(r'<!-- 작가용:시작 -->.*?<!-- 작가용:끝 -->', re.S)
EXCLUDE_FILES = {'21-기원-작가전용.md'}
EXCLUDE_DIRS  = {'공통'}
HIDDEN_TARGETS = ('21-기원-작가전용.md', '공통/')

def out_dir():
    if '--out' in sys.argv:
        return os.path.abspath(sys.argv[sys.argv.index('--out') + 1])
    return os.path.join(REPO, '.writer-view')

def unlink_hidden(text):
    """작가 전용 문서로 가는 링크를 풀고 표시만 남긴다."""
    def r(m):
        target = m.group(2)
        if any(h in target for h in HIDDEN_TARGETS):
            return '〔작가 전용〕'
        return m.group(0)
    return re.sub(r'\[([^\]]*)\]\(([^)]*)\)', r, text)

def build(dst):
    if os.path.exists(dst): shutil.rmtree(dst)
    n_files = n_blocks = 0
    for root, dirs, files in os.walk(WB):
        rel_root = os.path.relpath(root, WB)
        top = rel_root.split(os.sep)[0]
        if top in EXCLUDE_DIRS or '.git' in root: continue
        for f in files:
            if f in EXCLUDE_FILES: continue
            src = os.path.join(root, f)
            dst_f = os.path.join(dst, 'worldbuilding', rel_root, f)
            os.makedirs(os.path.dirname(dst_f), exist_ok=True)
            if not f.endswith('.md'):
                shutil.copy2(src, dst_f); continue
            t = open(src, encoding='utf-8').read()
            n_blocks += len(AUTHOR_BLOCK.findall(t))
            t = AUTHOR_BLOCK.sub('', t)
            t = unlink_hidden(t)
            open(dst_f, 'w', encoding='utf-8').write(t)
            n_files += 1
    open(os.path.join(dst, 'README.md'), 'w', encoding='utf-8').write(
        '# 집필용 보기\n\n`tools/writer-view.py`가 만든 사본이다. **직접 고치지 않는다** — '
        '원본 `worldbuilding/`을 고친 뒤 다시 만든다.\n\n'
        '작가 블록과 21번(세계의 기원)이 빠져 있다.\n')
    return n_files, n_blocks

def residual(dst):
    """사본에 남은 메타 용어 — 아직 작가 블록으로 감싸지 않은 곳."""
    spec = importlib.util.spec_from_file_location('cc', os.path.join(ROOT, 'canon-check.py'))
    cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
    terms = cc.BASE_TERMS + cc.origin_terms()
    hits = {}
    base = os.path.join(dst, 'worldbuilding')
    for root, _, files in os.walk(base):
        for f in files:
            if not f.endswith('.md'): continue
            p = os.path.join(root, f)
            for i, line in enumerate(open(p, encoding='utf-8'), 1):
                clean = cc.strip_noise(line)
                for s in cc.TITLE_SAFE: clean = clean.replace(s, '')
                for t in terms:
                    if t in clean:
                        hits.setdefault(os.path.relpath(p, base), []).append((i, t)); break
    return hits

def main():
    dst = out_dir()
    n, b = build(dst)
    print(f'집필용 보기 → {dst}')
    print(f'  문서 {n}개 · 작가 블록 {b}개 제거 · 21번·공통/ 제외')
    if '--report' in sys.argv:
        hits = residual(dst)
        total = sum(len(v) for v in hits.values())
        print(f'\n사본에 남은 메타 용어: {total}줄 / {len(hits)}문서')
        for k, v in sorted(hits.items(), key=lambda x: -len(x[1])):
            print(f'  {len(v):4d}  {k}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
