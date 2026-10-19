import random, math, html, sys
random.seed(7)
E=html.escape
D=[1,2,3,4,5,6,8,10,12,15,20,25,30]
LAYERS=['M1','M2','ITO']
METRICS=['union','lateral_only','vertical_only','simultaneous']
# fake fractions
def frac(layer,d,side):
    base={'M1':0.06,'M2':0.09,'ITO':0.04}[layer]
    k={'a':1.0,'b':{'M1':1.08,'M2':0.93,'ITO':1.15}[layer]}[side]
    return base*k*(1-math.exp(-d/6))*(1+0.02*math.sin(d))
fr={(l,d,s):frac(l,d,s) for l in LAYERS for d in D for s in 'ab'}
# fake line/pixel
lp={('line','a'):412.0,('line','b'):451.0,('pixel','a'):238.0,('pixel','b'):221.0,('transfer','a'):31.0,('transfer','b'):29.0}
pieces={('lateral_only','a'):180,('lateral_only','b'):205,('vertical_only','a'):122,('vertical_only','b'):109,('simultaneous','a'):58,('simultaneous','b'):66}
# pairs
pairs=[]
for i in range(1,27):
    a=round(random.uniform(20,140),1)
    dd=round(random.gauss(0,18),1)
    if i in (5,17): dd=0.0
    st='available' if i not in (9,22) else 'partial'
    pairs.append(dict(id=f'P{i:02d}',layer=random.choice(LAYERS),cx=round(random.uniform(100,900),1),cy=round(random.uniform(100,600),1),a=a,b=round(max(0,a+dd),1),st=st,
       ftype=random.choice(['lateral_short','vertical_short']),pieces=[round(random.uniform(0,1),2) for _ in range(3)]))
for p in pairs: p['d']=round(p['b']-p['a'],1)
blocked=[('P27','asset_manifest_missing'),('P28','center_outside_frame_b')]
ok=[p for p in pairs if p['st']=='available']
worse=sorted([p for p in ok if p['d']>0],key=lambda p:-p['d'])[:10]
better=sorted([p for p in ok if p['d']<0],key=lambda p:p['d'])[:10]
zero=[p for p in ok if p['d']==0]
partial=[p for p in pairs if p['st']!='available']
W=400  # window 20um, area 400um2

C={'a':'#4e79a7','b':'#e15759','delta':'#59a14f'}
def fmt(v,n=1): return f'{v:+.{n}f}' if isinstance(v,float) and n<0 else f'{v:.{n}f}'
def sgn(v,n=1): return ('+' if v>0 else '')+f'{v:.{n}f}'

def line_chart(layer):
    w,h,pl,pb=300,170,40,26
    ys=[fr[(layer,d,s)] for d in D for s in 'ab']; ymax=max(ys)*1.1
    X=lambda d: pl+(w-pl-10)*(d-1)/29; Y=lambda v: h-pb-(h-pb-10)*v/ymax
    s=f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{layer} frame 비율">'
    for t in [0,ymax/2,ymax]:
        s+=f'<line x1="{pl}" x2="{w-10}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="#ddd"/><text x="{pl-4}" y="{Y(t)+4:.1f}" font-size="10" text-anchor="end" fill="#666">{t*100:.1f}%</text>'
    for t in [1,5,10,20,30]:
        s+=f'<text x="{X(t):.1f}" y="{h-8}" font-size="10" text-anchor="middle" fill="#666">{t}</text>'
    s+=f'<line x1="{X(5):.1f}" x2="{X(5):.1f}" y1="10" y2="{h-pb}" stroke="#999" stroke-dasharray="3 3"/><text x="{X(5)+3:.1f}" y="20" font-size="10" fill="#666">5 µm</text>'
    for side in 'ab':
        pts=' '.join(f'{X(d):.1f},{Y(fr[(layer,d,side)]):.1f}' for d in D)
        s+=f'<polyline points="{pts}" fill="none" stroke="{C[side]}" stroke-width="2"/>'
    s+=f'<text x="{w-12}" y="{Y(fr[(layer,30,"a")])-4:.1f}" font-size="10" text-anchor="end" fill="{C["a"]}">a</text>'
    s+=f'<text x="{w-12}" y="{Y(fr[(layer,30,"b")])+12:.1f}" font-size="10" text-anchor="end" fill="{C["b"]}">b</text>'
    return s+f'<text x="{(w+pl)/2}" y="{h}" font-size="10" text-anchor="middle" fill="#666">지름 (µm)</text></svg>'

