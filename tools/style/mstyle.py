import re,sys,statistics as st
def paras(t):
    ps=[p.strip() for p in re.split(r'\n\s*\n|\n',t) if p.strip()]
    return [p for p in ps if not re.match(r'^(=+|\* \* \*|外傳|#|---|\*\*外傳)',p) and not p.startswith('삐이')]
def sents(p):
    return [s for s in re.split(r'(?<=[.?!…])\s+',p) if s.strip()]
def nos(s): return len(re.sub(r'\s|\*','',s))
def m(t,label):
    ps=paras(t)
    narr=[p for p in ps if p[0] not in '“"‘\'']
    dlg=[p for p in ps if p[0] in '“"']
    pl=[nos(p) for p in narr]; ss=[nos(s) for p in narr for s in sents(p)]
    one=sum(1 for p in narr if len(sents(p))==1)
    tot=sum(nos(p) for p in ps)
    print(f"{label}: 서술문단 {len(narr)} 평균 {st.mean(pl):.1f} 중앙 {st.median(pl)} | 문장 평균 {st.mean(ss):.1f} 중앙 {st.median(ss)} | 1문장문단 {one/len(narr)*100:.0f}% | 대사비 {sum(nos(p) for p in dlg)/tot*100:.0f}% | 문단당문장 {len(ss)/len(narr):.2f} | 100자+문단 {sum(1 for x in pl if x>=100)/len(pl)*100:.0f}%")
t=open(sys.argv[1],encoding='utf-8').read()
if len(sys.argv)>2:
    t=t.split('\n---\n',1)[1]; m(t,'mine'); sys.exit()
parts=re.split(r'\n(?:=+)?(外傳 [一二三四五六七八九十]+)\s*\n',t)
m(t,'전체')
i=1
while i<len(parts):
    m(parts[i+1],parts[i]); i+=2
