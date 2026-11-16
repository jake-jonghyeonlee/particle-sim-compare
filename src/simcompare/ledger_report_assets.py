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
function buildHash(o){var parts=[];['sel','tab','cat','q'].forEach(function(k){if(o.kv[k]!=null&&o.kv[k]!=='')parts.push(k+'='+encodeURIComponent(o.kv[k]));});
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
function apply(){var st=parseHash(),sel=selOf(st),pr=related(sel);filterRows(st);drawPartners(sel);
 if(st.flags.print){document.body.className='print';var d=document.querySelectorAll('details');for(var i=0;i<d.length;i++)d[i].open=true;}
 var lab=$('sel-label');if(lab)lab.innerHTML=label(sel);var clr=$('sel-clear');if(clr)clr.disabled=!sel;
 mark(sel,pr);drawCond(sel,pr,st);document.documentElement.setAttribute('data-sel',sel?sel.t+':'+sel.id:'');}
function go(id){var el=$(id);if(el&&el.scrollIntoView)el.scrollIntoView({block:'start'});}
document.addEventListener('click',function(ev){var t=ev.target;t=t&&t.closest?t.closest('[data-go],[data-href],[data-clear]'):null;if(!t)return;
 if(t.hasAttribute('data-clear')){var st=parseHash();delete st.kv.sel;st.flags.demo=false;setHash(st);return;}
 if(t.hasAttribute('data-go')){ev.preventDefault();go(t.getAttribute('data-go'));return;}
 var h=t.getAttribute('data-href');if(h){var st2=parseHash(),add={kv:{},flags:{}};
  h.replace(/^#/,'').split('&').forEach(function(p){var i=p.indexOf('=');if(i>0){var v=p.slice(i+1);try{v=decodeURIComponent(v);}catch(e){}add.kv[p.slice(0,i)]=v;}});
  Object.keys(add.kv).forEach(function(k){st2.kv[k]=add.kv[k];});if(add.kv.sel!=null)st2.flags.demo=false;setHash(st2);}});
document.addEventListener('keydown',function(ev){if(ev.key==='Enter'&&ev.target&&ev.target.getAttribute&&ev.target.getAttribute('data-href'))ev.target.click();});
var qi=$('el-q');if(qi)qi.addEventListener('input',function(){var st=parseHash();if(qi.value)st.kv.q=qi.value;else delete st.kv.q;
 var h=buildHash(st);history.replaceState(null,'',h==='#'?location.pathname+location.search:h);apply();});
var ca=$('cond-all');if(ca)ca.addEventListener('toggle',function(){apply();});
window.addEventListener('hashchange',apply);
apply();document.documentElement.setAttribute('data-ready','1');
})();
"""
