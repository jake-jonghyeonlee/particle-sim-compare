"""CSS and JS strings of the merged report (ledger_report_v2). Kept apart from ledger_report.py so that the page code and the
page text stay readable. No external URL; the script stays inside ES2017 (no optional chaining, no `??`, no class fields)."""

CSS = """
:root{color-scheme:light;--surface-0:#f4f3f0;--surface-1:#fcfcfb;--border:#e2e0da;--grid:#ebe9e4;
--text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#7a7972;
--line:#7a4fd6;--pixel:#eb6834;--up:#d03b3b;--down:#2a78d6;--mid:#e4e2dc;--common:#8d8c85;--sel:#fff3d6;
--badge-new:#fde7e7;--badge-gone:#e3eefc;--badge-both:#ecebe6;--trade:#f3ead0;--ok:#2b8a4e;--e1:#0f8b8d;--e2:#c98a00}
@media screen and (prefers-color-scheme:dark){:root{color-scheme:dark;--surface-0:#121211;--surface-1:#1a1a19;--border:#33332f;
--grid:#2a2a27;--text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#94938a;--line:#9a78ea;--pixel:#d95926;
--up:#e66767;--down:#3987e5;--mid:#3a3a36;--common:#77766f;--sel:#3a3018;--badge-new:#4a2323;--badge-gone:#1d2f4a;
--badge-both:#2c2c29;--trade:#2e2a1c;--ok:#5ac17f;--e1:#3cc3c5;--e2:#e5b23c}}
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
.b-new{background:var(--badge-new)}.b-gone{background:var(--badge-gone)}.b-mv{background:var(--trade)}
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
.mscale{margin-top:3px;font-size:11px;color:var(--text-secondary)}.mscale .sbar{height:5px;border:1px solid var(--text-primary);border-top:0;box-sizing:border-box}.mscale .scap{display:block}
canvas.mapcv,canvas.dcv{display:block;width:100%;height:auto;background:var(--surface-0);border:1px solid var(--border);border-radius:6px;image-rendering:pixelated}
canvas.mapcv{cursor:pointer}
.mnote{font-size:12px;color:var(--text-muted)}.mwarn{border-left:4px solid var(--pixel);background:var(--trade);padding:4px 8px;margin:4px 0;font-size:12px}
.s2row{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:14px;align-items:start}.s2map{position:sticky;top:44px}
.s3two{display:grid;grid-template-columns:minmax(340px,470px) minmax(0,1fr);gap:14px;align-items:start}.s3two .bgrid{grid-template-columns:1fr;gap:0}
.s3side{position:sticky;top:44px;align-self:start;height:calc(100vh - 54px);min-height:380px}
.dirs{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:8px 0 10px}.dirnote{margin:4px 0;color:var(--text-secondary)}
.dir{border:1px solid var(--border);border-left:5px solid var(--line);border-radius:8px;background:var(--surface-0);padding:8px 12px;cursor:pointer}
.dir.pixel{border-left-color:var(--pixel)}.dir.on{box-shadow:0 0 0 2px var(--text-primary) inset}
.dir .k{font-size:15px;font-weight:700}.dir .v{font-size:22px;font-weight:800;margin:0 8px}.dir .v.up{color:var(--up)}.dir .v.down{color:var(--down)}
.dir .dhow{font-size:13.5px;color:var(--text-secondary);margin-top:2px}.dir .dhow b{color:var(--text-primary)}
.klist[hidden]{display:none}
.crow{display:grid;grid-template-columns:34px minmax(0,1fr) 104px 18px;gap:6px;align-items:center;background:var(--surface-0);border:1px solid var(--border);border-radius:7px;padding:6px 8px;margin:4px 0;cursor:pointer}
.crow:hover{border-color:var(--common)}.crow.selcard{background:var(--sel);border-color:#e0b04a;box-shadow:0 0 0 1px #e0b04a inset}
.crow .rk{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:50%;font-weight:800;font-size:13px;color:#fff;background:var(--up)}
.crow .rk.dn{background:var(--down)}.crow .rk.op{background:var(--surface-1);border:2px solid var(--up);color:var(--up)}.crow .rk.dn.op{border-color:var(--down);color:var(--down)}
.crow .ct{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.crow .cw{font-size:12px;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.crow .shb{height:6px;background:var(--grid);border-radius:3px;margin-top:3px;overflow:hidden}.crow .shb i{display:block;height:100%;background:var(--up)}.crow .shb i.dn{background:var(--down)}
.crow .cv{text-align:right}.crow .cv .sh{font-size:11px;color:var(--text-muted)}
.crow .tw{color:var(--text-muted);font-size:13px;text-align:center;user-select:none;cursor:pointer}.crow.open .tw{transform:rotate(90deg)}
.crow .cexp{grid-column:1/-1;border-top:1px dashed var(--border);margin-top:4px;padding-top:5px;font-size:12px;display:none;cursor:default}
.crow.open .cexp,body.print .crow .cexp{display:block}
.crow .cexp h4{margin:6px 0 2px}.crow .cexp .bp{margin:2px 0}
.cref{margin:4px 0;padding:2px 8px}a.ctl{color:inherit;text-decoration:none}
.dpanel{border:1px solid var(--border);border-radius:8px;background:var(--surface-0);height:100%;display:flex;flex-direction:column;overflow:hidden}
#s3-detail{flex:1;min-height:0;display:flex;flex-direction:column}#s3-detail>p{margin:8px 12px}
.dph{flex:none;padding:7px 12px;border-bottom:1px solid var(--border);display:flex;gap:4px 12px;align-items:baseline;flex-wrap:wrap}.dph .dpt{font-size:15px;font-weight:800}.dph .dpk{white-space:nowrap}
.dmaps{flex:none;padding:5px 12px 4px;border-bottom:1px solid var(--border)}.dmaps h4{margin:0 0 2px}
.dmaps canvas.dcv{max-height:24vh;object-fit:contain}
.dtabs{flex:none;display:flex;gap:2px;padding:6px 12px 0;border-bottom:1px solid var(--border)}
.dtab{border:1px solid var(--border);border-bottom:0;border-radius:7px 7px 0 0;background:var(--surface-1);color:var(--text-secondary);padding:3px 12px;font-size:12.5px}
.dtab.on{background:var(--surface-0);color:var(--text-primary);font-weight:700;position:relative;top:1px}
.dtb{flex:1;min-height:0;overflow:auto;padding:8px 12px}.dtp[hidden]{display:none}.dtp>.pt{display:none}body.print .dtp>.pt{display:block}
.dsplit{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}.dcol h4{display:flex;justify-content:space-between;margin:0 0 4px}
.dpr{display:grid;grid-template-columns:22px minmax(0,1fr) 92px;gap:6px;align-items:center;padding:3px 4px;border-radius:5px;cursor:pointer}
.dpr:hover{background:var(--sel)}.dpr.on{background:var(--sel);outline:1px solid #e0b04a}.dpr .dpv{text-align:right}.dpr .n{font-weight:700;color:var(--text-muted);text-align:right}.dpr .dbar{height:5px;margin-top:2px}.dpr.cnorow{cursor:default;grid-template-columns:minmax(0,1fr)}.dpr.cnorow:hover{background:none}
.eq{border-left:3px solid var(--text-secondary);background:var(--surface-1);padding:4px 10px;margin:0 0 4px;font-size:13px}.eq.whole{border-left-color:var(--common);color:var(--text-secondary)}
.grp{margin:8px 0 2px;font-weight:700;font-size:12.5px;display:flex;justify-content:space-between}
#dp-sumbad{background:#fde2e2;color:#a12a2a;border-radius:8px;padding:0 7px;font-size:11px;font-weight:700}
.dbar{display:block;height:9px;border-radius:2px;background:var(--common)}.dbar.up{background:var(--up)}.dbar.down{background:var(--down)}
.mctl{display:flex;gap:6px;align-items:center;font-size:12px;margin-bottom:4px;flex-wrap:wrap}.mctl .sp{flex:1}
.lchs{display:inline-flex;gap:4px}.lch{border:1px solid var(--border);border-radius:10px;padding:0 8px;cursor:pointer;background:var(--surface-1);color:var(--text-primary);font-size:12px}
.lch.on{background:var(--text-primary);color:var(--surface-1);border-color:var(--text-primary)}
button.linkbtn{border:0;border-radius:0;background:none;color:var(--down);padding:0;font-size:11.5px;text-decoration:underline;cursor:pointer}
.leg{display:flex;gap:12px;flex-wrap:wrap;font-size:11.5px;color:var(--text-secondary);margin-top:3px}
.leg i{position:relative;display:inline-block;width:14px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px;border:var(--b)}.leg i::before{content:"";position:absolute;inset:0;background:var(--c);opacity:var(--a)}
#dp-items tr.itc td{font-weight:700}#dp-items tr.itsel td{background:var(--trade);outline:1px solid #e0b04a}
.rt th,.rt td{white-space:normal}
.purpose{margin:6px 0 4px;padding:7px 12px;border-left:3px solid var(--text-primary);background:var(--surface-1);font-size:15px;line-height:1.7}
.nextl{margin:12px 0 0;padding-top:6px;border-top:1px dashed var(--border);color:var(--text-muted)}
.report{border-left:3px solid var(--text-secondary);padding:7px 14px;margin:6px 0 10px;background:var(--surface-0);border-radius:0 6px 6px 0;font-size:14px;line-height:1.7}
.report p{margin:0 0 5px}.summary{border-left:3px solid var(--text-secondary);padding:6px 12px;margin:6px 0 10px;background:var(--surface-0)}.summary p{margin:0 0 3px}
.how,.ex,.note{background:var(--surface-0);border:1px solid var(--border);border-radius:6px;padding:6px 12px;margin:8px 0}.ex{border-style:dashed}.ex td{border:0;padding:1px 14px 1px 0;text-align:left}.ex table{width:auto}
.bgrid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start}
.bt2 th,.bt2 td{padding:2px 5px;font-size:12px}.unitcell{outline:2px solid #e0b04a}
a.lk{border-bottom:1px dotted var(--down)}a.chip{display:inline-block;width:auto;height:auto;border:1px solid var(--border);border-radius:14px;padding:0 10px;margin:0 2px;color:var(--text-primary);text-decoration:none}
.warn{color:#b07d00}.bar{display:inline-block;height:9px;background:var(--common);border-radius:2px;vertical-align:middle}.bar.line{background:var(--line)}.bar.pixel{background:var(--pixel)}
.small{font-size:11px}
.cbtns{margin-left:8px}a.lkbtn{display:inline-block;border:1px solid var(--border);border-radius:14px;padding:0 9px;margin-right:4px;font-size:11px;color:var(--text-primary);text-decoration:none;background:var(--surface-1)}a.lkbtn:hover{background:var(--sel)}
@media (max-width:1180px){.bgrid{grid-template-columns:1fr}}
@media (max-width:1180px){.s2row{grid-template-columns:1fr}.s2map{position:static}}
@media (max-width:1100px){.s3two{grid-template-columns:1fr}.s3side{position:static;height:auto}.dpanel{height:auto;overflow:visible}#s3-detail{display:block}.dtb{overflow:visible}.dmaps canvas.dcv{max-height:none}}
body.print .s3side{position:static;height:auto}body.print .dpanel{height:auto;overflow:visible}body.print #s3-detail{display:block}body.print .dtb{overflow:visible}body.print .dmaps canvas.dcv{max-height:none}
@media (max-width:1180px){.g3,.g2,.kpis{grid-template-columns:1fr}}
@media (max-width:900px){.dirs{grid-template-columns:1fr}}
@media print{*{-webkit-print-color-adjust:exact;print-color-adjust:exact}#bar{position:static}body{font-size:11px}.klist[hidden]{display:block!important}.crow .cexp{display:block}}
"""