def bars(data,keys,title):
    w,h=360,30*len(keys)+30; mx=max(data.values())*1.1
    s=f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{E(title)}">'
    for i,k in enumerate(keys):
        y=10+i*30
        s+=f'<text x="0" y="{y+16}" font-size="11" fill="#333">{k}</text>'
        for j,side in enumerate('ab'):
            v=data[(k,side)]; bw=(w-170)*v/mx
            s+=f'<rect x="100" y="{y+j*12}" width="{bw:.1f}" height="10" fill="{C[side]}" rx="2"/><text x="{104+bw:.1f}" y="{y+j*12+9}" font-size="10" fill="#333">{v:.0f}</text>'
    return s+'</svg>'

def crop(p,side,kind):
    r=random.Random(hash((p['id'],side,kind))%10**6)
    s='<svg viewBox="0 0 100 100" width="110" height="110" style="background:#111;border:1px solid #999">'
    if kind=='layout':
        for k in range(4):
            x=r.uniform(0,80); s+=f'<rect x="{x:.0f}" y="0" width="{r.uniform(6,14):.0f}" height="100" fill="rgb(40,90,220)"/>'
        for k in range(3):
            y=r.uniform(0,85); s+=f'<rect x="0" y="{y:.0f}" width="100" height="{r.uniform(5,10):.0f}" fill="rgb(235,140,20)" opacity=".85"/>'
    else:
        for col in ['rgb(0,90,200)','rgb(0,150,80)','rgb(200,30,30)']:
            for k in range(r.randint(1,3)):
                s+=f'<ellipse cx="{r.uniform(20,80):.0f}" cy="{r.uniform(20,80):.0f}" rx="{r.uniform(4,14):.0f}" ry="{r.uniform(3,10):.0f}" fill="{col}" opacity=".9"/>'
    s+='<rect x="25" y="25" width="50" height="50" fill="none" stroke="red" stroke-width="1.5"/><rect x="31" y="31" width="38" height="38" fill="none" stroke="#3c3" stroke-width="1" stroke-dasharray="2 2"/>'
    return s+'</svg>'

def rank_table(rows,title,cls):
    if not rows: return f'<h3>{title}</h3><p class="muted">해당 쌍 없음</p>'
    t=f'<h3>{title}</h3><table class="rank"><tr><th>순위</th><th>쌍</th><th>layer</th><th>a CA (µm²)</th><th>b CA (µm²)</th><th>Δ (b−a)</th><th>Δ / 창 면적</th><th></th></tr>'
    for i,p in enumerate(rows,1):
        t+=f'<tr><td>{i}</td><td><a href="#{p["id"]}">{p["id"]}</a></td><td>{p["layer"]}</td><td>{p["a"]:.1f}</td><td>{p["b"]:.1f}</td><td class="{cls}"><b>{sgn(p["d"])}</b></td><td>{sgn(p["d"]/W*100,2)}%</td><td><span class="bar {cls}" style="width:{min(abs(p["d"])*1.6,80):.0f}px"></span></td></tr>'
    return t+'</table>'

# KPI
def kpi(label,val,sub,cls=''):
    return f'<div class="kpi"><div class="kl">{label}</div><div class="kv {cls}">{val}</div><div class="ks">{sub}</div></div>'
kp=''
for l in LAYERS:
    a,b=fr[(l,5,'a')],fr[(l,5,'b')]; d=(b-a)*100
    kp+=kpi(f'{l} union 면적 비율 Δ @5 µm',sgn(d,2)+'%p',f'a {a*100:.2f}% → b {b*100:.2f}%','up' if d>0 else 'down')
kp+=kpi('line Δ (가중, G1 미확정)',sgn(lp[('line','b')]-lp[('line','a')],0),f"a {lp[('line','a')]:.0f} → b {lp[('line','b')]:.0f}",'up')
kp+=kpi('pixel Δ (가중, G1 미확정)',sgn(lp[('pixel','b')]-lp[('pixel','a')],0),f"a {lp[('pixel','a')]:.0f} → b {lp[('pixel','b')]:.0f}",'down')
kp+=kpi('대응 쌍',f'{len(pairs)} / {len(pairs)+len(blocked)}',f'ready {len(pairs)} · blocked {len(blocked)} · partial {len(partial)}')

