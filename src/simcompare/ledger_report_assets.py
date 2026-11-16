"""CSS and JS strings of the merged report (ledger_report_v2). Kept apart from ledger_report.py so that the page code and the
page text stay readable. No external URL; the script stays inside ES2017 (no optional chaining, no `??`, no class fields)."""

CSS = """
:root{color-scheme:light;--surface-0:#f4f3f0;--surface-1:#fcfcfb;--border:#e2e0da;--grid:#ebe9e4;
--text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#7a7972;
--line:#7a4fd6;--pixel:#eb6834;--up:#d03b3b;--down:#2a78d6;--mid:#e4e2dc;--common:#8d8c85;--sel:#fff3d6;
--badge-new:#fde7e7;--badge-gone:#e3eefc;--badge-both:#ecebe6;--trade:#f3ead0;--ok:#2b8a4e}
@media screen and (prefers-color-scheme:dark){:root{color-scheme:dark;--surface-0:#121211;--surface-1:#1a1a19;--border:#33332f;
--grid:#2a2a27;--text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#94938a;--line:#9a78ea;--pixel:#d95926;
--up:#e66767;--down:#3987e5;--mid:#3a3a36;--common:#77766f;--sel:#3a3018;--badge-new:#4a2323;--badge-gone:#1d2f4a;
--badge-both:#2c2c29;--trade:#2e2a1c;--ok:#5ac17f}}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:50px}
body{margin:0;background:var(--surface-0);color:var(--text-primary);font:13px/1.5 -apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}
main{max-width:1400px;margin:0 auto;padding:10px 16px 60px}
h1{font-size:19px;margin:6px 0 0}h2{font-size:16px;margin:0 0 2px}h3{font-size:13px;margin:10px 0 4px;color:var(--text-secondary)}
section{background:var(--surface-1);border:1px solid var(--border);border-radius:8px;padding:12px 16px;margin-top:12px}
.q{color:var(--text-secondary);font-weight:600;margin:0}.muted,.lead{color:var(--text-muted)}.lead{margin:0 0 6px}
#bar{position:sticky;top:0;z-index:9;background:var(--surface-1);border:1px solid var(--border);border-radius:0 0 8px 8px;
padding:5px 12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}#bar .sp{flex:1}
a{color:var(--down);cursor:pointer}
button{font:inherit;border:1px solid var(--border);background:var(--surface-1);color:var(--text-primary);border-radius:14px;padding:1px 10px;cursor:pointer}
button:disabled{opacity:.45;cursor:not-allowed}
.band{border:1px solid var(--border);border-left:4px solid var(--pixel);background:var(--trade);border-radius:6px;padding:8px 12px;margin:10px 0}
.band ul{margin:4px 0 0 18px;padding:0}.band.info{border-left-color:var(--common);background:var(--surface-0)}
table{border-collapse:collapse;width:100%}
th,td{padding:3px 7px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;vertical-align:top}
th{font-weight:600;color:var(--text-secondary);font-size:12px;border-bottom:1px solid var(--border);vertical-align:bottom}
.l{text-align:left}.wrap{white-space:normal}
tr.sum td{font-weight:650;border-top:1px solid var(--border)}
[data-href]{cursor:pointer}td[data-href]:hover,li[data-href]:hover{background:var(--sel)}
.sel,tr.sel td{background:var(--sel)}
.g3{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(0,1fr) minmax(0,.95fr);gap:16px}.g2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}
.kpis{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.kpi{border:1px solid var(--border);border-radius:8px;padding:8px 12px;background:var(--surface-0)}
.kpi .ab{font-size:14px;font-weight:600}.kpi .big{font-size:30px;font-weight:700;line-height:1.15}
.kpi .big.up{color:var(--up)}.kpi .big.dn{color:var(--down)}
.headline{border-left:3px solid var(--text-secondary);padding:5px 10px;margin:8px 0;background:var(--surface-0)}
.badge{display:inline-block;padding:0 7px;border-radius:9px;font-size:11px;background:var(--badge-both);color:var(--text-secondary);margin-right:3px;white-space:nowrap}
.b-new{background:var(--badge-new)}.b-gone{background:var(--badge-gone)}.b-mv{background:var(--trade)}.b-tag{background:transparent;border:1px dashed var(--common)}
.sub2{display:block;font-size:11px;color:var(--text-muted);font-weight:400;white-space:normal}
.chip{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px}
.sg{font-weight:600}.s-up{color:var(--up)}.s-down{color:var(--down)}
.s-up::before{content:"▲ ";font-size:10px}.s-down::before{content:"▼ ";font-size:10px}
.lst{list-style:none;margin:0;padding:0}
.lst li{display:flex;gap:6px;align-items:baseline;padding:3px 4px;border-bottom:1px solid var(--grid);cursor:pointer}
.lst .n{font-weight:700;min-width:26px}.lst .v{margin-left:auto;text-align:right}
.trust{margin-top:8px;padding:5px 10px;border:1px solid var(--border);border-radius:6px;background:var(--surface-0)}.trust2{margin-top:4px}
.ok{color:var(--ok);font-weight:700}.bad{color:var(--up);font-weight:700}
.slot{border:1px dashed var(--common);border-radius:6px;background:var(--surface-0);padding:10px;text-align:center;color:var(--text-muted);margin-top:6px}
details{margin-top:6px}summary{cursor:pointer;color:var(--text-secondary)}
.ovx{overflow-x:auto}
.scrl{max-height:420px;overflow:auto;border:1px solid var(--grid)}.scrl thead th{position:sticky;top:0;background:var(--surface-1);z-index:2}
.scrl tfoot td{position:sticky;bottom:0;background:var(--surface-1)}
#corr-table th[data-layer]{text-align:center;cursor:pointer}#corr-table td.c{text-align:center;min-width:34px}
.st-both{color:var(--text-secondary)}.st-a{color:var(--line)}.st-b{color:var(--pixel)}.st-mv{color:var(--ok);font-weight:700}
.st-unk{font-size:11px}.legend{margin:4px 0;color:var(--text-secondary)}.searchrow{margin:6px 0}
.searchrow input{font:inherit;border:1px solid var(--border);border-radius:6px;padding:1px 6px;background:var(--surface-1);color:var(--text-primary)}
td.nm{font-weight:600}
.hd-up1{background:rgba(208,59,59,.10)}.hd-up2{background:rgba(208,59,59,.20)}.hd-up3{background:rgba(208,59,59,.32)}.hd-up4{background:rgba(208,59,59,.46)}
.hd-dn1{background:rgba(42,120,214,.10)}.hd-dn2{background:rgba(42,120,214,.20)}.hd-dn3{background:rgba(42,120,214,.32)}.hd-dn4{background:rgba(42,120,214,.46)}
.partners{margin-top:8px}h4{font-size:12px;margin:8px 0 3px;color:var(--text-secondary)}
tr.hide{display:none}
.mpair{display:grid;grid-template-columns:1fr 1fr;gap:8px}.mfig{margin:0}.mfig figcaption{font-size:12px;color:var(--text-secondary)}
canvas.mapcv{display:block;width:100%;height:auto;background:var(--surface-0);border:1px solid var(--border);border-radius:6px;cursor:pointer;image-rendering:pixelated}
.mnote{font-size:12px;color:var(--text-muted)}.mwarn{border-left:4px solid var(--pixel);background:var(--trade);padding:4px 8px;margin:4px 0;font-size:12px}
.s2row{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:14px;align-items:start}.s2map{position:sticky;top:44px}
.s3grid{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:14px;align-items:start}.s3maps{position:sticky;top:44px}
.s3grid .slot{margin-top:0;min-height:120px}
.mbar{margin:4px 0;line-height:2}.mchip,.mtog{margin-right:4px}.mchip.on,.mtog.on{background:var(--sel);border-color:var(--text-secondary);font-weight:600}
.mrow{display:flex;gap:12px;align-items:flex-start}.mview{flex:1;min-width:0}.mleg{flex:0 0 150px;font-size:12px;position:sticky;top:44px}
.mleg div{margin:3px 0}.sw{display:inline-block;width:14px;height:10px;margin-right:5px;vertical-align:middle;border-radius:2px}
.mall{margin-bottom:8px}.smaps .mpair{grid-template-columns:1fr 1fr}
@media (max-width:1180px){.s2row,.s3grid{grid-template-columns:1fr}.s2map,.s3maps,.mleg{position:static}.mrow{flex-direction:column}}
@media (max-width:1180px){.g3,.g2,.kpis{grid-template-columns:1fr}}
@media print{*{-webkit-print-color-adjust:exact;print-color-adjust:exact}#bar{position:static}body{font-size:11px}}
"""