# The page works without the script (sections 1 and 5 are plain HTML). The script reads the location hash, marks the selected rows
# with `.sel`, draws the detail panel of section 3 (cause candidate -> its pairs -> failure types of a pair -> the places on the maps),
# filters the pair finder of section 4, draws the section 2 map and the condition table of section 5.
# `data-err` on <html> is set by any script error.
SCRIPT = r"""
(function(){
window.onerror=function(m,u,l){document.documentElement.setAttribute('data-err',String(m)+' @'+l);};
var D=JSON.parse(document.getElementById('report-data').textContent);
var $=function(id){return document.getElementById(id);};
var esc=function(s){return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');};
var PB=Object.create(null),LORD=Array.isArray(D.layer_order)?D.layer_order:[],KINDS=['line','pixel'];
var SELT={pair:1,electrode:1,layer:1,unit:1};
D.pairs.forEach(function(p){PB[p.id]=p;});
var BU=Object.create(null),IT=Object.create(null),TOS=/^(s[1-6]|sA|explore)$/,UNIT=/^bd-[0-9a-f]{16,24}$/;
(D.bundles||[]).forEach(function(b){BU[b.id]=b;});
D.pairs.forEach(function(p){p.items.forEach(function(it){IT[it.id]=p.id;});});
function parseHash(){var o={kv:{},flags:{}},h=location.hash.replace(/^#/,'');if(!h)return o;
 h.split('&').forEach(function(p){if(!p)return;var i=p.indexOf('=');if(i<0){o.flags[p]=true;return;}
  var k=p.slice(0,i),v=p.slice(i+1);try{v=decodeURIComponent(v);}catch(e){}
  if((k==='unit'||k==='cand')&&!(UNIT.test(v)&&BU[v]))return;   /* the hash is untrusted: a unit must be a known cause candidate id */
  if(k==='to'&&!TOS.test(v))return;
  if(k==='k'&&v!=='line'&&v!=='pixel')return;   /* the list kind of 3 */
  if(k==='it'&&!IT[v])return;
  o.kv[k]=v;});return o;}
function buildHash(o){var parts=[];['sel','cand','q','eq','more','layer','unit','it','k','to'].forEach(function(k){if(o.kv[k]!=null&&o.kv[k]!=='')parts.push(k+'='+encodeURIComponent(o.kv[k]));});
 ['print','demo'].forEach(function(k){if(o.flags[k])parts.push(k);});return parts.length?'#'+parts.join('&'):'#';}
function setHash(o){var h=buildHash(o);if(h==='#'){if(location.hash){history.replaceState(null,'',location.pathname+location.search);}apply();}else{location.hash=h;}}
function selOf(o){var s=o.kv.sel;if(!s&&o.flags.demo&&D.demo_pair)s='pair:'+D.demo_pair;if(!s)return null;
 var i=s.indexOf(':');if(i<0)return null;var t=s.slice(0,i);
 if(!Object.prototype.hasOwnProperty.call(SELT,t))return null;   /* the hash is untrusted: only these four kinds */
 if(t==='unit'&&!(UNIT.test(s.slice(i+1))&&BU[s.slice(i+1)]))return null;   /* a cause candidate must be a known id */
 return {t:t,id:s.slice(i+1)};}
function layersOfPair(p){var out={};p.items.forEach(function(it){[it.ul,it.ll].forEach(function(l){if(l)out[l]=true;});});
 (p.la||[]).concat(p.lb||[]).forEach(function(l){out[l]=true;});return out;}
function related(sel){var r={pairs:{},els:{},layers:{},items:{}};if(!sel)return r;
 if(sel.t==='unit'&&BU[sel.id]){var ub=BU[sel.id];Object.keys(ub.pairs).forEach(function(id){if(PB[id])r.pairs[id]=1;});
  ub.els.forEach(function(e){r.els[e]=1;});ub.layers.forEach(function(l){r.layers[l]=1;});Object.keys(ub.pairs).forEach(function(id){var pp=PB[id];if(pp)ub.pairs[id][3].forEach(function(n){if(pp.items[n])r.items[pp.items[n].id]=1;});});}   /* M3: the pairs and items of one cause candidate */
 else if(sel.t==='pair'&&PB[sel.id]){r.pairs[sel.id]=1;}
 else if(sel.t==='electrode'){r.els[sel.id]=1;D.pairs.forEach(function(p){if(p.e.indexOf(sel.id)>=0)r.pairs[p.id]=1;});}
 else if(sel.t==='layer'){D.pairs.forEach(function(p){if(layersOfPair(p)[sel.id])r.pairs[p.id]=1;});}
 Object.keys(r.pairs).forEach(function(id){var p=PB[id];if(!p)return;p.e.forEach(function(e){if(sel.t==='pair'||sel.t==='electrode'&&e===sel.id)r.els[e]=1;});
  if(sel.t==='pair'){var L=layersOfPair(p);Object.keys(L).forEach(function(l){r.layers[l]=1;});}});
 if(sel.t==='layer')r.layers[sel.id]=1;return r;}
function unitCellOf(u){var c=u&&BU[u]&&BU[u].cells[0];return c?{e:c[0],l:c[1]}:null;}
function cardsOfCell(e,l){var out=[];(D.bundles||[]).forEach(function(b){b.cells.forEach(function(c){if(c[0]===e&&c[1]===l&&out.indexOf(b)<0)out.push(b);});});
 out.sort(function(a,b){return (b.ic?1:0)-(a.ic?1:0);});return out;}
function label(sel,unit){var u=unit&&BU[unit]?'원인 후보 <b>'+esc(BU[unit].title)+'</b>':'';
 if(!sel)return u||'<span class="muted">없음</span>';return (u?u+' · ':'')+label0(sel);}
function label0(sel){if(!sel)return '<span class="muted">없음</span>';
 if(sel.t==='unit'&&BU[sel.id])return '원인 후보 <b>'+esc(BU[sel.id].title)+'</b>';
 if(sel.t==='pair'){var p=PB[sel.id];return p?'쌍 <b>'+esc(p.no||'')+' '+esc(p.e.join('–'))+'</b>':'쌍 <b>'+esc(sel.id)+'</b> <span class="muted">(없는 쌍)</span>';}
 if(sel.t==='electrode')return '전극 <b>'+esc(sel.id)+'</b>';if(sel.t==='layer')return '레이어 <b>'+esc(sel.id)+'</b>';
 return '<span class="muted">선택 없음</span>';}
var f4=function(v){return v==null?'대기':(v===0?'0%':(v*100).toFixed(4)+'%');};
var n4=function(v){return v==null?'—':(v===0?'0':Number(v).toPrecision(4));};
function condRows(pairs){var rows=[],zero=[];pairs.forEach(function(p){p.items.forEach(function(it){var sizes=it.sizes||[];
 if(!sizes.length)rows.push('<tr data-pair="'+esc(p.id)+'"><td class="l">'+esc(p.e.join('–'))+'</td><td class="l">'+esc(it.label)+'</td><td class="l muted" colspan="8">크기별 값 없음</td></tr>');
 sizes.forEach(function(s){(s.value_a===0&&s.value_b===0?zero:rows).push('<tr data-pair="'+esc(p.id)+'"><td class="l">'+esc(p.e.join('–'))+'</td><td class="l">'+esc(it.label)+'</td><td>'+esc(s.diameter_um)+'</td><td class="l">'+esc(s.density_layer==null?'':s.density_layer)+
  '</td><td>'+n4(s.area_a)+' / '+n4(s.area_b)+'</td><td>'+n4(s.lambda_a)+' / '+n4(s.lambda_b)+'</td><td>'+f4(s.value_a)+'</td><td>'+f4(s.value_b)+'</td></tr>');});});});rows.zero=zero;return rows;}
function condTotal(pairs){var sa=0,sb=0,dd=0,nul=false;pairs.forEach(function(p){dd+=p.d||0;p.items.forEach(function(it){(it.sizes||[]).forEach(function(s){
 if(s.value_a==null||s.value_b==null)nul=true;sa+=s.value_a||0;sb+=s.value_b||0;});});});return {a:sa,b:sb,d:dd,nul:nul};}
function condTable(id,pairs,open,total){var rows=condRows(pairs),n=rows.zero.length,h='<div class="ovx"><table id="'+id+'">'+CH+'<tbody>'+rows.join('')+'</tbody>',foot='',t;
 if(n)h+='<tbody class="zrows"'+(open?'':' hidden')+'>'+rows.zero.join('')+'</tbody>';
 if(total&&(rows.length||n)){t=condTotal(pairs);   /* the sum line: value A sum, value B sum, B − A (= the whole-pair delta when the sizes add up) */
  foot+='<tr class="sum" id="cond-sum"><td colspan="6" class="l">합 — 값 B − 값 A 합 = 쌍 전체 Δ</td><td>'+f4(t.nul?null:t.a)+'</td><td>'+f4(t.nul?null:t.b)+'</td></tr>'+
   '<tr class="sum"><td colspan="6" class="l">Δ = 값 B − 값 A</td><td colspan="2" id="cond-sum-d">'+(t.nul?'<span class="muted">대기</span>':sgx(t.b-t.a))+
   (t.nul||pairs.length!==1||pairs[0].d==null?'':(Math.abs((t.b-t.a)-pairs[0].d)<1e-9?' <span class="muted small">(쌍 전체 Δ 와 같음)</span>':' <span class="muted small">(쌍 전체 Δ '+esc(ppx(pairs[0].d))+' 와 다름)</span>'))+'</td></tr>';
  document.documentElement.setAttribute('data-dp-condsum',t.nul||pairs.length!==1||pairs[0].d==null?'na':(Math.abs((t.b-t.a)-pairs[0].d)<1e-9?'passed':'failed'));}
 if(n)foot+='<tr><td colspan="8" class="l"><button type="button" class="zf" data-n="'+n+'">값 0 인 크기 '+n+(open?'개 접기':'개 펼치기')+'</button></td></tr>';
 if(foot)h+='<tfoot>'+foot+'</tfoot>';
 return h+'</table></div>';}
var CH='<thead><tr><th class="l">전극 쌍</th><th class="l">item</th><th>이물 크기 µm</th><th class="l">density 레이어</th><th>면적 A / B</th><th>λ A / B</th><th>값 A</th><th>값 B</th></tr></thead>';
function condBlock(list){   /* the condition table of the selected pair: the third tab of the section 3 detail panel (values 0 folded; a sum line at the end) */
 return list.length?'<p class="muted small" style="margin:0 0 4px">조건 값 — 이물 크기 · density 레이어 · 면적 · λ · 값. 값 0 인 크기는 접혀 있습니다.</p>'+condTable('cond-table',list,!!parseHash().flags.print,true):'<p class="muted">쌍이 없습니다.</p>';}
function drawCond(sel,pr,state){var all=$('cond-all');if(!all)return;
 if(all&&(state.flags.print||all.open)&&!all.getAttribute('data-drawn')){all.setAttribute('data-drawn','1');
  $('cond-all-body').innerHTML=condTable('cond-all-table',D.pairs,true);}}
function mark(sel,pr){var i,els,k,keys={'data-pair':pr.pairs,'data-el':pr.els,'data-layer':pr.layers};
 els=document.querySelectorAll('.sel');for(i=0;i<els.length;i++)els[i].classList.remove('sel');
 for(k in keys){els=document.querySelectorAll('['+k+']');for(i=0;i<els.length;i++){if(els[i].closest&&els[i].closest('#cond-detail'))continue;var v=els[i].getAttribute(k);if(keys[k][v])els[i].classList.add('sel');}}}
function drawPartners(sel){var box=$('el-partners');if(!box)return;
 if(!sel||sel.t!=='electrode'){box.innerHTML='<p class="muted">전극 이름이나 칸을 누르면 그 전극의 상대 전극과 쌍 Δ 가 이 자리에 나옵니다. 이 목록은 스크립트가 그립니다.</p>';return;}
 var rows=[];D.pairs.forEach(function(p){var i=p.e.indexOf(sel.id);if(i<0)return;var other=p.e.filter(function(x,j){return j!==i;}).join('–')||sel.id;
  rows.push('<tr data-pair="'+esc(p.id)+'" data-href="#sel=pair:'+encodeURIComponent(p.id)+'" data-scroll="explore"><td class="l">'+esc(other)+'</td><td class="l">'+esc(p.kind)+'</td><td class="l">'+esc(D.labels.change_kinds[p.ck]||p.ck)+
  '</td><td>'+(p.pa?f4(p.a):'없음')+' → '+(p.pb?f4(p.b):'없음')+'</td><td class="l">'+(p.d==null?'대기':(p.d===0?'0%p':(p.d>0?'+':'−')+Math.abs(p.d*100).toFixed(4)+'%p'))+'</td></tr>');});
 box.innerHTML='<h3>선택한 전극 <b>'+esc(sel.id)+'</b> 의 상대 전극과 쌍 Δ <span class="muted" style="font-weight:400">(전극 장부 값 · 쌍 전체 값, 전극에 절반씩 나누기 전)</span></h3>'+
  (rows.length?'<div class="scrl"><table id="partner-table"><thead><tr><th class="l">상대 전극</th><th class="l">종류</th><th class="l">변화</th><th>A → B</th><th class="l">Δ</th></tr></thead><tbody>'+rows.join('')+'</tbody></table></div>':'<p class="muted">이 전극이 들어 있는 계산 쌍이 없습니다.</p>');}
function filterRows(st){var q=(st.kv.eq||'').toLowerCase(),inp=$('el-q'),n=0,tot=0,rows=document.querySelectorAll('tr[data-row="corr"]'),i;
 if(inp&&inp.value!==(st.kv.eq||''))inp.value=st.kv.eq||'';   /* the section 2 search (#eq=) filters the section 2 table only; the section 5 ledger is not filtered */
 for(i=0;i<rows.length;i++){var hit=!q||rows[i].getAttribute('data-el').toLowerCase().indexOf(q)>=0;rows[i].classList.toggle('hide',!hit);tot++;if(hit)n++;}
 var c=$('el-count');if(c)c.textContent=n+' / '+tot;}
/* ---- section 4 pair finder: name search over the rows of every pair, the first SHOW rows unless more=1 ---- */
var SHOW=20;
function drawFind(st){var tb=$('pf-table'),root=document.documentElement;if(!tb)return;
 var rows=tb.tBodies[0].querySelectorAll('tr[data-pair]'),q=(st.kv.q||'').toLowerCase(),lim=st.kv.more==='1'?1e9:SHOW,n=0,sh=0,i,r,ok,show;
 var inp=$('pf-q');if(inp&&inp.value!==(st.kv.q||''))inp.value=st.kv.q||'';
 for(i=0;i<rows.length;i++){r=rows[i];ok=!q||r.getAttribute('data-name').indexOf(q)>=0;show=false;if(ok){n++;if(n<=lim){show=true;sh++;}}r.classList.toggle('hide',!show);}
 tb.setAttribute('data-count',String(n));tb.setAttribute('data-shown',String(sh));
 var pc=$('pf-count');if(pc)pc.textContent='결과 '+n+' 쌍 · 표시 '+sh;
 var pm=$('pf-more');if(pm){pm.hidden=n<=sh;pm.textContent='더 보기 (+'+(n-sh)+' 쌍)';}
 var pn=$('pf-none');if(pn)pn.hidden=n!==0;
 root.setAttribute('data-pf-count',String(n));root.setAttribute('data-pf-shown',String(sh));root.setAttribute('data-pf-total',tb.getAttribute('data-total'));}
/* ---- maps: one PNG pair per layer and product, decoded on demand, self-checked; drawn in section 2 and in the detail panel of 3 ---- */
var ppx=function(v){return v==null?'대기':(v===0?'0%p':(v>0?'+':'−')+Math.abs(v*100).toFixed(4)+'%p');};
var scl=function(v){return v==null||v===0?'':(v>0?'s-up':'s-down');};
var sgx=function(v){return v==null?'<span class="muted">대기</span>':'<span class="sg '+scl(v)+'">'+ppx(v)+'</span>';};
function ckBadge(p){var cls={new_pair:' b-new',gone_pair:' b-gone',layer_change:' b-mv',value_pending:' b-mv'}[p.ck]||'';return '<span class="badge'+cls+'">'+esc(D.labels.change_kinds[p.ck]||p.ck)+'</span>';}
var MP=D.maps||null,PNG=null,SD={},IB=Object.create(null),ITL=Object.create(null),DEC={},GEN=0,CC={},ANYFAIL=false,DECODED=0,MKEY=null;
if(MP){try{var pe=$('map-png');PNG=pe?JSON.parse(pe.textContent):null;}catch(e1){PNG=null;}
 D.pairs.forEach(function(p){p.items.forEach(function(it){IB[it.id]={p:p,it:it};});});
 ['A','B'].forEach(function(r){var s=MP.sides[r],o={maps:{},items:{},els:{}};
  s.maps.forEach(function(m){o.maps[m.l+'|'+m.k]=m;});s.items.forEach(function(i){o.items[i.n]=i;if(i.l&&!ITL[i.id])ITL[i.id]=i.l;});s.els.forEach(function(e){o.els[e.n]=e.name;});SD[r]=o;});}
function rgb(name,fb){var v=getComputedStyle(document.documentElement).getPropertyValue(name).trim();
 var m=/^#([0-9a-f]{6})$/i.exec(v)||/^#([0-9a-f]{6})$/i.exec(fb);var n=parseInt(m[1],16);return [(n>>16)&255,(n>>8)&255,n&255];}
function colors(){CC={bg:rgb('--surface-0','#f4f3f0'),mid:rgb('--mid','#e4e2dc'),common:rgb('--common','#8d8c85'),sec:rgb('--text-secondary','#52514e'),line:rgb('--line','#7a4fd6'),
 pixel:rgb('--pixel','#eb6834'),down:rgb('--down','#2a78d6'),text:rgb('--text-primary','#0b0b0b'),e1:rgb('--e1','#0f8b8d'),e2:rgb('--e2','#c98a00')};}
function topPairs(){var seen={},out=[];['line','pixel'].forEach(function(k){(D.top.changed[k]||[]).forEach(function(id){if(!seen[id]&&PB[id]){seen[id]=1;out.push(PB[id]);}});});
 out.sort(function(a,b){return Math.abs(b.pct_d||0)-Math.abs(a.pct_d||0)||(a.id<b.id?-1:1);});return out.slice(0,3);}
function byLayer(a,b){var ra=LORD.indexOf(a),rb=LORD.indexOf(b);ra=ra<0?LORD.length:ra;rb=rb<0?LORD.length:rb;return ra-rb||(a<b?-1:(a>b?1:0));}
function pairLayer(p){var i,ks;for(i=0;i<p.items.length;i++){if(p.items[i].al&&MP.layers.indexOf(p.items[i].al)>=0)return p.items[i].al;}
 ks=Object.keys(layersOfPair(p)).sort(byLayer);for(i=0;i<ks.length;i++){if(MP.layers.indexOf(ks[i])>=0)return ks[i];}return null;}
function tableLayers(){var o={},hs=document.querySelectorAll('#corr-table th[data-layer]'),i;for(i=0;i<hs.length;i++)o[hs[i].getAttribute('data-layer')]=1;return o;}
function mapCfg(st,sel){var L=st.kv.layer,t,ul=null,want=null,TL=tableLayers();
 if(MP.layers.indexOf(L)<0){if(L&&TL[L])want=L;L=null;}   /* rexp/T9: a layer of the table with no picture is kept, so the map says so */
 if(!want&&sel&&sel.t==='layer'&&MP.layers.indexOf(sel.id)<0&&TL[sel.id])want=sel.id;
 if(want&&!(sel&&sel.t==='unit'))return {layer:want,single:want};
 if(sel&&sel.t==='unit'&&BU[sel.id]){ul=BU[sel.id].layers.filter(function(l){return MP.layers.indexOf(l)>=0;});   /* M3: only the layers of the cause candidate */
  if(!L||ul.indexOf(L)<0)L=ul[0]||null;}
 if(!L&&sel&&sel.t==='layer'&&MP.layers.indexOf(sel.id)>=0)L=sel.id;
 if(!L&&st.kv.unit){var uc0=unitCellOf(st.kv.unit);if(uc0&&MP.layers.indexOf(uc0.l)>=0)L=uc0.l;}   /* a chosen cause candidate shows its own layer */
 if(!L&&sel&&sel.t==='pair'&&PB[sel.id])L=pairLayer(PB[sel.id]);
 if(!L){t=topPairs();if(t.length)L=pairLayer(t[0]);}
 if(!L)L=MP.layers[0]||'';
 return {layer:L,single:L};}
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
 else if(sel.t==='unit'&&BU[sel.id]){BU[sel.id].els.forEach(function(x){hi[x]=1;});}
 return {hi:hi,par:par};}
function noteOf(cv){var n=cv.parentNode.querySelector('.mnote');return n;}
function failList(r,L){var rows=[];Object.keys(SD[r].items).forEach(function(k){var it=SD[r].items[k],ib=IB[it.id];if(it.l!==L)return;
 rows.push('<li>'+(ib?'쌍 '+esc(ib.p.no||'')+' '+esc(ib.p.e.join('–'))+' · '+esc(ib.it.label):esc(it.id))+' · 번호 '+esc(it.n)+'</li>');});
 return '<div class="mwarn"><b>⚠ 지도 검사 실패</b> — 이 그림의 번호별 픽셀 수가 ledger_maps.json 과 다릅니다. 그림 대신 목록을 보입니다.</div><ul>'+rows.join('')+'</ul>';}
function dashOn(x,y,per){return ((x+y)%per)<per/2;}
function mapCheck(){var root=document.documentElement,v=DECODED?(ANYFAIL?'failed':'passed'):'none';document.body.setAttribute('data-map-check',v);root.setAttribute('data-map-check',v);}
/* section 2 map: electrode outlines of one layer, the selected electrode bold; the unit window of the product as a yellow dashed box */
function render(cv,r,L,sel){var e=DEC[r+'|'+L],note=noteOf(cv);cv._drawn=0;cv._lay=L;
 if(note)note.innerHTML='';
 if(!e||!e.w){cv.hidden=true;if(note)note.textContent=MP.layers.indexOf(L)<0?'이 층은 DL1 layout 그림이 없습니다':'이 제품에는 이 레이어 지도가 없습니다';return 0;}
 if(!e.ok){cv.hidden=true;if(note)note.innerHTML=failList(r,L);return 0;}
 var w=e.w,h=e.h,g=cv.getContext('2d'),i,x,y,v;cv.width=w;cv.height=h;cv.hidden=false;
 var sc=MP.sides[r].sc,mm=e.m.it||e.m.el,bk=(mm&&mm.b)||1;cv._phys=sc?[w*bk*sc.p,h*bk*sc.p]:null;cv._sc=sc||null;cv._win=null;
 if(sc&&!sc.whole)cv._win=[(sc.wo[0]-sc.ro[0])/bk,(sc.wo[1]-sc.ro[1])/bk,sc.ws[0]/bk,sc.ws[1]/bk];
 var img=g.createImageData(w,h),d=img.data,tb=Math.max(2,Math.round(w/300)),per=Math.max(6,Math.round(w/80));
 for(i=0;i<w*h;i++){d[i*4]=CC.bg[0];d[i*4+1]=CC.bg[1];d[i*4+2]=CC.bg[2];d[i*4+3]=255;}
 function put(j,col,a){var q=j*4;d[q]=d[q]*(1-a)+col[0]*a;d[q+1]=d[q+1]*(1-a)+col[1]*a;d[q+2]=d[q+2]*(1-a)+col[2]*a;}
 var hs=hiSets(sel);
 if(e.el){var es={};
  for(i=0;i<w*h;i++){v=e.el[i];if(!v)continue;var nm=SD[r].els[v],k=es[v];if(k===undefined){k=es[v]=hs.hi[nm]?1:(hs.par[nm]?2:0);}
   x=i%w;y=(i/w)|0;
   if(k===1){put(i,CC.common,0.18);if(isBorder(e.el,w,h,x,y,v,tb))put(i,CC.text,1);}
   else if(k===2){if(isBorder(e.el,w,h,x,y,v,tb)&&dashOn(x,y,per))put(i,CC.common,1);else if(isBorder(e.el,w,h,x,y,v,1))put(i,CC.common,0.5);}
   else if(isBorder(e.el,w,h,x,y,v,1))put(i,CC.common,0.5);}}
 else if(note)note.textContent='이 레이어의 전극 윤곽(el)이 없습니다';
 g.putImageData(img,0,0);
 if(cv._win){var wl=cv._win,lw=Math.max(2,w/160),rx=wl[0]+0.5,ry=wl[1]+0.5,rw=Math.max(1,wl[2]-1),rh=Math.max(1,wl[3]-1),fs=Math.max(11,Math.round(w/40));
  g.save();g.setLineDash([Math.max(8,w/30),Math.max(3,w/90)]);   /* bright yellow with a thin dark halo: told apart from the grey outlines in both themes */
  g.lineWidth=lw+2;g.strokeStyle='rgba(20,20,20,0.85)';g.strokeRect(rx,ry,rw,rh);
  g.lineWidth=lw;g.strokeStyle='#ffd21f';g.strokeRect(rx,ry,rw,rh);g.setLineDash([]);
  g.font='bold '+fs+'px sans-serif';g.textBaseline='top';var tw=g.measureText('unit').width+6;
  g.fillStyle='rgba(20,20,20,0.85)';g.fillRect(rx+lw,ry+lw,tw,fs+4);g.fillStyle='#ffd21f';g.fillText('unit',rx+lw+3,ry+lw+2);g.restore();}
 cv.setAttribute('data-win',cv._win?'1':'0');
 cv._drawn=1;return 1;}
function fnum(n){return String(Math.round(n*100)/100);}
function niceBar(um){var t=um/5,p=Math.pow(10,Math.floor(Math.log(t)/Math.LN10)),c=[1,2,5,10],b=p,i;
 for(i=0;i<c.length;i++){if(Math.abs(Math.log(c[i]*p/t))<Math.abs(Math.log(b/t)))b=c[i]*p;}return b;}
function scaleCaption(sc,ph){var rs=sc.rs?[sc.rs[0]*sc.p,sc.rs[1]*sc.p]:ph,   /* the region size comes from region.size_px x pitch; the width ratio of the pair uses the drawn px (ph) */
 s='영역 '+fnum(rs[0])+' × '+fnum(rs[1])+' µm · ';
 if(sc.whole)return s+'창 = frame 전체';
 var u=sc.u?(sc.u[0]===sc.u[1]?fnum(sc.u[0]):fnum(sc.u[0])+' × '+fnum(sc.u[1])):'?';
 s+='unit 창 '+u+' µm('+(sc.us==='derived'?'frame 에서 유도':'설정')+')';
 if(sc.r!=null)s+=' + 여백 '+fnum(sc.r)+' µm';
 return s;}
function applyScale(){var cvs=document.querySelectorAll('canvas.mapcv'),groups=[],k,i;
 for(k=0;k<cvs.length;k++){var cv=cvs[k],fg=cv.parentNode,host=fg.parentNode,sb=fg.querySelector('.mscale');
  if(sb&&sb.parentNode)sb.parentNode.removeChild(sb);cv.style.width='';cv.removeAttribute('data-phys-w-um');
  if(host&&host.classList.contains('mpair')&&groups.indexOf(host)<0){groups.push(host);host.removeAttribute('data-scale-ratio');}
  if(!(host&&host.classList.contains('mpair')))groups.push({solo:cv});}
 groups.forEach(function(gp){var list=gp.solo?[gp.solo]:Array.prototype.slice.call(gp.querySelectorAll('canvas.mapcv')),
   ok=list.filter(function(c){return !c.hidden&&c._drawn&&c._phys;}),mx=0,mn=0;
  ok.forEach(function(c){var w=c._phys[0];if(w>mx)mx=w;if(!mn||w<mn)mn=w;});
  ok.forEach(function(c){var ph=c._phys,sc=c._sc,pct=ph[0]/mx*100,bar=niceBar(ph[0]),d;
   if(!gp.solo)c.style.width=pct.toFixed(3)+'%';c.setAttribute('data-phys-w-um',fnum(ph[0]));
   d=document.createElement('div');d.className='mscale';if(!gp.solo)d.style.width=pct.toFixed(3)+'%';
   d.innerHTML='<div class="sbar" style="width:'+(bar/ph[0]*100).toFixed(2)+'%"></div><span class="scap">'+fnum(bar)+' µm · '+esc(scaleCaption(sc,ph))+'</span>';
   c.parentNode.insertBefore(d,c.nextSibling);});
  if(!gp.solo&&ok.length===2)gp.setAttribute('data-scale-ratio',(mn/mx).toFixed(3));});}
function drawMaps(st,sel){if(!MP)return;var gen=++GEN,cfg=mapCfg(st,sel),jobs=[],need={},root=document.documentElement;colors();
 root.setAttribute('data-map-gen',String(gen));   /* how many times the section 2 map was redrawn (the tests count it) */
 var cvs=document.querySelectorAll('canvas.mapcv'),k;
 for(k=0;k<cvs.length;k++){var cv=cvs[k],r=cv.getAttribute('data-role');
  if(cv.getAttribute('data-view')!=='s2'){cv.hidden=true;cv._drawn=0;continue;}
  jobs.push({cv:cv,r:r,L:cfg.single});need[r+'|'+cfg.single]=[r,cfg.single];}
 var keys=Object.keys(need),left=keys.length;
 function paint(){if(gen!==GEN)return;var n=0;
  jobs.forEach(function(j){n+=render(j.cv,j.r,j.L,sel);});
  applyScale();
  var s2l=$('s2-layer');if(s2l)s2l.textContent='· 레이어 '+cfg.single;
  root.setAttribute('data-map-layer',cfg.layer);root.setAttribute('data-map-drawn',String(n));mapCheck();}
 if(!left){paint();return;}
 keys.forEach(function(key){load(need[key][0],need[key][1],function(){left--;if(left===0)paint();});});}
function pickAt(cv,ev){var b=cv.getBoundingClientRect();return [Math.floor((ev.clientX-b.left)*cv.width/b.width),Math.floor((ev.clientY-b.top)*cv.height/b.height)];}
function onMapClick(cv,ev){var r=cv.getAttribute('data-role'),q=pickAt(cv,ev),x=q[0],y=q[1];   /* a click on the section 2 map picks the electrode under it */
 if(!cv._drawn)return;
 var e=DEC[r+'|'+cv._lay];if(!e||!e.ok||x<0||y<0||x>=e.w||y>=e.h)return;
 var en=e.el?e.el[y*e.w+x]:0;if(en&&SD[r].els[en]){var st=parseHash();st.kv.sel='electrode:'+SD[r].els[en];st.flags.demo=false;delete st.kv.cand;delete st.kv.it;setHash(st);}}
/* ---- detail panel of section 3 (rexp/T2): cause candidate -> its pairs -> failure types of a pair -> the places of the pair on the maps ---- */
var PRES={both:'증감',gone:'없어짐',new:'새로 생김'},DS={u:null,p:null,layer:null,zoom:true,key:'',tab:'items'},DGEN=0,SHOWPAIRS=30;
var TINY_FRACTION=0.01;   /* rexp/T7: a pair (or item) is "미미" when its value is 0 or smaller than this fraction of the total variation it belongs to */
var EMPTY='<p class="muted" id="dp-empty">원인 후보 카드나 4 결과의 쌍을 누르면 이 자리에 그 후보의 쌍, 불량 방식, 자리가 나옵니다.</p>';
function kchip(k){return '<span class="chip" style="background:var(--'+(k==='pixel'?'pixel':'line')+')"></span>';}
function unitPairs(u){var b=BU[u],ids=Object.keys(b.pairs).filter(function(id){return !!PB[id];});   /* the pairs of a candidate, the largest |net| first */
 ids.sort(function(x,y){return Math.abs(b.pairs[y][0])-Math.abs(b.pairs[x][0])||(x<y?-1:(x>y?1:0));});return ids;}
function primaryUnit(id){var best=null,tot=-1;(D.bundles||[]).forEach(function(b){var q=b.pairs&&b.pairs[id];if(q&&(q[1]>tot||(q[1]===tot&&b.id<best.id))){best=b;tot=q[1];}});return best?best.id:null;}
function detailOf(st,sel){var u=null,p=null;
 if(!sel&&st.kv.unit&&BU[st.kv.unit]){sel={t:'unit',id:st.kv.unit};}   /* #unit=<id> only: that candidate opens */
 else if(!sel&&!st.kv.unit&&D.default_unit&&BU[D.default_unit]){sel={t:'unit',id:D.default_unit};}   /* no selection: the first candidate of the first kind is open (the hash stays clean) */
 if(sel&&sel.t==='unit'&&BU[sel.id]){u=sel.id;p=unitPairs(u)[0]||null;}
 else if(sel&&sel.t==='pair'&&PB[sel.id]){p=sel.id;u=st.kv.cand&&BU[st.kv.cand]&&BU[st.kv.cand].pairs[p]?st.kv.cand:primaryUnit(p);}
 return {u:u,p:p};}
function headHtml(d){var b=d.u?BU[d.u]:null,p=d.p?PB[d.p]:null,h;
 if(b){h='<div class="dph dp-h"><span class="muted">원인 후보</span><span class="dpt">'+esc(b.title)+'</span><span class="badge">'+esc(b.ty||'')+'</span>'+(b.ic?'':'<span class="muted small">(카드에 없는 후보)</span>')+
  KINDS.map(function(k,i){return '<span class="dpk">'+kchip(k)+k+(i?' ':' 이 후보 몫 ')+sgx(b.nk?b.nk[k]:null)+'</span>';}).join('')+'</div>';}
 else h='<div class="dph dp-h"><span class="muted">원인 후보 없음</span> — 이 쌍의 변화는 어느 원인 후보에도 들어 있지 않습니다. 쌍 <b>'+esc(p?p.e.join('–'):'')+'</b></div>';
 return h;}
function tinyOf(v,tot){return v===0||Math.abs(v)<TINY_FRACTION*tot;}
function tinyText(){return '0 또는 후보 총 변동의 '+(TINY_FRACTION*100)+'% 미만';}
/* the pairs of a candidate in two columns (increase | decrease, by the candidate's own share); tiny shares fold under them */
function pairsHtml(d){var b=d.u?BU[d.u]:null;if(!b)return '<p class="muted">이 쌍은 어느 원인 후보에도 들어 있지 않아 후보의 쌍 목록이 없습니다.</p>';
 var ids=unitPairs(d.u),tot={},mx=0,up=[],dn=[],ti=[],h;
 ids.forEach(function(id){var k=PB[id].kind;tot[k]=(tot[k]||0)+Math.abs(b.pairs[id][0]);});
 ids.forEach(function(id){var v=b.pairs[id][0];if(tinyOf(v,tot[PB[id].kind]||0))ti.push(id);else{mx=Math.max(mx,Math.abs(v));(v>0?up:dn).push(id);}});
 function row(id,n){var p=PB[id],v=b.pairs[id][0],w=mx?Math.max(3,Math.round(Math.abs(v)/mx*100)):3;
  return '<div class="dpr'+(id===d.p?' on':'')+'" data-dp="'+esc(id)+'" data-href="#sel=pair:'+encodeURIComponent(id)+'&cand='+encodeURIComponent(d.u)+'" tabindex="0"><span class="n">'+(n||'')+'</span><span>'+kchip(p.kind)+esc(p.e.join('–'))+
   '<span class="sub2">쌍 전체 '+ppx(p.d)+' · '+esc(p.kind)+'</span><span class="dbar '+(v>0?'up':(v<0?'down':''))+'" style="width:'+w+'%"></span></span><span class="dpv">'+sgx(v)+'</span></div>';}
 function col(label,cls,list){var sum=0,head=list.slice(0,SHOWPAIRS),tail=list.slice(SHOWPAIRS),sel=list.indexOf(d.p)>=SHOWPAIRS;list.forEach(function(id){sum+=b.pairs[id][0];});
  return '<div class="dcol" data-col="'+cls+'"><h4><span class="s-'+cls+'">'+label+'</span><span class="muted" style="font-weight:400">'+list.length+' 쌍 · 합 '+ppx(sum)+'</span></h4>'+
   (list.length?head.map(function(id,i){return row(id,i+1);}).join('')+(tail.length?'<details'+(sel?' open':'')+'><summary class="small">나머지 '+tail.length+' 쌍 ▸</summary>'+tail.map(function(id,i){return row(id,SHOWPAIRS+i+1);}).join('')+'</details>':''):'<p class="muted small">없음</p>')+'</div>';}
 h='<p class="muted small" style="margin:0 0 4px">큰 숫자 = <b>이 후보 몫</b>(이 후보에 들어간 item 의 합), 작은 글씨 = 쌍 전체. 누르면 지도와 불량 방식이 그 쌍으로 바뀝니다.</p>'+
  '<div class="dsplit" id="dp-pairs">'+col('증가','up',up)+col('감소','down',dn)+'</div>';
 if(ti.length)h+='<details id="dp-tiny-pairs"'+(ti.indexOf(d.p)>=0?' open':'')+'><summary class="small">미미한 쌍 '+ti.length+' 개 ('+tinyText()+') ▸</summary>'+ti.map(function(id){return row(id,'');}).join('')+'</details>';
 if(b.cno)h+='<div class="dpr cnorow muted" id="dp-cno">CNO '+b.cno+' 건 — 지도에 없음 <span class="small">(전극 단독 CNO 는 쌍이 아닙니다)</span></div>';
 if(!ids.length)h+='<p class="muted">이 후보에는 쌍이 없습니다.</p>';
 return h;}
var ITU=null;
function itemUnits(){if(ITU)return ITU;ITU=Object.create(null);   /* item id -> the cause candidate that holds it (built once from the positions in D.bundles[].pairs[pair][3]) */
 (D.bundles||[]).forEach(function(b){Object.keys(b.pairs).forEach(function(pid){var pp=PB[pid];if(!pp)return;b.pairs[pid][3].forEach(function(n){var it=pp.items[n];if(it&&!ITU[it.id])ITU[it.id]=b.id;});});});return ITU;}
function dsum(list){var s=0,nul=false;list.forEach(function(it){if(it.d==null)nul=true;else s+=it.d;});return {s:s,nul:nul};}
function hasOcOf(p){return p.items.some(function(it){return it.oc&&isFinite(it.oc[0])&&isFinite(it.oc[1]);});}
function itRow(it,mine,curIt,hasOc){var oc=(it.oc&&isFinite(it.oc[0])&&isFinite(it.oc[1]))?esc(String(Number(it.oc[0])))+' → '+esc(String(Number(it.oc[1]))):'<span class="muted">—</span>',pc=it.pres==='gone'?'b-gone':(it.pres==='new'?'b-new':''),
  uid=itemUnits()[it.id],ub=uid?BU[uid]:null,who=mine?'<span class="muted">이 후보</span>':(ub?'<a class="lk" href="#sel=unit:'+encodeURIComponent(ub.id)+'" data-dp-unit-link="'+esc(ub.id)+'">'+esc(ub.title)+' ▸</a>':'<span class="muted">후보 없음</span>');
 return '<tr data-it="'+esc(it.id)+'" class="'+(mine?'itc':'')+(it.id===curIt?' itsel':'')+'"><td class="l">'+esc(it.label)+'</td><td>'+(it.pres==='new'?'없음':(it.af?'거리 필터로 0':f4(it.a)))+'</td><td>'+(it.pres==='gone'?'없음':(it.bf?'거리 필터로 0':f4(it.b)))+
  '</td><td>'+sgx(it.d)+'</td><td class="l"><span class="badge '+pc+'">'+esc(PRES[it.pres]||it.pres)+'</span></td>'+(hasOc?'<td>'+oc+'</td>':'')+'<td class="l">'+who+'</td></tr>';}
function itTable(list,mine,curIt,hasOc){if(!list.length)return '';
 return '<table><thead><tr><th class="l">방식 · 레이어</th><th>A</th><th>B</th><th>Δ</th><th class="l">구분</th>'+(hasOc?'<th>연결 영역 A → B</th>':'')+'<th class="l">들어간 원인 후보</th></tr></thead><tbody>'+
  list.map(function(it){return itRow(it,mine,curIt,hasOc);}).join('')+'</tbody></table>';}
/* the failure types of the chosen pair: two scopes — this candidate's share, and the whole pair — with the items split in the same two groups */
function itemsHtml(d){var b=d.u?BU[d.u]:null,p=d.p?PB[d.p]:null,curIt=parseHash().kv.it,br,whole;
 if(!p)return b&&b.cno?'<h4>불량 방식</h4><p class="muted" id="dp-cno-only">CNO — 지도에 없음. 이 후보는 쌍이 아니라 전극 단독 CNO '+b.cno+' 건입니다.</p>':'';
 var inc={};if(b&&b.pairs[p.id])b.pairs[p.id][3].forEach(function(n){if(p.items[n])inc[p.items[n].id]=1;});
 br=p.brk;
 if(!br||br.status==='not_evaluated'||br.same_delta==null||br.new_value==null||br.gone_value==null)whole='<span class="muted">대기</span> — item 분해를 계산하지 못했습니다.';
 else whole='쌍 Δ = 같은 불량 증감 <b>'+ppx(br.same_delta)+'</b> + 새 방식 <b>'+f4(br.new_value)+'</b> − 없어진 방식 <b>'+f4(br.gone_value)+'</b>';
 var its=p.items.slice().sort(function(x,y){return Math.abs(y.d||0)-Math.abs(x.d||0)||(x.label<y.label?-1:(x.label>y.label?1:0));}),
  mine=its.filter(function(it){return !!inc[it.id];}),other=its.filter(function(it){return !inc[it.id];}),all=dsum(its),X=dsum(mine),Z=dsum(other),totAbs=0,i,hasOc=hasOcOf(p);
 its.forEach(function(it){totAbs+=Math.abs(it.d||0);});
 var chk=(p.d==null||all.nul)?'na':(Math.abs(all.s-p.d)<1e-12?'passed':'failed'),nm=esc(p.e.join('–'));
 var a=0,nw=0,gn=0;mine.forEach(function(it){var v=it.d||0;if(it.pres==='new')nw+=v;else if(it.pres==='gone')gn-=v;else a+=v;});
 var eq='<div id="dp-eq" data-dp-sumcheck="'+chk+'">'+(b?'<div class="eq" id="dp-eq-mine"><b>이 후보 몫</b> '+sgx(X.s)+' = 같은 불량 증감 <b>'+ppx(a)+'</b> + 새 방식 <b>'+f4(nw)+'</b> − 없어진 방식 <b>'+f4(gn)+'</b></div>':'')+
  '<div class="eq whole" id="dp-eq-all">쌍 <b>'+nm+'</b> <b>전체</b> '+sgx(p.d)+(b?' = 이 후보 '+ppx(X.s)+' + 다른 후보 '+ppx(Z.s):'')+
  (chk==='failed'?' <span class="bad" id="dp-sumbad" title="보이는 item 줄의 합 '+esc(ppx(all.s))+' 이 쌍 전체 '+esc(ppx(p.d))+' 와 다릅니다">합 불일치</span>':'')+'</div><div class="muted small" id="dp-eq-parts">'+whole+'</div></div>';
 document.documentElement.setAttribute('data-dp-sumcheck',chk);
 function group(title,list,isMine,sum){if(!list.length)return '';var big=[],tiny=[];
  list.forEach(function(it){(it.d!=null&&tinyOf(it.d,totAbs)?tiny:big).push(it);});
  return '<div class="grp"><span>'+title+' ('+list.length+' 줄)</span><span>소계 '+sgx(sum)+'</span></div>'+itTable(big,isMine,curIt,hasOc)+
   (tiny.length?'<details'+(tiny.some(function(it){return it.id===curIt;})?' open':'')+'><summary class="small">미미한 item '+tiny.length+' 개 ('+tinyText()+') ▸</summary>'+itTable(tiny,isMine,curIt,hasOc)+'</details>':'');}
 return '<h4>불량 방식 — <b>'+nm+'</b> '+ckBadge(p)+'</h4>'+eq+'<div id="dp-items">'+(b?group('이 후보 item',mine,true,X.s):'')+group(b?'다른 후보 item':'item',other,false,Z.s)+'</div>'+
  '<p class="muted small" style="margin:4px 0">값 = 그 item 의 vi 몫(%p). Δ = B − A. 굵게 = 이 후보에 들어간 item. 연결 영역 = ledger-maps 가 센 hotspot 덩어리 수(관측값, 값이 없으면 열이 없습니다).</p>'+
  (b&&b.cno?'<p class="muted small" id="dp-cno-line">CNO — 지도에 없음 (이 후보의 CNO '+b.cno+' 건은 전극 단독 값입니다)</p>':'');}
function pairLayers(p){var o={};['A','B'].forEach(function(r){var its=SD[r].items;Object.keys(its).forEach(function(n){var it=its[n];if(it.p===p.id&&it.l&&MP.layers.indexOf(it.l)>=0)o[it.l]=1;});});return Object.keys(o).sort(byLayer);}
function defaultLayer(p,ls,itId){var i,l,its;if(itId&&ITL[itId]&&ls.indexOf(ITL[itId])>=0)return ITL[itId];
 its=p.items.slice().sort(function(x,y){return Math.abs(y.d||0)-Math.abs(x.d||0);});   /* the layer of the item with the largest |delta| that has a map */
 for(i=0;i<its.length;i++){l=ITL[its[i].id];if(l&&ls.indexOf(l)>=0)return l;}return ls[0];}
function zoomText(){return DS.zoom?'전체 보기':'확대로 돌아가기';}
function legendHtml(p){var k=p.kind==='pixel'?'pixel':'line',   /* swatches: a tinted face (--c at --a) with the outline --b */
 sw=function(c,a,b){return '<i style="--c:var(--'+c+');--a:'+a+';--b:'+b+'"></i>';};
 return '<div class="leg" id="dp-legend"><span>'+sw('e1','.25','2px solid var(--e1)')+esc(p.e[0]||'')+'</span>'+
  (p.e.length>1?'<span>'+sw('e2','.25','2px solid var(--e2)')+esc(p.e[1])+'</span>':'')+
  '<span>'+sw('mid','.35','1px solid var(--common)')+'다른 전극</span>'+
  '<span>'+sw(k,'.85','1px solid var(--text-secondary)')+'이 쌍의 불량 영역('+k+')</span></div>';}
/* the places of the chosen pair: A | B in one row (layer chips, a small link for the whole map), the legend under them */
function placesHtml(d){var p=d.p?PB[d.p]:null;if(!p)return '';
 var h='<div class="mctl"><b>자리 보기</b> — 쌍 <b>'+esc(p.e.join('–'))+'</b>';
 if(!MP)return h+'</div><p class="muted" id="dp-nomap">지도 없음 — --maps-dir 를 주면 이 자리에 A | B 지도가 나옵니다.</p>';
 var ls=pairLayers(p);
 if(!ls.length){DS.layer=null;return h+'</div><p class="muted" id="dp-nolayer">이 쌍의 영역이 지도에 없습니다(지도 자료에 이 쌍의 item 번호가 없음).</p>';}
 if(!DS.layer||ls.indexOf(DS.layer)<0)DS.layer=defaultLayer(p,ls,parseHash().kv.it);
 return h+' · 레이어 <span id="dp-layer" class="lchs">'+ls.map(function(l){return '<button type="button" class="lch'+(l===DS.layer?' on':'')+'" data-l="'+esc(l)+'">'+esc(l)+'</button>';}).join('')+'</span>'+
  '<span class="sp"></span><button type="button" class="linkbtn" id="dp-zoom">'+zoomText()+'</button></div>'+
  '<div class="mpair">'+['A','B'].map(function(r){return '<figure class="mfig"><figcaption><b>'+(r==='A'?'기준 A':'대상 B')+'</b> · <span class="dplay">'+esc(DS.layer)+'</span></figcaption><canvas class="dcv" id="dcv-'+r+'" data-role="'+r+'" width="1" height="1" role="img" aria-label="'+(r==='A'?'기준 A':'대상 B')+' 지도" hidden></canvas><div class="mnote"></div></figure>';}).join('')+'</div>'+legendHtml(p);}
function bbox(e,r,pid){var memo={},n=0,x0=1e9,y0=1e9,x1=-1,y1=-1,w=e.w,i,v,z,x,y,it;if(!e.it)return {n:0};
 for(i=0;i<e.it.length;i++){v=e.it[i];if(!v)continue;z=memo[v];if(z===undefined){it=SD[r].items[v];z=memo[v]=!!(it&&it.p===pid);}if(!z)continue;
  x=i%w;y=(i/w)|0;n++;if(x<x0)x0=x;if(x>x1)x1=x;if(y<y0)y0=y;if(y>y1)y1=y;}
 return {n:n,x0:x0,y0:y0,x1:x1,y1:y1};}
function blend(d,o,col,a){d[o]=d[o]*(1-a)+col[0]*a;d[o+1]=d[o+1]*(1-a)+col[1]*a;d[o+2]=d[o+2]*(1-a)+col[2]*a;}
/* rexp/T8: the crop is drawn at screen size (long side DRAWPX), not at the map's pixels, so a 1 px / 2 px outline stays 1 px / 2 px on the screen:
   a light face and a 1 px outline for every electrode, the two electrodes of the pair in their own colours (--e1, --e2: 25 % face, 2 px outline),
   the failure areas of the pair in the kind colour (85 %) with a 1 px dark border. A map pixel of the pair is never lost when the crop is shrunk. */
var DRAWPX=600;
function drawCrop(cv,e,r,p,x0,y0,x1,y1){var cw=x1-x0,ch=y1-y0,g=cv.getContext('2d'),w=e.w,h=e.h,col=p.kind==='pixel'?CC.pixel:CC.line,memo={},x,y,v,z,it,si,o,i,q,
  s=DRAWPX/Math.max(cw,ch),ow=Math.max(1,Math.round(cw*s)),oh=Math.max(1,Math.round(ch*s)),n1=0,n2=0,
  dark=[col[0]*0.55,col[1]*0.55,col[2]*0.55],role={},rl=function(num){var k=role[num];if(k===undefined){var nm=SD[r].els[num];k=role[num]=(nm!==undefined&&nm===p.e[0])?1:((nm!==undefined&&p.e.length>1&&nm===p.e[1])?2:0);}return k;};
 cv.width=ow;cv.height=oh;cv.hidden=false;
 var elD=new Uint16Array(ow*oh),itD=new Uint8Array(ow*oh);
 if(e.el){for(y=0;y<oh;y++){for(x=0;x<ow;x++){elD[y*ow+x]=e.el[(y0+Math.min(ch-1,Math.floor(y/s)))*w+x0+Math.min(cw-1,Math.floor(x/s))];}}}
 if(e.it){for(y=0;y<ch;y++){for(x=0;x<cw;x++){v=e.it[(y+y0)*w+x+x0];if(!v)continue;z=memo[v];if(z===undefined){it=SD[r].items[v];z=memo[v]=!!(it&&it.p===p.id);}
   if(z)itD[Math.min(oh-1,Math.floor(y*s))*ow+Math.min(ow-1,Math.floor(x*s))]=1;}}}   /* a source pixel of the pair marks its screen pixel (never lost by shrinking) */
 if(s>1){for(y=0;y<oh;y++){for(x=0;x<ow;x++){   /* enlarged: every screen pixel of a block of a pair pixel is the pair's */
   if(e.it){v=e.it[(y0+Math.min(ch-1,Math.floor(y/s)))*w+x0+Math.min(cw-1,Math.floor(x/s))];if(v&&memo[v])itD[y*ow+x]=1;}}}}
 var img=g.createImageData(ow,oh),d=img.data;
 for(i=0;i<ow*oh;i++){d[i*4]=CC.bg[0];d[i*4+1]=CC.bg[1];d[i*4+2]=CC.bg[2];d[i*4+3]=255;}
 for(y=0;y<oh;y++){for(x=0;x<ow;x++){q=y*ow+x;o=q*4;
  v=elD[q];
  if(v){var k=rl(v);
   if(k===1)blend(d,o,CC.e1,0.25);else if(k===2)blend(d,o,CC.e2,0.25);else blend(d,o,CC.mid,0.35);
   if(k&&isBorder(elD,ow,oh,x,y,v,2)){blend(d,o,k===1?CC.e1:CC.e2,1);if(k===1)n1++;else n2++;}
   else if(!k&&isBorder(elD,ow,oh,x,y,v,1))blend(d,o,CC.common,0.7);}
  if(itD[q]){blend(d,o,col,0.85);
   if(x<1||y<1||x+1>=ow||y+1>=oh||!itD[q-1]||!itD[q+1]||!itD[q-ow]||!itD[q+ow])blend(d,o,dark,1);}}}
 g.putImageData(img,0,0);cv.setAttribute('data-e1',String(n1));cv.setAttribute('data-e2',String(n2));}
function cropOf(e,b){if(!b||!b.n)return null;   /* the box of THIS product's pixels + 20 % (>= 10 px), cut by THIS product's map; null = show the whole map */
 var mg=Math.max(10,Math.round(0.2*Math.max(b.x1-b.x0+1,b.y1-b.y0+1))),x0=Math.max(0,b.x0-mg),y0=Math.max(0,b.y0-mg),x1=Math.min(e.w,b.x1+1+mg),y1=Math.min(e.h,b.y1+1+mg);
 return (x1-x0>=1&&y1-y0>=1)?[x0,y0,x1,y1]:null;}
function paintDetail(p,L){var root=document.documentElement,es={A:DEC['A|'+L],B:DEC['B|'+L]},drawn=0,cnt={A:0,B:0},boxes={A:[],B:[]},ol={A:[0,0],B:[0,0]};
 ['A','B'].forEach(function(r){var e=es[r],cv=$('dcv-'+r);if(!cv)return;var note=noteOf(cv);note.innerHTML='';
  if(!e||!e.w){cv.hidden=true;note.textContent='이 제품에는 이 레이어 지도가 없습니다';return;}
  if(!e.ok){cv.hidden=true;note.innerHTML=failList(r,L);return;}
  var b=bbox(e,r,p.id),c=DS.zoom?cropOf(e,b):null,x0=0,y0=0,x1=e.w,y1=e.h;cnt[r]=b.n||0;
  if(c){x0=c[0];y0=c[1];x1=c[2];y1=c[3];}
  if(x1-x0<1||y1-y0<1){x0=0;y0=0;x1=Math.max(1,e.w);y1=Math.max(1,e.h);c=null;}   /* never a zero or negative canvas */
  drawCrop(cv,e,r,p,x0,y0,x1,y1);drawn++;boxes[r]=[x0,y0,x1,y1];ol[r]=[cv.getAttribute('data-e1'),cv.getAttribute('data-e2')];
  var sc=MP.sides[r].sc,mm=e.m.it||e.m.el,bk=(mm&&mm.b)||1;
  note.textContent=(b.n?'':'이 제품에는 이 쌍의 영역이 없습니다 · ')+(c?'확대 ':'전체 ')+(x1-x0)+' × '+(y1-y0)+' px'+(sc?' (≈ '+fnum((x1-x0)*bk*sc.p)+' × '+fnum((y1-y0)*bk*sc.p)+' µm)':'');});
 var box=boxes.A;root.setAttribute('data-dp-boxb',boxes.B.join(','));
 root.setAttribute('data-dp-drawn',String(drawn));root.setAttribute('data-dp-pixels',cnt.A+','+cnt.B);root.setAttribute('data-dp-box',box.join(','));
 root.setAttribute('data-dp-layer',L);root.setAttribute('data-dp-zoom',DS.zoom?'1':'0');
 root.setAttribute('data-dp-outline',ol.A.join('/')+','+ol.B.join('/'));   /* pair-electrode outline pixels drawn: A e1/e2, B e1/e2 */
 mapCheck();}
function drawDetailMaps(){var p=DS.p?PB[DS.p]:null,root=document.documentElement;
 if(!MP||!p||!DS.layer){root.setAttribute('data-dp-drawn','0');return;}
 var gen=++DGEN,L=DS.layer,left=2;colors();
 ['A','B'].forEach(function(r){load(r,L,function(){left--;if(left===0&&gen===DGEN)paintDetail(p,L);});});}
function drawDetail(st,sel){var box=$('s3-detail'),root=document.documentElement;if(!box)return;
 var d=detailOf(st,sel),key=(d.u||'')+'|'+(d.p||'')+'|'+(st.kv.it||'')+'|'+(sel&&!d.u&&!d.p?sel.t+':'+sel.id:'')+(st.flags.print?'|print':'');
 root.setAttribute('data-dp-unit',d.u||'');root.setAttribute('data-dp-pair',d.p||'');
 if(key===DS.key&&box.getAttribute('data-drawn')==='1')return;   /* the same state: keep the panel (chosen layer, zoom, tab, scroll) */
 DS.key=key;if(DS.u!==d.u||DS.p!==d.p){DS.layer=null;DS.zoom=true;DS.tab=d.p?'items':'pairs';}   /* a new candidate or pair: the failure types of the pair (no pair: the pair list) */
 DS.u=d.u;DS.p=d.p;box.setAttribute('data-drawn','1');
 if(!d.u&&!d.p){DGEN++;root.setAttribute('data-dp-drawn','0');
  box.innerHTML=sel&&sel.t==='pair'?'<p class="muted" id="dp-none">없는 쌍입니다.</p>':EMPTY;return;}
 var p=d.p?PB[d.p]:null,b=d.u?BU[d.u]:null,np=b?unitPairs(d.u).length:0;
 box.innerHTML=headHtml(d)+'<div class="dmaps">'+placesHtml(d)+'</div><div class="dtabs" role="tablist">'+
  '<button type="button" class="dtab" role="tab" data-dtab="pairs">이 후보의 쌍 ('+np+')</button>'+
  '<button type="button" class="dtab" role="tab" data-dtab="items">불량 방식'+(p?' — '+esc(p.e.join('–')):'')+'</button>'+
  '<button type="button" class="dtab" role="tab" data-dtab="cond">조건 값</button></div>'+
  '<div class="dtb"><div class="dtp" data-tab="pairs"><h4 class="pt">이 후보의 쌍</h4>'+pairsHtml(d)+'</div>'+
  '<div class="dtp" data-tab="items"><h4 class="pt">불량 방식</h4>'+itemsHtml(d)+'</div>'+
  '<div class="dtp" id="cond-detail" data-tab="cond"><h4 class="pt">조건 값</h4>'+(p?condBlock([p]):'<p class="muted">쌍이 없습니다.</p>')+'</div></div>';
 applyTab();drawDetailMaps();}
function applyTab(){var root=document.documentElement,pr=!!parseHash().flags.print,ps=document.querySelectorAll('#s3-detail .dtp'),ts=document.querySelectorAll('#s3-detail .dtab'),i;   /* one tab shows; #print shows all three in turn */
 for(i=0;i<ps.length;i++)ps[i].hidden=!pr&&ps[i].getAttribute('data-tab')!==DS.tab;
 for(i=0;i<ts.length;i++){var on=ts[i].getAttribute('data-dtab')===DS.tab;ts[i].classList.toggle('on',on);ts[i].setAttribute('aria-selected',on?'true':'false');}
 root.setAttribute('data-dp-tab',DS.tab);}
function markUnit(st,d){var i,els=document.querySelectorAll('.selcard,.unitcell');for(i=0;i<els.length;i++)els[i].classList.remove('selcard','unitcell');
 [st.kv.unit,d.u].forEach(function(u){if(!u)return;var c=$(u);if(c)c.classList.add('selcard');});
 var u=st.kv.unit;if(!u)return;
 var uc=unitCellOf(u);if(uc){var rows=document.querySelectorAll('tr[data-row="corr"]');for(i=0;i<rows.length;i++){if(rows[i].getAttribute('data-el')!==uc.e)continue;
  var cs=rows[i].querySelectorAll('td[data-layer]');for(var j=0;j<cs.length;j++){if(cs[j].getAttribute('data-layer')===uc.l)cs[j].classList.add('unitcell');}}}}
/* the list of 3 shows one kind (the band cell, #k=, or the kind of the open candidate); #print shows both */
function drawKinds(st,sel){var lists=document.querySelectorAll('.klist'),i,k=st.kv.k,u,c,pr=!!st.flags.print;if(!lists.length)return;
 if(!k){u=st.kv.unit||(sel&&sel.t==='unit'?sel.id:'')||(st.kv.cand||'');
  c=u?($(u)||$('rest-line-'+u)||$('rest-pixel-'+u)):null;c=c&&c.closest?c.closest('.klist'):null;k=c?c.getAttribute('data-k'):lists[0].getAttribute('data-k');}
 for(i=0;i<lists.length;i++)lists[i].hidden=!pr&&lists[i].getAttribute('data-k')!==k;
 var ds=document.querySelectorAll('.dir');for(i=0;i<ds.length;i++)ds[i].classList.toggle('on',ds[i].getAttribute('data-k')===k);
 document.documentElement.setAttribute('data-kind',k||'');}
function msel(st,sel){var uc=unitCellOf(st.kv.unit);return sel||(uc?{t:'electrode',id:uc.e,implied:true}:null);}
function cellCards(st){var p=$('cell-cards');if(!p||p.getAttribute('data-has')!=='1')return;var e,l,sel=selOf(st),uc=unitCellOf(st.kv.unit);
 if(sel&&sel.t==='electrode'&&st.kv.layer){e=sel.id;l=st.kv.layer;}else if(uc){e=uc.e;l=uc.l;}else{p.innerHTML='';return;}
 var cs=cardsOfCell(e,l),cc=cs.filter(function(b){return b.ic;}),oth=cs.length-cc.length;
 p.innerHTML='<b>이 칸('+esc(e)+' · '+esc(l)+')의 원인 후보</b> → '+(cc.length?cc.map(function(b){return '<a class="lk" href="#unit='+encodeURIComponent(b.id)+'&to=s3">'+esc(b.title)+'</a>';}).join(' · ')+(oth?' <span class="muted">(카드에 없는 '+oth+' 개는 3 의 "기타" 표)</span>':''):
  cs.length?'<span class="muted">카드에 없는 원인 후보 '+cs.length+' 개 (3 의 "기타" 표)</span>':'<span class="muted">이 칸에 배정된 원인 후보 없음</span>');}
function mapKey(st,sel){return [sel?sel.t+':'+sel.id:'',st.kv.layer||'',st.kv.unit||''].join('|');}
var CNOK='';
function openCno(ms){var d=$('cno-only'),k=ms&&ms.t==='electrode'&&!ms.implied?ms.id:'',rows,i;   /* rexp/T9: a CNO-only electrode chosen from anywhere opens its fold (once per choice) */
 if(!d||k===CNOK)return;CNOK=k;if(!k)return;rows=d.querySelectorAll('tr[data-el]');
 for(i=0;i<rows.length;i++){if(rows[i].getAttribute('data-el')===k){openUp(d);return;}}}
function apply(){var st=parseHash(),sel=selOf(st),pr=related(sel),ms=msel(st,sel),mpr=related(ms);filterRows(st);openCno(ms);drawFind(st);drawPartners(ms);drawDetail(st,sel);drawKinds(st,sel);
 if(st.flags.print){document.body.className='print';var d=document.querySelectorAll('details');for(var i=0;i<d.length;i++)d[i].open=true;}
 var lab=$('sel-label');if(lab)lab.innerHTML=label(sel,st.kv.unit);var clr=$('sel-clear');if(clr)clr.disabled=!(sel||st.kv.unit);
 mark(ms,mpr);markUnit(st,detailOf(st,sel));cellCards(st);drawCond(sel,pr,st);document.documentElement.setAttribute('data-sel',sel?sel.t+':'+sel.id:'');
 document.documentElement.setAttribute('data-unit',st.kv.unit||'');document.documentElement.setAttribute('data-sel-items',String(Object.keys(pr.items).length));
 var mk=mapKey(st,ms);if(mk!==MKEY){MKEY=mk;drawMaps(st,ms);}}   /* the section 2 map is redrawn only when a map state changed (not by search, filter or scroll) */
function openUp(el){for(var n=el;n&&n!==document.body;n=n.parentNode){if(n.tagName==='DETAILS')n.open=true;}}
/* a link with to= (and unit=) scrolls once; the to= is then dropped from the address so a later action does not scroll again.
   "explore" (the old name of the pair view) is the detail panel of 3 */
function target(id){return $(id==='explore'?'s3-detail':id);}
/* rexp/T7: on a wide screen the detail panel sticks beside the list, so a card or pair click must not scroll the page; it scrolls only when the panel is out of view (coming from another section) */
function stayPut(t){if(!t||t.id!=='s3-detail'||(window.innerWidth||document.documentElement.clientWidth)<1100)return false;
 var r=t.getBoundingClientRect(),vh=window.innerHeight||document.documentElement.clientHeight;return r.bottom>0&&r.top<vh;}
function routeScroll(first){var st=parseHash(),to=st.kv.to,u=st.kv.unit,t=null;
 if(to==='s3'&&u)t=$(u)||$('rest-line-'+u)||$('rest-pixel-'+u);
 if(!t&&to)t=target(to);if(stayPut(t))t=null;
 if(!t&&!to&&first&&u&&!selOf(st))t=$(u);   /* R12:2: a later hash change never pulls the page back to the card */
 if(t){openUp(t);t.scrollIntoView({block:'start'});}
 if(to){delete st.kv.to;var h=buildHash(st);history.replaceState(null,'',h==='#'?location.pathname+location.search:h);}}
function go(id){var el=target(id);if(el&&el.scrollIntoView&&!stayPut(el))el.scrollIntoView({block:'start'});}
document.addEventListener('click',function(ev){var t=ev.target;t=t&&t.closest?t.closest('[data-go],[data-href],[data-clear]'):null;if(!t)return;
 if(t.hasAttribute('data-clear')){var st=parseHash();['sel','unit','it','layer','cand'].forEach(function(k){delete st.kv[k];});st.flags.demo=false;setHash(st);return;}
 if(t.hasAttribute('data-go')){ev.preventDefault();go(t.getAttribute('data-go'));return;}
 var h=t.getAttribute('data-href');if(h){var st2=parseHash(),add={kv:{},flags:{}};
  h.replace(/^#/,'').split('&').forEach(function(p){var i=p.indexOf('=');if(i>0){var v=p.slice(i+1);try{v=decodeURIComponent(v);}catch(e){}add.kv[p.slice(0,i)]=v;}});
  if(add.kv.sel!=null){['cand','it'].forEach(function(k){if(add.kv[k]==null)delete st2.kv[k];});}   /* a new selection drops the candidate and item of the old one */
  Object.keys(add.kv).forEach(function(k){st2.kv[k]=add.kv[k];});if(add.kv.sel!=null)st2.flags.demo=false;
  if(t.hasAttribute('data-cell')&&add.kv.sel&&add.kv.layer){   /* a cell of 2: pick the cause candidate that owns it (none: the line says so) */
   var cs=cardsOfCell(add.kv.sel.replace(/^electrode:/,''),add.kv.layer);if(cs.length)st2.kv.unit=cs[0].id;else delete st2.kv.unit;}
  setHash(st2);if(t.hasAttribute('data-scroll'))go(t.getAttribute('data-scroll'));}});
document.addEventListener('click',function(ev){var t=ev.target;if(!t||!t.closest)return;
 var fb=t.closest('[data-f="more"]');if(fb){var fs=parseHash();fs.kv.more='1';setHash(fs);return;}
 var zb=t.closest('.zf');if(zb){var zt=zb.closest('table').querySelector('tbody.zrows'),n0=zb.getAttribute('data-n');zt.hidden=!zt.hidden;zb.textContent='값 0 인 크기 '+n0+(zt.hidden?'개 펼치기':'개 접기');return;}
 var cv=t.closest('canvas.mapcv');if(cv){onMapClick(cv,ev);return;}
 if(t.closest('#dp-zoom')){DS.zoom=!DS.zoom;var zbtn=$('dp-zoom');if(zbtn)zbtn.textContent=zoomText();drawDetailMaps();return;}
 var lc=t.closest('#dp-layer .lch');if(lc){DS.layer=lc.getAttribute('data-l');   /* a layer chip of the places row */
  var chips=document.querySelectorAll('#dp-layer .lch'),ci;for(ci=0;ci<chips.length;ci++)chips[ci].classList.toggle('on',chips[ci]===lc);
  var pls=document.querySelectorAll('.dplay');for(ci=0;ci<pls.length;ci++)pls[ci].textContent=DS.layer;drawDetailMaps();return;}
 var dt=t.closest('.dtab');if(dt){DS.tab=dt.getAttribute('data-dtab');applyTab();return;}   /* the three tabs of the detail panel */
 if(t.closest('.dpr[data-dp]')){DS.tab='items';applyTab();}   /* a pair row (even the open one) shows its failure types */
 var tw=t.closest('.crow .tw');if(tw){tw.closest('.crow').classList.toggle('open');return;}   /* ▸ unfolds the card in place */
 var dirc=t.closest('.dir');if(dirc){var ks=parseHash();ks.kv.k=dirc.getAttribute('data-k');setHash(ks);return;}   /* the band switches the list */
 var card=t.closest('.s3cards .crow');   /* a click on a card row (not on its links, buttons, folds or unfolded part) opens its detail */
 if(card&&!t.closest('a,button,summary,details,input,select,.cexp')&&UNIT.test(card.id)&&BU[card.id]){var cs2=parseHash();cs2.kv.sel='unit:'+card.id;delete cs2.kv.cand;delete cs2.kv.it;cs2.flags.demo=false;setHash(cs2);}});
document.addEventListener('keydown',function(ev){var t=ev.target;if((ev.key==='Enter'||ev.key===' ')&&t&&t.getAttribute&&(t.getAttribute('data-href')||t.classList.contains('dir')||t.classList.contains('tw'))){ev.preventDefault();t.click();}});
var qi=$('el-q');if(qi)qi.addEventListener('input',function(){var st=parseHash();if(qi.value)st.kv.eq=qi.value;else delete st.kv.eq;
 var h=buildHash(st);history.replaceState(null,'',h==='#'?location.pathname+location.search:h);apply();});
var pqi=$('pf-q');if(pqi)pqi.addEventListener('input',function(){var st=parseHash();if(pqi.value)st.kv.q=pqi.value;else delete st.kv.q;delete st.kv.more;
 var h=buildHash(st);history.replaceState(null,'',h==='#'?location.pathname+location.search:h);apply();});
var ca=$('cond-all');if(ca)ca.addEventListener('toggle',function(){apply();});
window.addEventListener('hashchange',function(){apply();routeScroll(false);});
apply();routeScroll(true);document.documentElement.setAttribute('data-ready','1');
})();
"""