# detail cards
order=worse+better+[p for p in pairs if p not in worse and p not in better]
cards=''
for p in order:
    tag='악화' if p in worse else ('개선' if p in better else ('부분' if p['st']!='available' else '기타'))
    rows=''
    for m in METRICS:
        if m=='union': av,bv=p['a'],p['b']
        else:
            i=METRICS.index(m)-1; av=round(p['a']*p['pieces'][i]/sum(p['pieces']),1); bv=round(p['b']*p['pieces'][i]/sum(p['pieces']),1)
        dv=round(bv-av,1)
        rows+=f'<tr><td>{m}</td><td>{av:.1f}</td><td>{bv:.1f}</td><td class="{"up" if dv>0 else "down" if dv<0 else ""}"><b>{sgn(dv)}</b></td><td>{av*0.55:.1f}</td><td>{bv*0.55:.1f}</td></tr>'
    st='' if p['st']=='available' else '<p class="warn">partial: window_clipped_by_frame (b)</p>'
    cards+=f'''<div class="card" id="{p["id"]}"><div class="ch"><b>{p["id"]}</b> <span class="tag {tag}">{tag}</span> <span class="muted">layer {p["layer"]} · {p["ftype"]} · a 중심 ({p["cx"]}, {p["cy"]}) µm · b 중심 ({p["cx"]+1.5:.1f}, {p["cy"]-0.5:.1f}) µm</span></div>
<div class="cb"><div class="thumbs">{''.join(f'<figure>{crop(p,s,k)}<figcaption>{s} {k}</figcaption></figure>' for s in 'ab' for k in ('layout','hotspot'))}</div>
<table class="metrics"><tr><th rowspan="2">지표</th><th colspan="3">창 20 µm (µm²)</th><th colspan="2">core 15 µm</th></tr><tr><th>a</th><th>b</th><th>Δ</th><th>a</th><th>b</th></tr>{rows}</table></div>{st}</div>'''

# sensitivity
sens='<table><tr><th>쌍</th><th>Δ @10 µm</th><th>Δ @20 µm</th><th>Δ @30 µm</th><th>부호 일치</th></tr>'
for p in (worse+better)[:8]:
    d10=round(p['d']*0.4+random.uniform(-2,2),1); d30=round(p['d']*1.5+random.uniform(-5,5),1)
    sc='예' if (d10>0)==(p['d']>0)==(d30>0) else '<span class="warn">아니오</span>'
    sens+=f'<tr><td>{p["id"]}</td><td>{sgn(d10)}</td><td><b>{sgn(p["d"])}</b></td><td>{sgn(d30)}</td><td>{sc}</td></tr>'
sens+='</table><p class="muted">창 크기 쌍 간 |Δ| 순위 상관(Spearman): 10↔20 0.81 · 20↔30 0.88 · 10↔30 0.70. 추천값은 계산하지 않습니다.</p>'

# heatmap fake
def heat(side):
    r=random.Random(side); n=24; cs=7
    s=f'<svg viewBox="0 0 {n*cs} {n*cs}" width="{n*cs}" height="{n*cs}">'
    pal=['#f7fbff','#c6dbef','#6baed6','#2171b5','#08306b']
    for y in range(n):
        for x in range(n):
            v=int(min(4,max(0,r.gauss(1.3+math.sin(x/4)*math.cos(y/5),1)))); s+=f'<rect x="{x*cs}" y="{y*cs}" width="{cs}" height="{cs}" fill="{pal[v]}"/>'
    return s+'</svg>'
hist='<table><tr><th>전극 수</th><th>a 셀 수</th><th>b 셀 수</th></tr>'+''.join(f'<tr><td>{k}</td><td>{v1}</td><td>{v2}</td></tr>' for k,v1,v2 in [(0,'41,203','40,118'),(1,'18,540','19,002'),(2,'6,112','6,870'),(3,'1,021','1,344'),('≥4','88','131')])+'</table>'

gates=[('G1','밀도 계약','pending','line/pixel 가중값은 확정 전 값'),('G2','2×2 → 한 unit 환산','pending','frame 비율은 영향 없음'),('G4','창 크기','pending','20 µm 임시 기준'),('G7a','—','answered',''),('G7b','short only','answered','2026-10-08')]
gt='<table><tr><th>게이트</th><th>내용</th><th>상태</th><th>보고서 영향</th></tr>'+''.join(f'<tr><td>{a}</td><td>{b}</td><td class="{"warn" if c=="pending" else ""}">{c}</td><td>{d}</td></tr>' for a,b,c,d in gates)+'</table>'

issues=[('local_delta','P09','partial','window_clipped_by_frame'),('local_delta','P22','partial','valid_hole_in_window'),('local_delta','P27','blocked','asset_manifest_missing'),('local_delta','P28','blocked','center_outside_frame_b'),('comparison','ITO · 40 µm','partial','source_case_missing:3')]
it='<table><tr><th>출처</th><th>대상</th><th>상태</th><th>사유</th></tr>'+''.join(f'<tr><td>{a}</td><td>{b}</td><td class="warn">{c}</td><td><code>{d}</code></td></tr>' for a,b,c,d in issues)+'</table>'

