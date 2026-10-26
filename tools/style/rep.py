import sys,json
p=sys.argv[1]; R=json.load(open(sys.argv[2],encoding='utf-8'))
t=open(p,encoding='utf-8').read()
for a,b in R:
    c=t.count(a)
    if c!=1: print('MISS',c,a[:40]); continue
    t=t.replace(a,b)
open(p,'w',encoding='utf-8').write(t)