# The page works without the script (sections 1 and 5 are plain HTML). The script only reads the location hash, marks the
# selected rows with `.sel` and draws the condition table of section 5. `data-err` on <html> is set by any script error.
SCRIPT = r"""
(function(){
window.onerror=function(m,u,l){document.documentElement.setAttribute('data-err',String(m)+' @'+l);};
var D=JSON.parse(document.getElementById('report-data').textContent);
var $=function(id){return document.getElementById(id);};
var esc=function(s){return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');};
var PB={},EB={};
D.pairs.forEach(function(p){PB[p.id]=p;});
function parseHash(){var o={kv:{},flags:{}},h=location.hash.replace(/^#/,'');if(!h)return o;
 h.split('&').forEach(function(p){if(!p)return;var i=p.indexOf('=');if(i<0){o.flags[p]=true;return;}
  var k=p.slice(0,i),v=p.slice(i+1);try{v=decodeURIComponent(v);}catch(e){}o.kv[k]=v;});return o;}
function buildHash(o){var parts=[];['sel','tab','cat','q','layer','layers','ov'].forEach(function(k){if(o.kv[k]!=null&&o.kv[k]!=='')parts.push(k+'='+encodeURIComponent(o.kv[k]));});
 ['print','demo'].forEach(function(k){if(o.flags[k])parts.push(k);});return parts.length?'#'+parts.join('&'):'#';}
function setHash(o){var h=buildHash(o);if(h==='#'){if(location.hash){history.replaceState(null,'',location.pathname+location.search);}apply();}else{location.hash=h;}}
function selOf(o){var s=o.kv.sel;if(!s&&o.flags.demo&&D.demo_pair)s='pair:'+D.demo_pair;if(!s)return null;
 var i=s.indexOf(':');return i<0?null:{t:s.slice(0,i),id:s.slice(i+1)};}
function layersOfPair(p){var out={};p.items.forEach(function(it){[it.ul,it.ll].forEach(function(l){if(l)out[l]=true;});});
 (p.la||[]).concat(p.lb||[]).forEach(function(l){out[l]=true;});return out;}
function related(sel){var r={pairs:{},els:{},layers:{},rois:{}};if(!sel)return r;
 if(sel.t==='pair'&&PB[sel.id]){r.pairs[sel.id]=1;}
 else if(sel.t==='electrode'){r.els[sel.id]=1;D.pairs.forEach(function(p){if(p.e.indexOf(sel.id)>=0)r.pairs[p.id]=1;});}
 else if(sel.t==='layer'){D.pairs.forEach(function(p){if(layersOfPair(p)[sel.id])r.pairs[p.id]=1;});}
 else if(sel.t==='roi'&&D.changes&&D.changes.pairs){Object.keys(D.changes.pairs).forEach(function(id){if(D.changes.pairs[id].roi_keys.indexOf(sel.id)>=0)r.pairs[id]=1;});r.rois[sel.id]=1;}
 Object.keys(r.pairs).forEach(function(id){var p=PB[id];if(!p)return;p.e.forEach(function(e){if(sel.t==='pair'||sel.t==='electrode'&&e===sel.id)r.els[e]=1;});
  if(sel.t==='pair'){var L=layersOfPair(p);Object.keys(L).forEach(function(l){r.layers[l]=1;});}});
 if(sel.t==='layer')r.layers[sel.id]=1;return r;}
function label(sel){if(!sel)return '<span class="muted">선택 없음</span>';
 if(sel.t==='pair'){var p=PB[sel.id];return p?'쌍 <b>'+esc(p.no||'')+' '+esc(p.e.join('–'))+'</b>':'쌍 <b>'+esc(sel.id)+'</b> <span class="muted">(없는 쌍)</span>';}
 if(sel.t==='electrode')return '전극 <b>'+esc(sel.id)+'</b>';if(sel.t==='layer')return '레이어 <b>'+esc(sel.id)+'</b>';
 if(sel.t==='roi')return '변경 위치 <b>'+esc(sel.id)+'</b>';return '<span class="muted">선택 없음</span>';}
var f4=function(v){return v==null?'대기':(v===0?'0%':(v*100).toFixed(4)+'%');};
var n4=function(v){return v==null?'—':(v===0?'0':Number(v).toPrecision(4));};
function condRows(pairs){var rows=[];pairs.forEach(function(p){p.items.forEach(function(it){var sizes=it.sizes||[];
 if(!sizes.length)rows.push('<tr data-pair="'+esc(p.id)+'"><td class="l">'+esc(p.e.join('–'))+'</td><td class="l">'+esc(it.label)+'</td><td class="l muted" colspan="8">크기별 값 없음</td></tr>');
 sizes.forEach(function(s){rows.push('<tr data-pair="'+esc(p.id)+'"><td class="l">'+esc(p.e.join('–'))+'</td><td class="l">'+esc(it.label)+'</td><td>'+esc(s.diameter_um)+'</td><td class="l">'+esc(s.density_layer==null?'':s.density_layer)+
  '</td><td>'+n4(s.area_a)+' / '+n4(s.area_b)+'</td><td>'+n4(s.lambda_a)+' / '+n4(s.lambda_b)+'</td><td>'+f4(s.value_a)+'</td><td>'+f4(s.value_b)+'</td></tr>');});});});return rows;}
var CH='<thead><tr><th class="l">전극 쌍</th><th class="l">item</th><th>이물 크기 µm</th><th class="l">density 레이어</th><th>면적 A / B</th><th>λ A / B</th><th>값 A</th><th>값 B</th></tr></thead>';
function drawCond(sel,pr,state){var box=$('cond'),all=$('cond-all');if(!box)return;
 var ids=Object.keys(pr.pairs);
 if(!sel||!ids.length){box.innerHTML='<p class="muted">'+(sel?'선택한 것과 연결된 쌍이 없습니다.':'위에서 쌍을 하나 고르면 그 쌍의 조건 값이 이 자리에 나옵니다.')+'</p>';}
 else{var list=ids.sort().map(function(id){return PB[id];}).filter(Boolean);
  box.innerHTML='<p class="headline" style="margin:4px 0">선택: '+label(sel)+(sel.t==='pair'?'':' 의 관련 쌍 '+list.length+'개')+'</p><div class="ovx"><table id="cond-table">'+CH+'<tbody>'+condRows(list).join('')+'</tbody></table></div>';}
 if(all&&(state.flags.print||all.open)&&!all.getAttribute('data-drawn')){all.setAttribute('data-drawn','1');
  $('cond-all-body').innerHTML='<div class="ovx"><table id="cond-all-table">'+CH+'<tbody>'+condRows(D.pairs).join('')+'</tbody></table></div>';}}
function mark(sel,pr){var i,els,k,keys={'data-pair':pr.pairs,'data-el':pr.els,'data-layer':pr.layers,'data-roi':pr.rois};
 els=document.querySelectorAll('.sel');for(i=0;i<els.length;i++)els[i].classList.remove('sel');
 for(k in keys){els=document.querySelectorAll('['+k+']');for(i=0;i<els.length;i++){var v=els[i].getAttribute(k);if(keys[k][v])els[i].classList.add('sel');}}}
function drawPartners(sel){var box=$('el-partners');if(!box)return;
 if(!sel||sel.t!=='electrode'){box.innerHTML='<p class="muted">전극 이름이나 칸을 누르면 그 전극의 상대 전극과 쌍 Δ 가 이 자리에 나옵니다. 이 목록은 스크립트가 그립니다.</p>';return;}
 var rows=[];D.pairs.forEach(function(p){var i=p.e.indexOf(sel.id);if(i<0)return;var other=p.e.filter(function(x,j){return j!==i;}).join('–')||sel.id;
  rows.push('<tr data-pair="'+esc(p.id)+'" data-href="#sel=pair:'+encodeURIComponent(p.id)+'"><td class="l">'+esc(other)+'</td><td class="l">'+esc(p.kind)+'</td><td class="l">'+esc(D.labels.change_kinds[p.ck]||p.ck)+
  '</td><td>'+f4(p.a)+' → '+f4(p.b)+'</td><td class="l">'+(p.d==null?'대기':(p.d===0?'0%p':(p.d>0?'+':'−')+Math.abs(p.d*100).toFixed(4)+'%p'))+'</td></tr>');});
 box.innerHTML='<h3>선택한 전극 <b>'+esc(sel.id)+'</b> 의 상대 전극과 쌍 Δ <span class="muted" style="font-weight:400">(전극 장부 값 · 쌍 전체 값, 전극에 절반씩 나누기 전)</span></h3>'+
  (rows.length?'<div class="scrl"><table id="partner-table"><thead><tr><th class="l">상대 전극</th><th class="l">종류</th><th class="l">변화</th><th>A → B</th><th class="l">Δ</th></tr></thead><tbody>'+rows.join('')+'</tbody></table></div>':'<p class="muted">이 전극이 들어 있는 계산 쌍이 없습니다.</p>');}
function filterRows(st){var q=(st.kv.q||'').toLowerCase(),inp=$('el-q'),n=0,tot=0,rows=document.querySelectorAll('tr[data-el]'),i;
 if(inp&&inp.value!==(st.kv.q||''))inp.value=st.kv.q||'';
 for(i=0;i<rows.length;i++){var hit=!q||rows[i].getAttribute('data-el').toLowerCase().indexOf(q)>=0;rows[i].classList.toggle('hide',!hit);
  if(rows[i].getAttribute('data-row')==='corr'){tot++;if(hit)n++;}}
 var c=$('el-count');if(c)c.textContent=n+' / '+tot;}
/* ---- maps (mrep/T7): one PNG pair per layer and product, decoded on demand, self-checked, drawn in sections 1, 2 and 3 ---- */
var MP=D.maps||null,PNG=null,SD={},IB={},DEC={},GEN=0,ALPHA=0.8,CC={},ANYFAIL=false,DECODED=0;
if(MP){try{var pe=$('map-png');PNG=pe?JSON.parse(pe.textContent):null;}catch(e1){PNG=null;}
 D.pairs.forEach(function(p){p.items.forEach(function(it){IB[it.id]={p:p,it:it};});});
 ['A','B'].forEach(function(r){var s=MP.sides[r],o={maps:{},items:{},els:{}};
  s.maps.forEach(function(m){o.maps[m.l+'|'+m.k]=m;});s.items.forEach(function(i){o.items[i.n]=i;});s.els.forEach(function(e){o.els[e.n]=e.name;});SD[r]=o;});}
function rgb(name,fb){var v=getComputedStyle(document.documentElement).getPropertyValue(name).trim();
 var m=/^#([0-9a-f]{6})$/i.exec(v)||/^#([0-9a-f]{6})$/i.exec(fb);var n=parseInt(m[1],16);return [(n>>16)&255,(n>>8)&255,n&255];}
function colors(){CC={bg:rgb('--surface-0','#f4f3f0'),mid:rgb('--mid','#e4e2dc'),common:rgb('--common','#8d8c85'),line:rgb('--line','#7a4fd6'),
 pixel:rgb('--pixel','#eb6834'),down:rgb('--down','#2a78d6'),text:rgb('--text-primary','#0b0b0b')};}
function topPairs(){var seen={},out=[];['line','pixel'].forEach(function(k){(D.top.changed[k]||[]).forEach(function(id){if(!seen[id]&&PB[id]){seen[id]=1;out.push(PB[id]);}});});
 out.sort(function(a,b){return Math.abs(b.pct_d||0)-Math.abs(a.pct_d||0)||(a.id<b.id?-1:1);});return out.slice(0,3);}
function pairLayer(p){var i,ks;for(i=0;i<p.items.length;i++){if(p.items[i].al&&MP.layers.indexOf(p.items[i].al)>=0)return p.items[i].al;}
 ks=Object.keys(layersOfPair(p)).sort();for(i=0;i<ks.length;i++){if(MP.layers.indexOf(ks[i])>=0)return ks[i];}return null;}
function mapCfg(st,sel){var L=st.kv.layer,t;if(L!=='all'&&MP.layers.indexOf(L)<0)L=null;
 if(!L&&sel&&sel.t==='layer'&&MP.layers.indexOf(sel.id)>=0)L=sel.id;
 if(!L&&sel&&sel.t==='pair'&&PB[sel.id])L=pairLayer(PB[sel.id]);
 if(!L){t=topPairs();if(t.length)L=pairLayer(t[0]);}
 if(!L)L=MP.layers[0]||'';
 var on={shape:true,defect:true,others:false,roi:false};
 if(st.kv.layers!=null){on={shape:false,defect:false,others:false,roi:false};
  if(st.kv.layers!=='none')st.kv.layers.split(',').forEach(function(k){if(on.hasOwnProperty(k))on[k]=true;});}
 if(MP.shape_reason)on.shape=false;
 return {layer:L,single:L==='all'?(MP.layers[0]||''):L,on:on,ov:st.kv.ov==='1'&&!!(MP.overlay&&MP.overlay.available)};}
function checkNums(m,num){var cnt={},i,v,kc,kw;for(i=0;i<num.length;i++){v=num[i];if(v)cnt[v]=(cnt[v]||0)+1;}
 kc=Object.keys(cnt);kw=Object.keys(m.n);if(kc.length!==kw.length)return false;
 for(i=0;i<kw.length;i++){if(cnt[kw[i]]!==m.n[kw[i]])return false;}return true;}
function decodeOne(m,cb){var im=new Image();
 im.onload=function(){var res=null;try{var c=document.createElement('canvas');c.width=m.w;c.height=m.h;var g=c.getContext('2d');g.drawImage(im,0,0);
  var d=g.getImageData(0,0,m.w,m.h).data,num=new Uint16Array(m.w*m.h),bad=0,i;
  for(i=0;i<num.length;i++){num[i]=d[i*4]|(d[i*4+1]<<8);if(d[i*4+2]!==0||d[i*4+3]!==255)bad++;}
  res=(bad===0&&checkNums(m,num))?num:null;}catch(e2){res=null;}cb(res);};
 im.onerror=function(){cb(null);};im.src='data:image/png;base64,'+((PNG&&PNG[m.f])||'');}
function load(r,L,cb){var key=r+'|'+L,e=DEC[key];if(e&&e.done){cb(e);return;}if(e){e.cbs.push(cb);return;}
 e={done:false,cbs:[cb],it:null,el:null,ok:true,w:0,h:0,m:{}};DEC[key]=e;
 var left=0;['it','el'].forEach(function(k){if(SD[r].maps[L+'|'+k])left++;});
 function fin(){e.done=true;DECODED++;if(!e.ok)ANYFAIL=true;var cbs=e.cbs;e.cbs=[];cbs.forEach(function(f){f(e);});}
 if(!left){fin();return;}
 ['it','el'].forEach(function(k){var m=SD[r].maps[L+'|'+k];if(!m)return;e.w=m.w;e.h=m.h;e.m[k]=m;
  decodeOne(m,function(num){if(num)e[k]=num;else e.ok=false;left--;if(left===0)fin();});});}
function isBorder(a,w,h,x,y,n,t){if(x<t||y<t||x+t>=w||y+t>=h)return true;var i=y*w+x;
 return a[i-t]!==n||a[i+t]!==n||a[i-t*w]!==n||a[i+t*w]!==n;}
function hiSets(sel){var hi={},par={};if(!sel)return {hi:hi,par:par};
 if(sel.t==='electrode'){hi[sel.id]=1;D.pairs.forEach(function(p){if(p.e.indexOf(sel.id)>=0)p.e.forEach(function(x){if(x!==sel.id)par[x]=1;});});}
 else if(sel.t==='pair'&&PB[sel.id]){PB[sel.id].e.forEach(function(x){hi[x]=1;});}
 return {hi:hi,par:par};}
function noteOf(cv){var n=cv.parentNode.querySelector('.mnote');return n;}
function failList(r,L){var rows=[];Object.keys(SD[r].items).forEach(function(k){var it=SD[r].items[k],ib=IB[it.id];if(it.l!==L)return;
 rows.push('<li>'+(ib?'쌍 '+esc(ib.p.no||'')+' '+esc(ib.p.e.join('–'))+' · '+esc(ib.it.label):esc(it.id))+' · 번호 '+it.n+'</li>');});
 return '<div class="mwarn"><b>⚠ 지도 검사 실패</b> — 이 그림의 번호별 픽셀 수가 ledger_maps.json 과 다릅니다. 그림 대신 목록을 보입니다.</div><ul>'+rows.join('')+'</ul>';}
function dashOn(x,y,per){return ((x+y)%per)<per/2;}
function drawRois(g,cv,r,L,cfg,sel,w){var rois=(D.changes&&D.changes.rois)||[],cnt=0,lw=Math.max(1.5,w/260),fs=Math.max(11,Math.round(w/28));cv._rects=[];
 rois.forEach(function(roi,i){var m=roi.map;if(!m||m.status!=='ok')return;if(roi.layers&&roi.layers.length&&L!=='all'&&roi.layers.indexOf(L)<0)return;
  var side=r==='A'?m.reference:m.target;if(!side||side.status==='no_offset'||side.status==='outside_b_window'||!side.rect_map)return;
  var b=side.rect_map,on=sel&&sel.t==='roi'&&sel.id===roi.roi_key;
  g.save();g.lineWidth=on?lw*2:lw;g.strokeStyle='rgb('+CC.text.join(',')+')';g.setLineDash(m.shifted?[2,3,9,3]:[7,4]);
  g.strokeRect(b[0],b[1],b[2]-b[0],b[3]-b[1]);g.restore();
  var lab=String(i+1)+(m.shifted?'↻':'')+(side.clipped?'⌐':'')+(m.larger_than_window?'≫':'');
  g.save();g.font='bold '+fs+'px sans-serif';g.textBaseline='top';g.lineWidth=Math.max(2,fs/4);g.strokeStyle='rgb('+CC.bg.join(',')+')';g.fillStyle='rgb('+CC.text.join(',')+')';
  g.strokeText(lab,b[0]+2,b[1]+2);g.fillText(lab,b[0]+2,b[1]+2);g.restore();
  cv._rects.push({key:roi.roi_key,x0:b[0],y0:b[1],x1:b[2],y1:b[3]});cnt++;});return cnt;}
function render(cv,r,L,o,cfg,sel,pr){var e=DEC[r+'|'+L],note=noteOf(cv);cv._labels=[];cv._rects=[];cv._drawn=0;cv._lay=L;
 if(note)note.innerHTML='';
 if(!e||!e.w){cv.hidden=true;if(note)note.textContent='이 제품에는 이 레이어 지도가 없습니다';return 0;}
 if(!e.ok){cv.hidden=true;if(note)note.innerHTML=failList(r,L);return 0;}
 var w=e.w,h=e.h,g=cv.getContext('2d'),i,x,y,v;cv.width=w;cv.height=h;cv.hidden=false;
 var img=g.createImageData(w,h),d=img.data,tb=Math.max(2,Math.round(w/300)),per=Math.max(6,Math.round(w/80));
 for(i=0;i<w*h;i++){d[i*4]=CC.bg[0];d[i*4+1]=CC.bg[1];d[i*4+2]=CC.bg[2];d[i*4+3]=255;}
 function put(j,col,a){var q=j*4;d[q]=d[q]*(1-a)+col[0]*a;d[q+1]=d[q+1]*(1-a)+col[1]*a;d[q+2]=d[q+2]*(1-a)+col[2]*a;}
 var hs=hiSets(sel);
 if(o.shape&&e.el){var es={};
  for(i=0;i<w*h;i++){v=e.el[i];if(!v)continue;var nm=SD[r].els[v],k=es[v];if(k===undefined){k=es[v]=hs.hi[nm]?1:(hs.par[nm]?2:0);}
   x=i%w;y=(i/w)|0;
   if(k===1){put(i,CC.common,0.18);if(isBorder(e.el,w,h,x,y,v,tb))put(i,CC.text,1);}
   else if(k===2){if(isBorder(e.el,w,h,x,y,v,tb)&&dashOn(x,y,per))put(i,CC.common,1);else if(isBorder(e.el,w,h,x,y,v,1))put(i,CC.common,0.5);}
   else if(isBorder(e.el,w,h,x,y,v,1))put(i,CC.common,0.5);}}
 else if(o.shape&&note)note.textContent='이 레이어의 전극 윤곽(el)이 없습니다';
 var bb={};
 if(e.it&&(o.defect||o.others||o.tops)){var sty={},pidOf={};
  for(i=0;i<w*h;i++){v=e.it[i];if(!v)continue;var z=sty[v];
   if(z===undefined){z=null;var it=SD[r].items[v],ib=it&&IB[it.id];
    if(ib){var col=ib.p.kind==='pixel'?CC.pixel:CC.line,mine=o.defect&&pr.pairs[ib.p.id]&&sel&&sel.t!=='layer',top=o.tops&&o.tops[ib.p.id];
     if(top)z={m:'f',c:col,a:0.8};
     else if(mine)z=ib.it.pres!=='both'?{m:'d',c:col,a:1}:{m:'f',c:col,a:ALPHA};
     else if(o.others)z={m:'f',c:col,a:0.16};}
    sty[v]=z;pidOf[v]=ib?ib.p.id:'';}
   x=i%w;y=(i/w)|0;
   if(o.tops&&pidOf[v]&&o.tops[pidOf[v]]){var b=bb[v];if(!b){bb[v]=[x,y,x,y];}else{if(x<b[0])b[0]=x;if(x>b[2])b[2]=x;if(y<b[1])b[1]=y;if(y>b[3])b[3]=y;}}
   if(!z)continue;
   if(z.m==='f')put(i,z.c,z.a);
   else if(isBorder(e.it,w,h,x,y,v,tb)&&dashOn(x,y,per))put(i,z.c,1);}}
 if(o.ovB){var ob=DEC['B|'+L],oa=e;
  if(ob&&ob.ok&&ob.el&&ob.m.el&&oa.m.el){var off=MP.overlay.offset_px,bl=oa.m.el.b||1,sx=Math.round((ob.m.el.o[0]-oa.m.el.o[0]-off[0])/bl),sy=Math.round((ob.m.el.o[1]-oa.m.el.o[1]-off[1])/bl);
   for(y=0;y<ob.h;y++){for(x=0;x<ob.w;x++){var bn=ob.el[y*ob.w+x];if(!bn)continue;
    if(!isBorder(ob.el,ob.w,ob.h,x,y,bn,1)||!dashOn(x,y,per))continue;var tx=x+sx,ty=y+sy;if(tx<0||ty<0||tx>=w||ty>=h)continue;put(ty*w+tx,CC.down,1);}}}}
 g.putImageData(img,0,0);
 if(o.tops){var fs=Math.max(11,Math.round(w/14)),done={};g.font='bold '+fs+'px sans-serif';g.textBaseline='top';g.lineWidth=Math.max(2,fs/4);
  Object.keys(SD[r].items).forEach(function(n){var it=SD[r].items[n],ib=IB[it.id],b=bb[n];if(!ib||!b||!o.tops[ib.p.id])return;
   var u=done[ib.p.id];if(!u){done[ib.p.id]=[b[0],b[1],b[2],b[3]];}else{if(b[0]<u[0])u[0]=b[0];if(b[1]<u[1])u[1]=b[1];}});
  Object.keys(done).forEach(function(id){var u=done[id],lab=String(PB[id].no||'?'),tw=g.measureText(lab).width,lx=Math.max(1,Math.min(w-tw-1,u[0])),ly=Math.max(1,Math.min(h-fs-1,u[1]-fs));
   g.strokeStyle='rgb('+CC.bg.join(',')+')';g.fillStyle='rgb('+CC.text.join(',')+')';g.strokeText(lab,lx,ly);g.fillText(lab,lx,ly);
   cv._labels.push({id:id,x0:lx,y0:ly,x1:lx+tw,y1:ly+fs});});}
 if(o.roi)cv._rois=drawRois(g,cv,r,L,cfg,sel,w);else cv._rois=0;
 cv._drawn=1;return 1;}
function drawMaps(st,sel,pr){if(!MP)return;var gen=++GEN,cfg=mapCfg(st,sel),jobs=[],need={},root=document.documentElement;colors();
 var top3=topPairs(),tops={};top3.forEach(function(p){tops[p.id]=1;});
 var all=$('m3-all');
 if(all){all.innerHTML='';if(cfg.layer==='all'&&!cfg.ov){MP.layers.forEach(function(L){var div=document.createElement('div');div.className='mall';
  div.innerHTML='<h4>'+esc(L)+'</h4><div class="mpair">'+['A','B'].map(function(r){return '<figure class="mfig"><figcaption><b>'+(r==='A'?'기준 A':'대상 B')+'</b></figcaption><canvas class="mapcv" data-view="s3all" data-role="'+r+'" data-layer="'+esc(L)+'" width="1" height="1" hidden></canvas><div class="mnote"></div></figure>';}).join('')+'</div>';
  all.appendChild(div);});}}
 var cvs=document.querySelectorAll('canvas.mapcv'),k;
 for(k=0;k<cvs.length;k++){var cv=cvs[k],view=cv.getAttribute('data-view'),r=cv.getAttribute('data-role'),L=null,o=null;
  if(view==='s3'&&!cfg.ov&&cfg.layer!=='all'){L=cfg.layer;o={shape:cfg.on.shape,defect:cfg.on.defect,others:cfg.on.others,roi:cfg.on.roi};}
  else if(view==='s3all'){L=cv.getAttribute('data-layer');o={shape:cfg.on.shape,defect:cfg.on.defect,others:cfg.on.others,roi:cfg.on.roi};}
  else if(view==='ov'&&cfg.ov){L=cfg.single;o={shape:true,ovB:true,roi:cfg.on.roi};}
  else if(view==='s2'){L=cfg.single;o={shape:true};}
  else if(view==='s1'){L=cfg.single;o={shape:true,tops:tops};}
  if(L===null){cv.hidden=true;cv._drawn=0;continue;}
  jobs.push({cv:cv,r:r,L:L,o:o});need[r+'|'+L]=[r,L];if(view==='ov'){need['B|'+L]=['B',L];}}
 var keys=Object.keys(need),left=keys.length;
 function paint(){if(gen!==GEN)return;var n=0,rois=0;
  jobs.forEach(function(j){n+=render(j.cv,j.r,j.L,j.o,cfg,sel,pr);if(j.cv.getAttribute('data-view')==='s3'&&j.r==='A')rois=j.cv._rois||0;});
  var v3=$('m3-view'),vo=$('m3-ov');if(v3)v3.hidden=cfg.ov||cfg.layer==='all';if(vo)vo.hidden=!cfg.ov;if(all)all.hidden=!(cfg.layer==='all'&&!cfg.ov);
  var s2l=$('s2-layer');if(s2l)s2l.textContent='· 레이어 '+cfg.single;
  var on=Object.keys(cfg.on).filter(function(q){return cfg.on[q];});
  root.setAttribute('data-map-layer',cfg.layer);root.setAttribute('data-map-layers',on.join(','));root.setAttribute('data-map-ov',cfg.ov?'1':'0');
  root.setAttribute('data-map-rois',String(rois));root.setAttribute('data-map-drawn',String(n));
  document.body.setAttribute('data-map-check',DECODED?(ANYFAIL?'failed':'passed'):'none');root.setAttribute('data-map-check',DECODED?(ANYFAIL?'failed':'passed'):'none');
  var b=document.querySelectorAll('[data-mlayer]'),q;for(q=0;q<b.length;q++){b[q].classList.toggle('on',b[q].getAttribute('data-mlayer')===cfg.layer);}
  b=document.querySelectorAll('[data-t]');for(q=0;q<b.length;q++){var t=b[q].getAttribute('data-t');b[q].classList.toggle('on',t==='ov'?cfg.ov:!!cfg.on[t]);}}
 if(!left){paint();return;}
 keys.forEach(function(key){load(need[key][0],need[key][1],function(){left--;if(left===0)paint();});});}
function renderMaps(){var st=parseHash(),sel=selOf(st);drawMaps(st,sel,related(sel));}
function pickAt(cv,ev){var b=cv.getBoundingClientRect();return [Math.floor((ev.clientX-b.left)*cv.width/b.width),Math.floor((ev.clientY-b.top)*cv.height/b.height)];}
function chooseSel(id){var st=parseHash();st.kv.sel=id;st.flags.demo=false;setHash(st);}
function onMapClick(cv,ev){var view=cv.getAttribute('data-view'),r=cv.getAttribute('data-role'),q=pickAt(cv,ev),x=q[0],y=q[1],i;
 if(!cv._drawn)return;
 if(view==='s1'){for(i=0;i<(cv._labels||[]).length;i++){var lb=cv._labels[i];if(x>=lb.x0&&x<=lb.x1&&y>=lb.y0&&y<=lb.y1){chooseSel('pair:'+lb.id);go('s3');return;}}go('s3');return;}
 for(i=0;i<(cv._rects||[]).length;i++){var rc=cv._rects[i];if(x>=rc.x0&&x<=rc.x1&&y>=rc.y0&&y<=rc.y1){chooseSel('roi:'+rc.key);return;}}
 var lay=view==='s3all'?cv.getAttribute('data-layer'):cv._lay,e=DEC[r+'|'+lay];if(!e||!e.ok||x<0||y<0||x>=e.w||y>=e.h)return;
 var idx=y*e.w+x,n=e.it?e.it[idx]:0,it=n&&SD[r].items[n],ib=it&&IB[it.id];
 if(ib&&view!=='s2'){chooseSel('pair:'+ib.p.id);return;}
 var en=e.el?e.el[idx]:0;if(en&&SD[r].els[en]){chooseSel('electrode:'+SD[r].els[en]);}}
function toggleLayers(key){var st=parseHash(),cfg=mapCfg(st,selOf(st));
 if(key==='ov'){if(st.kv.ov==='1')delete st.kv.ov;else st.kv.ov='1';setHash(st);return;}
 cfg.on[key]=!cfg.on[key];var l=Object.keys(cfg.on).filter(function(q){return cfg.on[q];});st.kv.layers=l.length?l.join(','):'none';setHash(st);}
function apply(){var st=parseHash(),sel=selOf(st),pr=related(sel);filterRows(st);drawPartners(sel);
 if(st.flags.print){document.body.className='print';var d=document.querySelectorAll('details');for(var i=0;i<d.length;i++)d[i].open=true;}
 var lab=$('sel-label');if(lab)lab.innerHTML=label(sel);var clr=$('sel-clear');if(clr)clr.disabled=!sel;
 mark(sel,pr);drawCond(sel,pr,st);document.documentElement.setAttribute('data-sel',sel?sel.t+':'+sel.id:'');drawMaps(st,sel,pr);}
function go(id){var el=$(id);if(el&&el.scrollIntoView)el.scrollIntoView({block:'start'});}
document.addEventListener('click',function(ev){var t=ev.target;t=t&&t.closest?t.closest('[data-go],[data-href],[data-clear]'):null;if(!t)return;
 if(t.hasAttribute('data-clear')){var st=parseHash();delete st.kv.sel;st.flags.demo=false;setHash(st);return;}
 if(t.hasAttribute('data-go')){ev.preventDefault();go(t.getAttribute('data-go'));return;}
 var h=t.getAttribute('data-href');if(h){var st2=parseHash(),add={kv:{},flags:{}};
  h.replace(/^#/,'').split('&').forEach(function(p){var i=p.indexOf('=');if(i>0){var v=p.slice(i+1);try{v=decodeURIComponent(v);}catch(e){}add.kv[p.slice(0,i)]=v;}});
  Object.keys(add.kv).forEach(function(k){st2.kv[k]=add.kv[k];});if(add.kv.sel!=null)st2.flags.demo=false;setHash(st2);}});
document.addEventListener('click',function(ev){var t=ev.target;if(!t||!t.closest)return;
 var cv=t.closest('canvas.mapcv');if(cv){onMapClick(cv,ev);return;}
 var ch=t.closest('[data-mlayer]');if(ch){var s1=parseHash();s1.kv.layer=ch.getAttribute('data-mlayer');setHash(s1);return;}
 var tg=t.closest('[data-t]');if(tg&&!tg.disabled){toggleLayers(tg.getAttribute('data-t'));}});
var al=$('m-alpha');if(al)al.addEventListener('input',function(){ALPHA=Number(al.value)/100;renderMaps();});
document.addEventListener('keydown',function(ev){if(ev.key==='Enter'&&ev.target&&ev.target.getAttribute&&ev.target.getAttribute('data-href'))ev.target.click();});
var qi=$('el-q');if(qi)qi.addEventListener('input',function(){var st=parseHash();if(qi.value)st.kv.q=qi.value;else delete st.kv.q;
 var h=buildHash(st);history.replaceState(null,'',h==='#'?location.pathname+location.search:h);apply();});
var ca=$('cond-all');if(ca)ca.addEventListener('toggle',function(){apply();});
window.addEventListener('hashchange',apply);
apply();document.documentElement.setAttribute('data-ready','1');
})();
"""