prov='<table><tr><th>입력</th><th>SHA256</th></tr>'+''.join(f'<tr><td>{a}</td><td><code>{"%064x"%random.getrandbits(256)}</code></td></tr>' for a in ['comparison.json','electrode_map.json','local_delta.json','review_rois.json','config.yaml (config_sha256)','pairs.json (correspondence_sha256)'])+'</table>'

rois='<table><tr><th>ROI</th><th>종류</th><th>layer</th><th>중심 (µm)</th><th>상태</th></tr>'+''.join(f'<tr><td>R{i:03d}</td><td>{k}</td><td>{l}</td><td>({random.uniform(100,900):.1f}, {random.uniform(100,600):.1f})</td><td>{s}</td></tr>' for i,(k,l,s) in enumerate([('map_region','M2','ready'),('map_region','M1','ready'),('manual','ITO','ready'),('map_region','M2','blocked: asset_missing')],1))+'</table>'

css='''
:root{--fg:var(--foreground,#1d1d1f);--bg:var(--background,#fff);--mut:var(--muted-foreground,#6b6b6b);--bd:var(--border,#ddd)}
body{font-family:system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;color:var(--fg);background:var(--bg);max-width:1100px;margin:0 auto;padding:16px;font-size:14px;line-height:1.5}
.mock{background:#fff3cd;border:1px solid #e0b100;color:#5c4400;padding:6px 10px;border-radius:6px;font-weight:600;margin-bottom:12px}
h1{font-size:22px;margin:4px 0}h2{font-size:18px;border-bottom:2px solid var(--bd);padding-bottom:4px;margin-top:32px}h3{font-size:15px;margin:16px 0 6px}
.part{font-size:12px;letter-spacing:.08em;color:var(--mut);text-transform:uppercase;margin-top:28px}
.meta{display:grid;grid-template-columns:repeat(4,1fr);gap:6px 16px;font-size:13px;background:rgba(127,127,127,.06);padding:10px;border-radius:8px}.meta b{display:block;font-size:11px;color:var(--mut);font-weight:500}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.kpi{border:1px solid var(--bd);border-radius:8px;padding:10px}.kl{font-size:12px;color:var(--mut)}.kv{font-size:24px;font-weight:700}.ks{font-size:12px;color:var(--mut)}
.up{color:#c0392b}.down{color:#1e7d3a}.bar{display:inline-block;height:9px;border-radius:2px}.bar.up{background:#e15759}.bar.down{background:#59a14f}
table{border-collapse:collapse;font-size:13px;margin:4px 0}td,th{border:1px solid var(--bd);padding:3px 8px;text-align:right}th{background:rgba(127,127,127,.08);font-weight:600}td:first-child,th:first-child{text-align:left}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}.three{display:flex;gap:12px;flex-wrap:wrap}
.note{border-left:4px solid #e0b100;background:rgba(224,177,0,.08);padding:6px 10px;margin:8px 0;font-size:13px}
.warn{color:#b35c00}.muted{color:var(--mut);font-size:12px}
.card{border:1px solid var(--bd);border-radius:8px;margin:10px 0;padding:8px 10px}.ch{margin-bottom:6px}.cb{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-start}
.thumbs{display:flex;gap:6px}figure{margin:0;text-align:center}figcaption{font-size:11px;color:var(--mut)}
.tag{font-size:11px;padding:1px 6px;border-radius:8px;color:#fff}.tag.악화{background:#c0392b}.tag.개선{background:#1e7d3a}.tag.부분{background:#b35c00}.tag.기타{background:#888}
.legend span{margin-right:10px}nav{font-size:13px;margin:8px 0}nav a{margin-right:10px}
'''
out=f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>입자 시뮬레이션 비교 보고서 (시안)</title><style>{css}</style>
<div class="mock">시안(MOCKUP) · 모든 수치와 그림은 가짜 데이터입니다. 구성·형식 확인용.</div>
<h1>입자 시뮬레이션 비교 보고서</h1>
<div class="meta"><div><b>reference (a)</b>원안 PRD-A rev3</div><div><b>target (b)</b>변경 PRD-B rev1</div><div><b>방향</b>b − a</div><div><b>자료</b>실자료 (is_synthetic=false)</div>
<div><b>대표 지름</b>5 µm</div><div><b>순위 기준 창</b>20 µm <span class="warn">(임시 기준값)</span></div><div><b>요약 쌍 수</b>악화 10 · 개선 10</div><div><b>생성</b>2026-10-19 · commit 1ac6441</div></div>
<nav><a href="#A1">핵심 지표</a><a href="#A2">악화/개선 상위</a><a href="#A3">판정 조건</a> | <a href="#B1">면적 비율</a><a href="#B2">line/pixel</a><a href="#B3">대응 위치 상세</a><a href="#B4">창 민감도</a><a href="#B5">취약 위치 지도</a><a href="#B6">ROI</a><a href="#B7">상태·누락</a><a href="#B8">출처</a></nav>

<div class="part">A · 요약</div>
<h2 id="A1">A1. 핵심 지표</h2><div class="kpis">{kp}</div>
<p class="muted">면적 비율 = hotspot 면적 / 측정 2×2 frame 면적. 밀도와 무관한 값입니다. line/pixel은 밀도 가중값이며 G1 확정 전입니다.</p>
<h2 id="A2">A2. 대응 위치 변화 상위 (창 20 µm · 지름 5 µm · union)</h2>
<div class="two"><div>{rank_table(worse,"악화 (Δ &gt; 0) 상위 10","up")}</div><div>{rank_table(better,"개선 (Δ &lt; 0) 상위 10","down")}</div></div>
<p class="muted">변화 없음(Δ = 0) {len(zero)}쌍 · partial {len(partial)}쌍({", ".join(p["id"] for p in partial)}) · blocked {len(blocked)}쌍은 순위에서 제외, B7 참조. 쌍 ID를 누르면 B3 상세로 이동.</p>
<h2 id="A3">A3. 판정 조건과 주의</h2>{gt}
<div class="note">창 20 µm는 임시 기준값입니다. 제품마다 다를 수 있으며 B4 창 크기 민감도를 보고 정합니다.</div>
<div class="note">불량 유형 조각(lateral_only / vertical_only / simultaneous)은 line / pixel과 다른 축입니다. 두 축의 값을 서로 더하거나 빼지 않습니다.</div>
<div class="note">수율·밀도 단위 환산은 보류 항목이라 이 보고서에 포함하지 않습니다.</div>

<div class="part">B · 상세</div>
<h2 id="B1">B1. frame 면적 비율 (union, 지름별)</h2>
<p class="legend"><span style="color:{C["a"]}">━ a 원안</span><span style="color:{C["b"]}">━ b 변경</span></p>
<div class="three">{"".join(f"<div><h3>{l}</h3>{line_chart(l)}</div>" for l in LAYERS)}</div>
<table><tr><th>layer @5 µm</th>{"".join(f"<th>{m}</th>" for m in METRICS)}</tr>{"".join(f"<tr><td>{l}</td>"+"".join(f"<td>{fr[(l,5,'a')]*100*f:.2f}% → {fr[(l,5,'b')]*100*f:.2f}%</td>" for f in (1,0.5,0.33,0.17))+"</tr>" for l in LAYERS)}</table>
<h2 id="B2">B2. line / pixel / transfer 와 불량 유형 조각</h2>
<div class="two"><div><h3>line / pixel / transfer (unit, 가중)</h3>{bars(lp,["line","pixel","transfer"],"line pixel")}</div><div><h3>불량 유형 조각 (unit)</h3>{bars(pieces,["lateral_only","vertical_only","simultaneous"],"pieces")}</div></div>
<h2 id="B3">B3. 대응 위치 상세 (창 20 µm · 지름 5 µm)</h2>
<p class="legend muted">layout: <span style="color:rgb(40,90,220)">■ 전극 1</span><span style="color:rgb(235,140,20)">■ 전극 2</span> · hotspot: <span style="color:rgb(0,90,200)">■ lateral_only</span><span style="color:rgb(0,150,80)">■ vertical_only</span><span style="color:rgb(200,30,30)">■ simultaneous</span> · <span style="color:red">□ 창</span> <span style="color:#3c3">┅ attributable core</span>. 두 제품은 각자 자기 frame 좌표입니다.</p>
{cards}
<h2 id="B4">B4. 창 크기 민감도</h2>{sens}
<h2 id="B5">B5. 취약 위치 지도 (전극 수, 5 µm)</h2>
<div class="three"><figure>{heat("a")}<figcaption>a M2</figcaption></figure><figure>{heat("b")}<figcaption>b M2</figcaption></figure><div>{hist}</div></div>
<p class="muted">색이 진할수록 한 위치에서 short가 생길 수 있는 서로 다른 전극 수가 많습니다(관측 하한).</p>
<h2 id="B6">B6. 검토 ROI</h2>{rois}
<h2 id="B7">B7. 상태 · 누락</h2>{it}
<h2 id="B8">B8. 출처</h2>{prov}
</html>'''
open(sys.argv[1],'w',encoding='utf-8').write(out)
print(len(out.encode()))
