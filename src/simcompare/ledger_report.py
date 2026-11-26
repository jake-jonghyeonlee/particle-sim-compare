"""ledger-report: the merged comparison report of one `ledger` run (`ledger_v1` -> `ledger_report.html`, `ledger_report_v2`).

Input: `ledger.json` (+ the `execution.json` of the `ledger` run), optionally the `ledger_maps_v2` folder (`--maps-dir`) and the
folder of the 07 report (`--report07-dir`). Nothing is recalculated: every number is a `ledger.json` / `ledger_maps.json`
value; the report only converts formats (% of the reference vi, %p) and adds up the values of the rows it folds.

One data bundle (`<script type="application/json" id="report-data">`), a fixed bar with one global selection kept in the location
hash, and five sections: 1 summary, 2 electrode correspondence, 3 exploration (maps, pair list, location cards), 4 electrode
ledger and layer x size, 5 evidence. The page is plain HTML plus a script: sections 1, 4 and 5 read without the script; the script
marks the selected rows (`.sel`), draws the maps, the pair detail and the condition table of the selected pair. The location hash
is untrusted input: the script accepts only the four selection kinds (pair, electrode, layer, roi) and never puts a hash or data
value into an attribute name.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .compare2 import _json_text
from .ledger_report_assets import CSS, SCRIPT
from .numeric_schema import NumericContractError

SCHEMA_VERSION = 'ledger_report_v2'
LEDGER_SCHEMA = 'ledger_v1'
MAPS_SCHEMA = 'ledger_maps_v2'
COMMAND = 'ledger-report'
KINDS = ('line', 'pixel')
HTML_NAME = 'ledger_report.html'
OWNED_NAMES = {HTML_NAME, 'report.json', 'execution.json', 'error.json'}
TMP_PREFIX = '.ledger-report-'
DEFAULT_TOP = 30
HTML_WARN_BYTES = 8 * 1024 * 1024
LIST_TOP = 5
MODE_LABELS = {'lateral_short': '측면', 'vertical_short': '상하', 'lateral_open': 'CNO'}
REASON_LABELS = {'ledger_totals_check_failed': '합계 검산이 맞지 않습니다(ledger 의 checks 를 확인하세요)',
                 'ledger_summary_check_failed': '원인 후보·해상도 비교·집중도 검산이 맞지 않습니다(ledger 의 checks.bundles · rebase · concentration 을 확인하세요)'}
OUTDATED_HINT = '08 을 새 번들로 다시 돌리세요(ledger → ledger-maps → ledger-report)'
CATS = (('same', '같은 불량의 증감', '양쪽에 있는 item 의 B−A 합'),
        ('new', '새로 생긴 불량 방식', 'B 에만 있는 item 의 B 합 (+)'),
        ('gone', '없어진 불량 방식', 'A 에만 있는 item 의 A 합 (−)'),
        ('cno', 'CNO', 'pixel 전극 접촉 열림 · 전극 하나의 값'))
CHECK_TEXT = {'passed': '✓ 통과', 'failed': '✗ 실패', 'not_evaluated': '확인 안 함'}
PRODUCT_NAMES = (('reference', '기준 A'), ('target', '대상 B'))
PAIR_REASON_TEXT = {'builtin:GH': '코드 규칙: GH 포함', 'builtin:N_NODE': '코드 규칙: N_NODE 포함',
                    'builtin:VBIAS-VINT1': '코드 규칙: VBIAS-VINT1', 'pairs_csv:excluded': 'pairs.csv: excluded'}


# ---- formatting -----------------------------------------------------------------------------------------------------

def esc(value):
    return html.escape('' if value is None else str(value), quote=True)


def muted(text):
    return '<span class="muted">%s</span>' % esc(text)


def num(value):
    if value is None:
        return '대기'
    return '0' if value == 0 else '%.4g' % value


def pct_of(value, vi_a):
    """Display only: 100 x value / vi_a. Null for a null part or an exactly 0 reference."""
    if value is None or vi_a is None or vi_a == 0:
        return None
    return 100.0 * (value / vi_a)


def pct_txt(p):
    if p == 0:
        return '0%'
    a = abs(p)
    return '%.1f%%' % p if a >= 10 else ('%.2f%%' % p if a >= 0.1 else '%.3g%%' % p)


def spct(p):
    if p == 0:
        return '0%'
    return ('+' if p > 0 else '−') + pct_txt(abs(p))


def raw_pct(v):
    """vi values are fractions; the report writes them as % (07 does the same)."""
    return '%.4f%%' % (v * 100)


def raw_pp(v):
    return ('+' if v > 0 else '−') + '%.4f%%p' % abs(v * 100)


def sub(text):
    return '<span class="sub2">%s</span>' % esc(text)


def chip(kind):
    return '<span class="chip" style="background:var(--%s)"></span>' % (kind if kind in KINDS else 'common')


def osum(values):
    """Exact sum with the ledger rule (ledger.py): one null part makes the whole sum null (대기)."""
    values = list(values)
    return None if any(v is None for v in values) else math.fsum(values)


def vi_txt(value):
    return '대기' if value is None else ('0%' if value == 0 else raw_pct(value))


def pp_txt(v):
    return '대기' if v is None else ('0%p' if v == 0 else raw_pp(v))


def pp_cell(delta, pct):
    """A change: sign + %p (big), 'A 대비 %' below. Null = 대기, exactly 0 = 0%p."""
    if delta is None:
        return muted('대기')
    if delta == 0:
        return '0%p'
    cls = 's-up' if delta > 0 else 's-down'
    out = '<span class="sg %s">%s</span>' % (cls, esc(raw_pp(delta)))
    return out + (sub('A 대비 ' + spct(pct)) if pct is not None else '')


def _json_embed(value):
    """JSON for a <script type="application/json"> block: no `<` is left, so `</script>` (or `<!--`) can never appear."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True).replace('<', '\\u003c')


def _href(sel):
    return '#sel=' + quote(sel, safe=':')


# ---- reading the inputs -----------------------------------------------------------------------------------------------

def _read_ledger(ledger_dir):
    """(document, sha256, execution) or NumericContractError ledger_input_missing / ledger_schema_mismatch /
    ledger_report_input_outdated (no `kinds.<kind>.item_breakdown`: the ledger was made before mrep/T1)."""
    folder = Path(ledger_dir)
    path = folder / 'ledger.json'
    if not path.is_file():
        raise NumericContractError('ledger_input_missing', f'{path}: ledger.json 이 없습니다. 먼저 ledger 를 실행하세요')
    execution = None
    exec_path = folder / 'execution.json'
    if exec_path.is_file():
        try:
            execution = json.loads(exec_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise NumericContractError('ledger_input_missing', f'{exec_path}: execution.json 을 읽을 수 없습니다')
        if not isinstance(execution, dict) or execution.get('status') == 'error' or execution.get('exit_code') == 2:
            raise NumericContractError('ledger_input_missing', f'{exec_path}: ledger 가 오류로 끝났습니다')
    if (folder / 'error.json').is_file():
        raise NumericContractError('ledger_input_missing', f'{folder / "error.json"}: ledger 가 오류로 끝난 폴더입니다')
    raw = path.read_bytes()
    try:
        doc = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise NumericContractError('ledger_input_missing', f'{path}: ledger.json 을 읽을 수 없습니다')
    if not isinstance(doc, dict) or doc.get('schema_version') != LEDGER_SCHEMA:
        found = doc.get('schema_version') if isinstance(doc, dict) else None
        raise NumericContractError('ledger_schema_mismatch', f'{path}: schema_version {found!r} (필요 {LEDGER_SCHEMA})')
    kinds = doc.get('kinds')
    if not isinstance(kinds, dict) or any(not isinstance((kinds.get(kind) or {}).get('item_breakdown'), dict) for kind in KINDS):
        raise NumericContractError('ledger_report_input_outdated',
                                   f'{path}: kinds.<kind>.item_breakdown 이 없습니다. {OUTDATED_HINT}')
    return doc, hashlib.sha256(raw).hexdigest(), execution


# the PNG names ledger-maps writes (one folder, no path separator, no ..)
MAP_FILE_NAME = re.compile(r'map_(reference|target)_\d{3}_(el|it)\.png')


def _read_maps(maps_dir, ledger_sha):
    """{'doc', 'sha'} of a ledger-maps folder, or NumericContractError ledger_maps_input_missing /
    ledger_maps_schema_mismatch (not ledger_maps_v2) / ledger_maps_stale (made from another ledger.json). The map PNGs are
    read once here and embedded once (base64) in the page; the script draws them."""
    folder = Path(maps_dir)
    path = folder / 'ledger_maps.json'
    if not path.is_file():
        raise NumericContractError('ledger_maps_input_missing', f'{path}: ledger_maps.json 이 없습니다. 먼저 ledger-maps 를 실행하세요')
    if (folder / 'error.json').is_file():
        raise NumericContractError('ledger_maps_input_missing', f'{folder / "error.json"}: ledger-maps 가 오류로 끝난 폴더입니다')
    raw = path.read_bytes()
    try:
        doc = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise NumericContractError('ledger_maps_input_missing', f'{path}: ledger_maps.json 을 읽을 수 없습니다')
    if not isinstance(doc, dict) or doc.get('schema_version') != MAPS_SCHEMA:
        found = doc.get('schema_version') if isinstance(doc, dict) else None
        raise NumericContractError('ledger_maps_schema_mismatch',
                                   f'{path}: schema_version {found!r} (필요 {MAPS_SCHEMA}). {OUTDATED_HINT}')
    recorded = (doc.get('inputs') or {}).get('ledger_json_sha256')
    if recorded != ledger_sha:
        raise NumericContractError('ledger_maps_stale', f'{path}: 다른 ledger.json 으로 만든 지도입니다(지도 {recorded}, 현재 {ledger_sha}). '
                                                      'ledger-maps 를 다시 실행하세요')
    pngs = {}
    for role in ('reference', 'target'):
        for entry in ((doc.get('sides') or {}).get(role) or {}).get('maps') or []:
            name = entry.get('file') if isinstance(entry, dict) else None
            if not isinstance(name, str) or not MAP_FILE_NAME.fullmatch(name):
                raise NumericContractError('ledger_maps_input_missing', f'{folder}: 지도 파일 이름 {name!r} 이 폴더 직속 map_*.png 가 아닙니다')
            if not (folder / name).is_file():
                raise NumericContractError('ledger_maps_input_missing', f'{folder / name}: 지도 PNG 가 없습니다')
            pngs[name] = (folder / name).read_bytes()
    return {'doc': doc, 'sha': hashlib.sha256(raw).hexdigest(), 'pngs': pngs}


def _report07_link(report07_dir, out):
    """07 link: {'status': 'none' | 'missing' | 'linked', 'href'} — a path relative to the output folder."""
    if report07_dir is None:
        return {'status': 'none', 'href': None}
    target = Path(report07_dir) / 'report.html'
    if not target.is_file():
        return {'status': 'missing', 'href': None}
    try:
        rel = os.path.relpath(str(target.resolve()), str(Path(out).resolve()))
    except ValueError:      # Windows: another drive has no relative path; the report is still made, without the link
        return {'status': 'missing', 'href': None, 'reason': 'no_relative_path'}
    rel = rel.replace(os.sep, '/')
    if re.match(r'^[A-Za-z][A-Za-z0-9+.\-]*:', rel):     # a scheme would make the href run code instead of opening a file
        return {'status': 'missing', 'href': None, 'reason': 'scheme'}
    return {'status': 'linked', 'href': quote(rel, safe='/')}


# ---- context ------------------------------------------------------------------------------------------------------------

class Ctx(object):
    """Lookup tables of one report."""

    def __init__(self, doc, top_pairs, top_electrodes, maps=None, report07=None):
        self.doc = doc
        self.top_pairs, self.top_electrodes = top_pairs, top_electrodes
        self.maps = maps
        self.report07 = report07 or {'status': 'none', 'href': None}
        self.change_labels = {c['code']: c['label'] for c in doc['change_kinds']}
        self.vi_a = {kind: doc['kinds'][kind]['vi_a'] for kind in KINDS}
        self.pair_by_id = {p['pair_id']: p for p in doc['pairs']}
        # electrodes that belong to a failure kind: a calculated pair of that kind (pixel: or a CNO row). The others are 해당 없음.
        self.kind_electrodes = {kind: set() for kind in KINDS}
        for row in doc['pairs']:
            if row['kind'] in KINDS:
                self.kind_electrodes[row['kind']].update(row['electrodes'])
        self.kind_electrodes['pixel'] |= {r['electrode_id'] for r in doc.get('cno') or []}
        # E10: the layer order of every list (inputs.layer_order; absent = by name, as before)
        raw_order = (doc.get('inputs') or {}).get('layer_order')
        self.layer_order = [n for n in raw_order if isinstance(n, str)] if isinstance(raw_order, list) else []
        rank = {name: i for i, name in enumerate(self.layer_order)}
        self.lk = lambda name: (rank.get(name, len(rank)), str(name))
        # the narrative summary of ledger.py (rnarr/T5); an older ledger has none of it (E8: the text is left out)
        self.summary = doc.get('summary') if isinstance(doc.get('summary'), dict) else {}
        self.bundles = self.summary.get('bundles') if isinstance(self.summary.get('bundles'), dict) else None
        # rnarr/T12: `summary.bundles.version == 2` is the direction reading (net change, direction shares); a ledger without it
        # is the older gross (|delta|) reading, shown with its old meaning and the rerun hint (L5)
        self.v2 = bool(self.bundles) and self.bundles.get('version') == 2
        self.bundle_by_id = {b['id']: b for b in (self.bundles or {}).get('list') or []}
        self.design = self.summary.get('design') if isinstance(self.summary.get('design'), dict) else None
        self.rebase = self.summary.get('rebase') if isinstance(self.summary.get('rebase'), dict) else None
        self.conc = self.summary.get('concentration') if isinstance(self.summary.get('concentration'), dict) else None
        occ = maps['doc'].get('occurrence') if maps is not None else None
        self.occ = occ if isinstance(occ, dict) else None
        self.occ_by_item = {row['item_id']: row for row in (self.occ or {}).get('items') or []}
        self.item_by_id = {item['item_id']: item for p in doc['pairs'] for item in p.get('items') or []}

    def sort_layers(self, names):
        return sorted(names, key=self.lk)

    def pct(self, value, kind):
        return pct_of(value, self.vi_a[kind])

    def change_badge(self, code):
        cls = {'new_pair': ' b-new', 'gone_pair': ' b-gone', 'layer_change': ' b-mv', 'value_pending': ' b-mv'}.get(code, '')
        return '<span class="badge%s">%s</span>' % (cls, esc(self.change_labels.get(code, code)))

    def crp(self):
        return (self.doc['totals']['pixel'].get('crp') or {})

    def crp_differs(self):
        return bool(self.crp().get('present'))

    def crp_pp(self):
        """The CRP difference in %p; 대기 when the ledger has no value (never a made-up 0)."""
        return pp_txt(self.crp().get('delta'))


def _item_failed(ctx):
    checks = (ctx.doc.get('checks') or {}).get('item_breakdown') or {}
    states = [(checks.get('pair') or {}).get('status')] + [((checks.get('kind') or {}).get(kind) or {}).get('status') for kind in KINDS]
    return 'failed' in states


def _pair_exclusion_rows(doc):
    """[(product name, electrodes, pair_id, reason, actual cases, expected-only cases, layers)] of ledger.json
    inputs.pair_exclusions; one row per entry of a block's `pairs` (empty when the key is absent)."""
    blocks = (doc.get('inputs') or {}).get('pair_exclusions') or {}
    rows = []
    for role, name in PRODUCT_NAMES:
        block = blocks.get(role)
        if not isinstance(block, dict):
            continue
        presence = {}
        for case in block.get('cases') or []:
            counts = presence.setdefault((tuple(case.get('electrodes') or ()), case.get('reason')), [0, 0])
            counts[0 if case.get('presence') == 'discovered' else 1] += 1
        for pair in block.get('pairs') or []:
            electrodes = tuple(pair.get('electrodes') or ())
            actual, expected = presence.get((electrodes, pair.get('reason')), (pair.get('cases') or 0, 0))
            rows.append((name, electrodes, pair.get('pair_id'), pair.get('reason'), actual, expected,
                         list(pair.get('analysis_layers') or [])))
    return rows


# ---- bands ----------------------------------------------------------------------------------------------------------------

def _head_band(ctx):
    doc = ctx.doc
    if doc.get('status') != 'partial':
        return ''
    reasons = ''.join('<li>%s <span class="muted">(%s)</span></li>' % (esc(REASON_LABELS.get(r, r)), esc(r))
                      for r in doc.get('reasons') or ['partial'])
    failed = [name for name, e in (doc.get('checks', {}).get('equations') or {}).items() if e.get('status') == 'failed']
    extra = '<p>맞지 않는 합계식: %s</p>' % esc(', '.join(failed)) if failed else ''
    return ('<div class="band partial" id="partial"><b>부분 결과</b> — ledger 가 partial 로 끝났습니다. '
            '표의 숫자를 그대로 믿지 말고 사유를 확인하세요.<ul>%s</ul>%s</div>' % (reasons, extra))


def _bands(ctx, size_warning):
    out = [_head_band(ctx)]
    if _item_failed(ctx):
        out.append('<div class="band partial" id="band-item-breakdown"><b>item 분해 검산 실패</b> — ledger 의 checks.item_breakdown 이 '
                   '맞지 않습니다. 4 결과의 분해 숫자를 그대로 믿지 마세요(부록에 검산이 있습니다).</div>')
    if ctx.maps is None:
        out.append('<div class="band info" id="band-no-maps"><b>지도 없음</b> — <code>--maps-dir</code> 를 주지 않아 지도 자리가 '
                   '비어 있습니다. ledger-maps 를 실행한 뒤 다시 만드세요.</div>')
    else:
        mdoc = ctx.maps['doc']
        if mdoc.get('status') == 'partial':
            out.append('<div class="band partial" id="maps-partial"><b>지도 부분 결과</b> — ledger-maps 가 partial 로 끝났습니다(%s). '
                       '지도·카드의 값을 그대로 믿지 마세요.</div>' % esc(', '.join(mdoc.get('reasons') or ['partial'])))
    if size_warning:
        out.append('<div class="band info" id="band-size"><b>큰 보고서</b> — HTML 이 8 MB 를 넘습니다. 열기가 느리면 '
                   'ledger-maps 의 지도 크기 옵션을 줄여 다시 실행하세요.</div>')
    return ''.join(out)


# ---- section 1 --------------------------------------------------------------------------------------------------------------

def _kpi(ctx, kind):
    kd = ctx.doc['kinds'][kind]
    d = ctx.doc['totals'][kind]['delta']
    big = 'up' if d and d > 0 else ('dn' if d and d < 0 else '')
    big_txt = muted('대기') if d is None else esc('0%p' if d == 0 else raw_pp(d))
    pct = ctx.pct(d, kind)
    sub_txt = '대기' if d is None else ('A 대비 ' + (spct(pct) if pct is not None else '— (A 가 0)'))
    return ('<div class="kpi" data-kind="%s"><div class="k muted">%s%s vi</div><div class="ab">A %s → B %s</div>'
            '<div class="big %s">%s</div><div class="muted">%s <span class="muted">· 기준: 각 종류의 A vi</span></div></div>' % (
                kind, chip(kind), kind, esc(vi_txt(kd['vi_a'])), esc(vi_txt(kd['vi_b'])), big, big_txt, esc(sub_txt)))


def _size_value(value):
    return '%g' % value if isinstance(value, (int, float)) else str(value)


def _sizes_text(values):
    """Same rule as the 07 report: up to 8 values listed, else `a–b µm, n개`."""
    values = sorted(v for v in values if isinstance(v, (int, float)))
    if not values:
        return None
    if len(values) > 8:
        return '%s–%s µm, %d개' % (_size_value(values[0]), _size_value(values[-1]), len(values))
    return ', '.join(_size_value(v) for v in values) + ' µm'


def _size_range_parts(doc):
    """(text, with_07_hint) of the particle size range recorded in ledger.json `inputs.diameter_filter`."""
    flt = (doc.get('inputs') or {}).get('diameter_filter')
    if not isinstance(flt, dict):
        return '이물 크기 범위 정보 없음 — 08 을 다시 실행하면 나옵니다', False
    if flt.get('mode') != 'listed':
        return '이물 크기 범위: 모든 크기', False
    listed = _sizes_text(flt.get('diameters_um') or [])
    text = '이물 크기 범위: 선택 %s' % (listed or '(목록 없음)')
    missing = _sizes_text(flt.get('not_observed_um') or [])
    if missing:
        text += ' (관찰 안 됨: %s)' % missing
    return text, True


def _size_range_line(ctx):
    text, hint = _size_range_parts(ctx.doc)
    tail = ''
    if hint:
        link = ctx.report07
        tail = ' (전체 크기 값은 %s에서 봅니다)' % (
            '<a href="%s">07 1절</a>' % esc(link['href']) if link['status'] == 'linked' else '07 1절')
    return '<p class="muted size-range" id="size-range">%s%s</p>' % (esc(text), tail)


def _pair_count(ctx, kind, cat):
    """Pairs of a kind that have at least one item of the category (counts of pairs[].breakdown)."""
    if cat == 'cno':
        return len(ctx.doc['cno']) if kind == 'pixel' else 0
    key = {'same': 'n_both', 'new': 'n_new', 'gone': 'n_gone'}[cat]
    return sum(1 for p in ctx.doc['pairs'] if p['kind'] == kind and ((p.get('breakdown') or {}).get(key) or 0) > 0)


def _breakdown_cell(ctx, kind, cat):
    if cat == 'cno' and kind == 'line':
        return '<td class="l muted">해당 없음</td>'
    brk = ctx.doc['kinds'][kind]['item_breakdown']
    value = brk.get(cat)
    # CNO is no pair: it points to the electrode ledger (5); the other rows are plain numbers
    href = ' data-go="s5" title="CNO 는 쌍이 아니라 5 분포의 전극 장부에서 봅니다"' if cat == 'cno' else ''
    if value is None:
        return '<td class="l muted"%s>대기</td>' % href
    if cat == 'gone':
        value = -value
    count = _pair_count(ctx, kind, cat)
    unit = '줄' if cat == 'cno' else '쌍'
    if value == 0:
        return '<td class="l muted"%s>0%%p</td>' % href
    return '<td class="l"%s>%s%s</td>' % (href, pp_cell(value, ctx.pct(value, kind)), sub('%d%s' % (count, unit)))


def _breakdown_table(ctx):
    rows = []
    for cat, label, hint in CATS:
        rows.append('<tr data-cat="%s"><td class="l" title="%s">%s%s</td>%s%s</tr>' % (
            cat, esc(hint), esc(label), sub(hint), _breakdown_cell(ctx, 'line', cat), _breakdown_cell(ctx, 'pixel', cat)))
    total = []
    for kind in KINDS:
        d = ctx.doc['totals'][kind]['delta']
        total.append('<td class="l">%s</td>' % pp_cell(d, ctx.pct(d, kind)))
    rows.append('<tr class="sum"><td class="l">합 = 종류 전체 Δ</td>%s</tr>' % ''.join(total))
    note = ''
    if ctx.crp_differs():
        note = ('<p class="muted" id="crp-note" style="margin:3px 0 0">CRP 차이 %s 는 부록 참고 — 두 제품의 CRP 가 달라 pixel 합에는 분해에 없는 CRP 줄이 더해집니다.</p>' %
                esc(ctx.crp_pp()))
    return ('<table id="breakdown"><thead><tr><th class="l">범주</th><th class="l">%sline</th><th class="l">%spixel</th></tr></thead><tbody>%s</tbody></table>'
            '%s<p class="muted" style="margin:3px 0 0">item = 불량 방식 × 양 끝 레이어. 쌍의 불량 구성이 달라도 이 분해에는 모두 들어갑니다.</p>' % (
                chip('line'), chip('pixel'), ''.join(rows), note))


def _comp_text(pair):
    comp = pair.get('composition') or {}
    added, removed = comp.get('added') or [], comp.get('removed') or []
    if pair.get('change_kind') != 'layer_change' or not (added or removed):
        return ''
    inner = ' '.join('+' + a['text'] for a in added)
    if added and removed:
        inner += ' / '
    inner += ' '.join('−' + r['text'] for r in removed)
    return sub('item +%d / −%d (%s)' % (len(added), len(removed), inner))


def _pair_li(ctx, pair, value_html):
    name = '–'.join(pair['electrodes'])
    return ('<li data-pair="%s" data-href="%s" data-scroll="explore" tabindex="0"><span class="n">%s</span><span><b>%s</b> %s%s</span><span class="v">%s</span></li>' % (
        esc(pair['pair_id']), esc(_href('pair:' + pair['pair_id'])), esc(pair.get('rank') or '—'), esc(name),
        ctx.change_badge(pair['change_kind']), _comp_text(pair), value_html))


def top_changed(doc, kind, n=LIST_TOP):
    """Pairs of a kind by |change| as % of the A vi (pairs with a value only); ties by pair_id."""
    rows = [p for p in doc['pairs'] if p['kind'] == kind and p.get('pct_delta') is not None]
    return sorted(rows, key=lambda p: (-abs(p['pct_delta']), p['pair_id']))[:n]


def top_risk(doc, kind, n=LIST_TOP):
    """Pairs of a kind by their B vi as % of the A vi (pairs present in B with a value only)."""
    rows = [p for p in doc['pairs'] if p['kind'] == kind and p['present_b'] and p.get('pct_b') is not None]
    return sorted(rows, key=lambda p: (-p['pct_b'], p['pair_id']))[:n]


def top_signed(doc, kind, worse, n=LIST_TOP):
    """Pairs of a kind with a value, worse (vi up) or better (vi down) first by |change|; ties by pair_id."""
    rows = [p for p in doc['pairs'] if p['kind'] == kind and isinstance(p.get('delta'), (int, float)) and
            (p['delta'] > 0 if worse else p['delta'] < 0)]
    return sorted(rows, key=lambda p: (-abs(p['delta']), p['pair_id']))[:n]


def _lists(ctx):
    """4 lists: line and pixel x worse (vi up) and better (vi down), the 5 pairs of the biggest change of the pair total."""
    out = []
    for kind in KINDS:
        for worse, key, text in ((True, 'w', '악화(vi 증가)'), (False, 'b', '개선(vi 감소)')):
            rows = top_signed(ctx.doc, kind, worse)
            out.append('<div><div class="muted">%s%s · %s 상위 5 쌍</div><ul class="lst" id="%s-%s">%s</ul></div>' % (
                chip(kind), kind, text, key, kind, ''.join(
                    _pair_li(ctx, p, pp_cell(p['delta'], p.get('pct_delta'))) for p in rows) or '<li class="muted">해당하는 쌍이 없습니다</li>'))
    return '<div class="g2">%s</div>' % ''.join(out)


def _trust(ctx):
    doc = ctx.doc
    checks = doc.get('checks') or {}
    ok = checks.get('status') == 'passed' and not _item_failed(ctx)
    n_excluded = len({tuple(sorted(r[1])) for r in _pair_exclusion_rows(doc)})
    pending = doc['kinds']['change_kind_counts'].get('value_pending', 0)
    partial = doc.get('status') == 'partial' or (ctx.maps is not None and ctx.maps['doc'].get('status') == 'partial')
    state = '<span class="ok">✓ 검산 통과</span>' if ok else '<span class="bad">✗ 검산 확인 필요</span>'
    reduced = ''
    if ctx.maps is not None and (ctx.maps['doc'].get('cards_dropped_for_budget') or []):
        reduced = ' · <b>카드 일부 없음</b>(cards_budget_reduced)'
    return ('<div class="trust" id="trust">%s · 제외 쌍 <b>%d</b> · 대기 <b>%d</b> · 부분 결과: <b>%s</b>%s · <a href="#sA" data-go="sA">부록 근거·검산 보기</a></div>' % (
        state, n_excluded, pending, '예' if partial else '아니오', reduced))


# ---- the narrative frame: the head of a section (question -> summary -> body -> where to look next) ---------------------------

RERUN = '02·08 을 새 번들로 다시 실행하세요.'
SECTION_NAMES = (('s1', '1 전체 요약'), ('s2', '2 제품 대응'), ('s3', '3 원인 후보'), ('s4', '4 결과'), ('s5', '5 분포'),
                 ('s6', '6 해상도 차이 비교'), ('sA', '부록 근거·검산'))
KIND_REASON = {'kind_not_computable': '이 종류의 값을 계산할 수 없습니다', 'atom_null': '값이 대기인 항목이 있습니다'}
TOTAL_TEXT = '총 변동(증가·감소 크기 합)'
# M1: the names of the cause candidate types (older ledgers: new / gone / move are the nearest new names)
TYPE_NAME = {'electrode_new': '전극 신규', 'electrode_removed': '전극 삭제', 'layer_change': '레이어 변경', 'hub': '다중 쌍 변화',
             'pair': '단일 쌍 변화', 'self': '동일 전극', 'cno': 'CNO 단독'}
TYPE_ORDER = ('electrode_new', 'electrode_removed', 'layer_change', 'hub', 'pair', 'self', 'cno')
LEGACY_TYPE = {'new': 'electrode_new', 'gone': 'electrode_removed', 'move': 'layer_change'}
DIR_WORD = {'increase': '증가', 'decrease': '감소'}
WHY_TEXT = {'both': '두 전극 모두 이 레이어의 상태가 바뀌었습니다.',
            'hubs': '두 전극 모두 상대 여럿과 함께 변하고 변한 상대 수가 같아 이 쌍 하나로 묶었습니다.',
            'multi': '전극 3 개 이상이 걸린 불량이라 전극 규칙을 적용하지 않고 이 쌍 하나로 묶었습니다.',
            'both_new_removed': '두 전극이 모두 신규·삭제라서 이 쌍 하나로 묶었습니다.'}
MIXED_WHY = '두 전극 중 한쪽에 맡길 수 없어 이 쌍 하나로 묶었습니다.'
# rexp/T13: how many cause candidates one compartment (one direction of one kind) shows: the balance bar, its legend and the card list all use the same rule
SHOW_MAIN, MIN_MAIN = 7, 5      # the net change direction: at most 7, at least 5
SHOW_OPP, MIN_OPP = 3, 2        # the opposite direction: at most 3, at least 2
SHOW_MIN_SHARE = 0.02           # inside the maximum, a candidate under this share of its compartment ends the list (the minimum is always filled)
FLAT_TOP_SHARE = 0.15           # a compartment whose first candidate holds less than this reads as "evenly spread"


def share_txt(x):
    """A share of the change amount (a fraction) as %: whole numbers from 10 %, one decimal below."""
    if x is None:
        return '—'
    p = x * 100
    return '0%' if p == 0 else ('%d%%' % round(p) if p >= 9.95 else '%.1f%%' % p)


def _hd(number, title, question, sums=None):
    out = '<p class="q">%s</p><h2>%s %s</h2>' % (esc(question), esc(number), esc(title))
    if sums:
        out += '<div class="summary">%s</div>' % ''.join('<p>%s</p>' % s for s in sums)
    return out


def _nx(links):
    """links: [(id, label)] — the pointer at the end of a section."""
    return '<p class="nextl">다음에 볼 곳 → %s</p>' % ' · '.join(
        '<a href="#%s" data-go="%s">%s</a>' % (i, i, esc(label)) for i, label in links)


def _rerun_line(what):
    return '<p class="muted rerun">%s 이 입력에는 없습니다. %s</p>' % (esc(what), RERUN)


def _tiny(value):
    return value is None or abs(value) < 1e-12


def btype(b):
    """The type of a bundle in the version 2 names (an older ledger's new / gone / move are the nearest new names)."""
    t = LEGACY_TYPE.get(b.get('type'), b.get('type'))
    if t == 'hub' and not b.get('hub') and not (b.get('partner_count') or 0):
        return 'self'        # R18:4: an older ledger's one-electrode hub (no partner, no judgement) is the same-electrode type
    return t


def _hub_numbers(b):
    """(partners that changed, increase, decrease) of a hub bundle: the judged partners of `bundle.hub` (R17:3 / T11b);
    an older ledger has no `hub` and keeps the numbers of the bundle."""
    hub = b.get('hub')
    if hub:
        return hub['changed_count'], hub['increase'], hub['decrease']
    atoms = b.get('atoms') or {}
    return b.get('partner_count') or 0, atoms.get('increase') or 0, atoms.get('decrease') or 0


def _layer_change_text(b):
    """'GAT2 추가 · SD1 제거' of a layer change bundle (B only layers added, A only layers removed)."""
    parts = []
    if b.get('to'):
        parts.append('%s 추가' % '·'.join(b['to']))
    if b.get('from'):
        parts.append('%s 제거' % '·'.join(b['from']))
    return ' · '.join(parts) or '레이어 구성'


def bundle_title(b):
    """M1 title of a cause candidate, e.g. 'Scan3 · SD1 — 다중 쌍 변화(8 쌍: 증가 4 · 감소 4)'."""
    t, e, layers = btype(b), b.get('electrode'), '·'.join(b.get('layers') or ([b['layer']] if b.get('layer') else []))
    if t == 'pair':
        return '%s · %s — %s' % ('–'.join(b.get('electrodes') or []), b.get('layer'), TYPE_NAME['pair'])
    if t == 'electrode_new':
        return '%s — B 에 신규(%s)' % (e, layers or '—')
    if t == 'electrode_removed':
        return '%s — B 에서 삭제(%s)' % (e, layers or '—')
    if t == 'layer_change':
        return '%s — 레이어 변경(%s)' % (e, _layer_change_text(b))
    if t == 'hub':
        n, inc, dec = _hub_numbers(b)
        return '%s · %s — %s(%d 쌍: 증가 %d · 감소 %d)' % (e, b.get('layer'), TYPE_NAME['hub'], n, inc, dec)
    return '%s · %s — %s' % (e, b.get('layer'), TYPE_NAME.get(t, t))


def lk_bundle(b, label=None, to='s3'):
    return '<a class="lk" href="#unit=%s&amp;to=%s">%s</a>' % (esc(b['id']), to, esc(label or bundle_title(b)))


def unit_first(b):
    """What changed, in one sentence (the card head)."""
    t, n = btype(b), b.get('partner_count') or 0
    e, layer = b.get('electrode'), b.get('layer')
    layers = '·'.join(b.get('layers') or ([layer] if layer else [])) or '—'
    if t == 'electrode_new':
        return '%s 전극이 B 에 신규로 놓였습니다(%s).' % (e, layers)
    if t == 'electrode_removed':
        return '%s 전극이 B 에서 삭제되었습니다(%s).' % (e, layers)
    if t == 'layer_change':
        return '%s 전극의 레이어 구성이 바뀌었습니다(%s).' % (e, _layer_change_text(b))
    if t == 'hub':
        number, inc, dec = _hub_numbers(b)
        if inc and not dec:        # R17b:2: the verb follows the judged partners (hub.increase / hub.decrease), not the bundle net
            return '%s 전극의 %s 에서 상대 %d 개와의 불량이 함께 증가했습니다.' % (e, layer, number)
        if dec and not inc:
            return '%s 전극의 %s 에서 상대 %d 개와의 불량이 함께 감소했습니다.' % (e, layer, number)
        return '%s 전극의 %s 에서 상대 %d 개와의 불량이 함께 변했습니다(증가 %d · 감소 %d).' % (e, layer, number, inc, dec)
    if t == 'self':
        return '%s 전극 안의 %s 불량이 변했습니다.' % (e, layer)
    if t == 'pair':
        return '%s 쌍의 %s 불량이 변했습니다.' % ('–'.join(b.get('electrodes') or []), layer)
    return '%s 전극의 %s CNO 가 변했습니다.' % (e, layer)


def unit_what(ctx, b):
    t, n = btype(b), b.get('partner_count') or 0
    first = unit_first(b)
    if t in ('electrode_new', 'electrode_removed'):
        return first + ' 모든 레이어의 불량과 CNO 를 이 카드 하나에 넣었습니다(상대 %d 개).' % n
    if t == 'layer_change':
        return first + ' 바뀐 레이어의 불량과 CNO 를 이 카드 하나에 넣었습니다(상대 %d 개).' % n
    if t == 'hub':
        return first + ' 한쪽 전극만 여러 상대와 함께 변해서, 증가한 것과 감소한 것을 모두 그 전극의 이 카드에 넣었습니다.'
    if t == 'self':
        return first + ' 전극이 하나인 불량이라 그 전극에 맡깁니다.'
    if t == 'cno':
        return first + ' 전극 단독 항목이며 그 전극의 레이어 상태는 바뀌지 않았습니다.'
    why = b.get('why') or []
    if len(why) > 1:
        return first + ' ' + MIXED_WHY
    if why == ['nohub']:
        return first + ' 설계가 변한 상대가 %d 개 미만이거나 전체 상대의 %s 미만이어서 이 쌍 하나로 묶었습니다.' % (
            ctx.bundles.get('hub_min') or 3, share_txt(ctx.bundles.get('hub_min_fraction') or 0.5))
    return first + (' ' + WHY_TEXT[why[0]] if why and why[0] in WHY_TEXT else '')


def _unit_badge(b):
    t = btype(b)
    cls = {'electrode_new': ' b-new', 'electrode_removed': ' b-gone', 'layer_change': ' b-mv'}.get(t, '')
    return '<span class="badge%s">%s</span>' % (cls, esc(TYPE_NAME.get(t, t)))


CARD_BADGE = {'electrode_new': '신규', 'electrode_removed': '삭제', 'layer_change': '레이어 변경', 'pair': '쌍', 'hub': '허브', 'self': '동일 전극',
              'cno': 'CNO'}


def _card_name(b):
    """rexp/T12: the short name of a card row: the pair 'A–B'; a hub or same-electrode 'electrode · layer'; the others the electrode."""
    t, e = btype(b), b.get('electrode')
    if t == 'pair':
        return '–'.join(b.get('electrodes') or []) or '—'
    if t in ('hub', 'self'):
        return '%s · %s' % (e, b.get('layer'))
    return str(e) if e else '–'.join(b.get('electrodes') or []) or '—'


def _card_badge(b):
    t = btype(b)
    cls = {'electrode_new': ' b-new', 'electrode_removed': ' b-gone', 'layer_change': ' b-mv'}.get(t, '')
    return '<span class="badge%s">%s</span>' % (cls, esc(CARD_BADGE.get(t, TYPE_NAME.get(t, t))))


def _card_fact(b):
    """rexp/T12: the one-line fact under the name of a card row ('ACT·GAT1 외 3 층 · 상대 12'); the whole sentence is the tooltip and the first
    line of the unfolded part."""
    t, n = btype(b), b.get('partner_count') or 0
    layers = list(b.get('layers') or ([b['layer']] if b.get('layer') else []))
    partners = ' · 상대 %d' % n if n else ''
    if t in ('electrode_new', 'electrode_removed'):
        text = '·'.join(layers[:2]) if layers else '—'
        return text + (' 외 %d 층' % (len(layers) - 2) if len(layers) > 2 else '') + partners
    if t == 'layer_change':
        return _layer_change_text(b) + partners
    if t == 'hub':
        number, inc, dec = _hub_numbers(b)
        return '상대 %d (증가 %d · 감소 %d)' % (number, inc, dec)
    return '·'.join(layers) if layers else str(b.get('layer') or '—')


def _crp_outside(ctx, kind):
    """The CRP change of the pixel kind when it is larger than the negligible limit (summary.rules.negligible), else None.
    The CRP line belongs to no cause candidate (R12:1)."""
    if kind != 'pixel' or not ctx.bundles:
        return None
    value = (ctx.bundles.get('crp') or {}).get('pixel')
    if not isinstance(value, (int, float)):
        return None
    rule = ((ctx.summary.get('rules') or {}).get('negligible')) or {}
    limit = (rule.get('abs') or 0.0) + (rule.get('rel') or 0.0) * abs(ctx.vi_a['pixel'] or 0.0)
    return value if abs(value) > max(limit, 1e-12) else None


def _crp_dominates(ctx, kind):
    """R18:1: the kind's vi change has the opposite sign of its cause candidates' net change (the CRP part is larger than the net).
    Then the 'vi change direction' of the page is the direction of the cause candidates, and the text says so."""
    if not ctx.v2 or not ctx.bundles or kind not in ctx.bundles.get('ranking', {}):
        return False
    rk = ctx.bundles['ranking'][kind]
    main = rk.get('main')
    delta = ctx.doc['totals'][kind]['delta']
    if rk.get('direction') == 'balanced' or main not in DIR_WORD or not isinstance(delta, (int, float)) or delta == 0:
        return False
    return (delta > 0) != (main == 'increase')


def _dir_name(ctx, kind):
    """The name of the ranking direction: 'vi 변화 방향', or '원인 후보 순 변화 방향' when the CRP part turns the vi change around."""
    return '원인 후보 순 변화 방향' if _crp_dominates(ctx, kind) else 'vi 변화 방향'


def _skip_lines(ctx, kind):
    """The H4 sentences of a kind that has no cause candidates to name (None = it has some)."""
    bd = ctx.bundles
    st = (bd.get('status') or {}).get(kind)
    if st == 'not_evaluated':
        why = ' · '.join(KIND_REASON.get(r, r) for r in (bd.get('reasons') or {}).get(kind) or []) or '값이 없습니다'
        return ['%s 원인 후보는 계산할 수 없습니다(%s).' % (kind, esc(why))]
    if st == 'no_change':
        out = ['%s 에는 item 변화가 없습니다.' % kind]
        crp_d = _crp_outside(ctx, kind)
        if crp_d is not None:
            out.append('pixel 변화 %s 는 보정값(CRP) 차이에서 나왔습니다.' % esc(pp_txt(crp_d)))
        return out
    return None


def _cards_of(ctx, ids):
    return [ctx.bundle_by_id[i] for i in ids or [] if i in ctx.bundle_by_id]


def _pick_count(nets, total, is_main):
    """rexp/T13: how many of the candidates of one compartment (largest |net| first) are shown: at most 7 (opposite 3), at least 5 (opposite 2);
    inside the maximum a candidate under 2 % of the compartment ends the list."""
    most, least = (SHOW_MAIN, MIN_MAIN) if is_main else (SHOW_OPP, MIN_OPP)
    n = 0
    for v in nets:
        if n >= most:
            break
        if (abs(v) / abs(total) if total else 0.0) >= SHOW_MIN_SHARE or n < least:
            n += 1
        else:
            break
    return n


def _split(ctx, kind):
    """rexp/T13: the two compartments of a kind of a version 2 ledger (None: older ledger, a kind with no candidates to name).
    main = the net change direction of the ledger, other = the opposite one. For each: `all` (every candidate of that sign, largest |net| first),
    `shown` (the first ones by the display rule), `folded` (the others), `total` (the sum of their net). `zero` = candidates whose net is 0."""
    if not ctx.v2:
        return None
    rk = ctx.bundles['ranking'][kind]
    main = rk.get('main')
    if rk.get('status') != 'ok' or main not in DIR_WORD:
        return None
    other = 'decrease' if main == 'increase' else 'increase'
    out = {'main': main, 'other': other, 'all': {}, 'shown': {}, 'folded': {}, 'total': {}, 'zero': _cards_of(ctx, rk.get('zero'))}
    for name in (main, other):
        d = rk.get(name) or {}
        bs = sorted(_cards_of(ctx, list(d.get('cards') or []) + list(d.get('rest') or [])),
                    key=lambda b: (-abs(b['kinds'][kind]['net'] or 0.0), b['id']))
        total = math.fsum(b['kinds'][kind]['net'] or 0.0 for b in bs)
        n = _pick_count([b['kinds'][kind]['net'] or 0.0 for b in bs], total, name == main)
        out['all'][name], out['shown'][name], out['folded'][name], out['total'][name] = bs, bs[:n], bs[n:], total
    return out


def _cancel_ratio(sp):
    """|the opposite direction sum| / |the net direction sum| (None without a net direction sum)."""
    base = abs(sp['total'][sp['main']])
    return None if base == 0 else abs(sp['total'][sp['other']]) / base


def _ranked_ids(ctx, kind):
    """Every card id of a kind that has a card or a folded card row, the net change direction first: the shown candidates only (an older
    ledger: its one `cards` list)."""
    rk = ctx.bundles['ranking'][kind]
    if not ctx.v2:
        return list(rk.get('cards') or [])
    sp = _split(ctx, kind)
    if sp is None:
        return []
    return [b['id'] for name in (sp['main'], sp['other']) for b in sp['shown'][name]]


def _top_type(ctx, kind, direction):
    """(type, share) of the type that holds most of one direction of a kind; None without a type table or a total."""
    types = ((ctx.bundles.get('types') or {}).get(kind)) or {}
    total = ((ctx.bundles['ranking'][kind].get(direction)) or {}).get('total')
    if not total:
        return None
    best = None
    for name in TYPE_ORDER:
        value = (types.get(name) or {}).get(direction)
        if value and (best is None or abs(value) > abs(best[1])):
            best = (name, value)
    return None if best is None else (best[0], best[1] / total)


def _factor_sentence(ctx, kind, b, direction, label):
    """'가장 큰 감소 요인은 <name>, line −0.1%p(감소 요인의 12%) 입니다.' — one candidate, one sentence (R15:2)."""
    s = b['kinds'][kind]
    share = '' if direction is None or s.get('direction_share') is None else '(%s 요인의 %s)' % (DIR_WORD[direction], share_txt(s['direction_share']))
    return '가장 큰 %s은 %s, %s %s%s 입니다.' % (label, lk_bundle(b), kind, esc(pp_txt(s['net'])), share)


def _kind_lines_v2(ctx, kind):
    """The sentences of one kind about its cause candidates (K4 / M1): factors, how many candidates hold them, the first
    candidate, the biggest opposite one, the type, the CRP part."""
    skip = _skip_lines(ctx, kind)
    if skip is not None:
        return skip
    rk = ctx.bundles['ranking'][kind]
    inc, dec = rk.get('increase') or {}, rk.get('decrease') or {}
    # R18b:1: a side whose total is zero is named as absent, not as '0%p'
    dec_t, inc_t = dec.get('total'), inc.get('total')
    if not dec_t and inc_t:
        out = ['감소 요인은 없고, 증가 요인은 <b>%s</b> 입니다.' % esc(pp_txt(inc_t))]
    elif not inc_t and dec_t:
        out = ['증가 요인은 없고, 감소 요인은 <b>%s</b> 입니다.' % esc(pp_txt(dec_t))]
    else:
        sp0 = _split(ctx, kind)
        ratio = None if sp0 is None else _cancel_ratio(sp0)
        # rexp/T13: the opposite direction cancels this much of the net direction (the cancel ratio)
        tail = '' if ratio is None else ' 반대 방향 요인이 %s을 상쇄합니다(상쇄 비율 <b>%d%%</b>).' % (
            '감소 요인' if sp0['main'] == 'decrease' else '증가 요인', round(ratio * 100))
        out = ['감소 요인은 <b>%s</b>, 증가 요인은 <b>%s</b> 입니다.%s' % (esc(pp_txt(dec_t)), esc(pp_txt(inc_t)), tail)]
    if _crp_dominates(ctx, kind):
        # R18:1: the bridge between the vi change and the direction of the cause candidates; the CRP part is named here once
        out.append('보정값(CRP) 차이 %s 를 빼면 원인 후보의 순 변화는 %s입니다.' % (
            esc(pp_txt((ctx.bundles.get('crp') or {}).get(kind))), '증가' if rk['main'] == 'increase' else '감소'))
    sp = _split(ctx, kind)       # rexp/T13: the counts below are the ones the page shows (7 / 3 by the display rule)
    if sp is None:
        return out
    main = rk['main']
    opposite = 'decrease' if main == 'increase' else 'increase'
    cards = sp['shown'][main]
    if rk.get('direction') == 'balanced':
        out.append('증가 요인과 감소 요인이 비슷합니다. 큰 후보를 순 변화 방향 %d 개, 반대 방향 %d 개 보여 줍니다.' % (len(cards), len(sp['shown'][opposite])))
        for name in (main, opposite):
            if sp['shown'][name]:
                out.append(_factor_sentence(ctx, kind, sp['shown'][name][0], name, '%s 요인' % DIR_WORD[name]))
        return out
    if not cards:
        out.append('%s 변화는 원인 후보마다 아주 작아 따로 꼽지 않습니다.' % kind)
        return out
    word = DIR_WORD[main]
    folded = sp['folded'][main]
    cover = math.fsum(b['kinds'][kind].get('direction_share') or 0.0 for b in cards)
    if not folded:
        out.append('%s 요인은 원인 후보 %d 개에 모두 있습니다.' % (word, len(cards)))
    else:
        out.append('%s 요인의 %s 가 원인 후보 %d 개에서 나왔습니다.' % (word, share_txt(cover), len(cards)))
        out.append('나머지 %d 원인 후보가 %s 를 나눠 가집니다.' % (len(folded), share_txt(1.0 - cover)))
    out.append(_factor_sentence(ctx, kind, cards[0], main, '%s 요인' % word))
    others = sp['shown'][opposite]
    if others:
        out.append(_factor_sentence(ctx, kind, others[0], None, '반대 방향 요인'))
    top = _top_type(ctx, kind, main)
    if top is not None:
        out.append('%s %s 요인의 %s 는 \'%s\' 유형에서 나왔습니다.' % (kind, word, share_txt(top[1]), TYPE_NAME[top[0]]))
    return out


def _kind_lines_legacy(ctx, kind):
    """An older ledger (no `bundles.version`): the ranking is by total variation (|delta| sum) — the old reading, said so."""
    skip = _skip_lines(ctx, kind)
    if skip is not None:
        return skip
    bd = ctx.bundles
    rk = bd['ranking'][kind]
    cards = _cards_of(ctx, rk.get('cards'))
    if not cards:
        return ['%s 변화는 원인 후보마다 아주 작아 따로 꼽지 않습니다.' % kind]
    rest = rk.get('rest') or []
    if not rest:
        out = ['%s %s은 원인 후보 %d 개에 모두 있습니다.' % (kind, TOTAL_TEXT, len(cards))]
    else:
        out = ['%s %s의 %s 는 상위 %d 원인 후보의 변화입니다.' % (kind, TOTAL_TEXT, share_txt(rk.get('cover')), len(cards)),
               '나머지 %d 원인 후보가 %s 를 나눠 가집니다.' % (len(rest), share_txt(rk.get('rest_share')))]
    top = cards[0]
    out.append('가장 큰 %s 원인 후보는 %s, %s %s(총 변동의 %s) 입니다.' % (
        kind, lk_bundle(top), kind, esc(pp_txt(top['kinds'][kind]['net'])), share_txt(top['kinds'][kind]['share'])))
    return out


def _vi_sentence(ctx, kind):
    kd, d = ctx.doc['kinds'][kind], ctx.doc['totals'][kind]['delta']
    if d is None:
        return '%s vi 는 값이 대기라 비교하지 않습니다.' % kind
    if d == 0:
        return '%s vi 는 A %s 에서 B %s 로 변하지 않았습니다.' % (kind, esc(vi_txt(kd['vi_a'])), esc(vi_txt(kd['vi_b'])))
    return '%s vi 는 A <b>%s</b> 에서 B <b>%s</b> 로 <b>%s</b> %s.' % (
        kind, esc(vi_txt(kd['vi_a'])), esc(vi_txt(kd['vi_b'])), esc(pp_txt(d)), '증가했습니다' if d > 0 else '감소했습니다')


def _bundle_status(bd):
    """checks.bundles.status of the ledger as it is (R12:8: never counted again here)."""
    return bd.get('status') or 'not_evaluated'


def check_statuses(ctx, with_rebase=False):
    """Every check status of the page: the ledger sums, the item breakdown, the bundles (and the rebase when asked)."""
    statuses = _sum_checks(ctx)[1] + _item_checks(ctx)[1]
    checks = ctx.doc.get('checks') or {}
    bd = checks.get('bundles')
    if isinstance(bd, dict):
        statuses.append(_bundle_status(bd))
        for kind in KINDS:
            statuses.extend(c.get('status') for c in ((bd.get('kinds') or {}).get(kind) or {}).values())
    conc = checks.get('concentration')
    if isinstance(conc, dict):
        for kind in KINDS:
            for stage in (conc.get(kind) or {}).values():
                statuses.extend(c.get('status') for c in stage.values())
    if with_rebase and isinstance(checks.get('rebase'), dict):
        statuses.extend(rebase_statuses(ctx))
    return statuses


def rebase_statuses(ctx):
    rb = (ctx.doc.get('checks') or {}).get('rebase')
    out = []
    if isinstance(rb, dict):
        for kind in KINDS:
            entry = rb.get(kind) or {}
            if 'segments' in entry:
                out.extend(c.get('status') for c in entry['segments'].values())
                out.append((entry.get('total') or {}).get('status'))
            else:
                out.append(entry.get('status', 'not_evaluated'))
    return out


def _opinion(ctx):
    """The overall opinion of 1: what changed, which cause candidates carry it, the conflict, the resolution notice, the trust."""
    ops = []
    if ctx.bundles is None:
        ops.extend(_vi_sentence(ctx, kind) for kind in KINDS)
        ops.append('원인 후보 요약은 이 입력에 없어 쓰지 않습니다. ' + RERUN)
    else:
        if not ctx.v2:
            ops.append('이 ledger 는 원인 후보를 총 변동 기준으로 순위 매긴 옛 방식입니다. 증가·감소 방향 기준으로 보려면 ' + RERUN)
        for kind in KINDS:
            ops.append(_vi_sentence(ctx, kind))
            lines = (_kind_lines_v2 if ctx.v2 else _kind_lines_legacy)(ctx, kind)
            ops.extend(lines)
            crp_d = _crp_outside(ctx, kind)
            if (crp_d is not None and not _crp_dominates(ctx, kind)
                    and (ctx.bundles.get('status') or {}).get(kind) not in ('no_change', 'not_evaluated')):
                # R12:1 / M2: the CRP part is named once, after the sentences that say what the cards hold
                ops.append('pixel 변화 중 %s 는 보정값(CRP) 차이에서 나왔고, 원인 후보에는 들어 있지 않습니다.' % esc(pp_txt(crp_d)))
        cards = [ctx.bundle_by_id[i] for kind in KINDS for i in _ranked_ids(ctx, kind) if i in ctx.bundle_by_id]
        conflict = sorted((b for b in cards if b.get('conflict')), key=lambda b: (-max(b['kinds'][k]['share'] or 0.0 for k in KINDS), b['id']))
        if conflict:
            b = conflict[0]
            ops.append('%s 변화는 line 을 %s. 같은 변화가 pixel 을 %s.' % (
                lk_bundle(b), '증가시켰습니다' if b['kinds']['line']['net'] > 0 else '감소시켰습니다',
                '증가시켰습니다' if b['kinds']['pixel']['net'] > 0 else '감소시켰습니다'))
    design = ctx.design or {}
    states = {k: (design.get(k) or {}).get('status') for k in KINDS}
    if any(s == 'available' and 'resolution' not in (design[k].get('negligible') or []) for k, s in states.items()):
        ops.append('원인 후보 값에는 해상도 차이 몫도 들어 있습니다. 해상도 몫을 뺀 비교는 <a href="#s6" data-go="s6">6 해상도 차이 비교</a>에 있습니다.')
    elif any(s in ('unavailable', 'not_evaluated') for s in states.values()):
        ops.append('해상도 몫은 지금 나눌 수 없습니다. 이유는 <a href="#s6" data-go="s6">6 해상도 차이 비교</a>에 있습니다.')
    statuses = check_statuses(ctx)
    n_ok = sum(1 for s in statuses if s == 'passed')
    n_ne = sum(1 for s in statuses if s == 'not_evaluated')
    n_bad = len(statuses) - n_ok - n_ne
    ops.append(('검산 %d 건은 확인이 필요합니다. ' % n_bad if n_bad else '검산 %d 건 통과 · %d 건 판정 불가. ' % (n_ok, n_ne) if n_ne
                else '검산 %d 건은 통과했습니다. ' % n_ok) + '원인 후보는 원인을 확정하지 않고 함께 나타난 변화를 보여 줍니다.')
    return ops


def _section1(ctx):
    opinion = '<div class="report" id="opinion"><b>종합 의견</b>%s</div>' % ''.join('<p>%s</p>' % o for o in _opinion(ctx))
    out = ['<section id="s1">', _hd('1', '전체 요약', '얼마나 달라졌고, 주로 무엇에서 왔나?'), opinion,
           '<div class="kpis">%s%s</div>' % (_kpi(ctx, 'line'), _kpi(ctx, 'pixel')), _size_range_line(ctx), _trust(ctx),
           _nx([('s3', '3 원인 후보 — 무엇이 vi 를 바꿨나'), ('s2', '2 제품 대응 — 무엇이 달라졌나'), ('s4', '4 결과 — 불량은 어떻게 변했나')]),
           '</section>']
    return ''.join(out)


# ---- section 5 ------------------------------------------------------------------------------------------------------------

def _check_cell(status, text=None):
    cls = 'ok' if status == 'passed' else ('bad' if status == 'failed' else 'muted')
    return '<td class="l %s">%s</td>' % (cls, esc(text or CHECK_TEXT.get(status, status)))


def _sum_checks(ctx):
    """(table html, [status of every row]): the column sum (pairs [+ CNO] + curve intercept [+ CRP]) next to the vi, per kind and product."""
    rows, statuses = [], []
    for kind in KINDS:
        t = ctx.doc['totals'][kind]
        for side, label in (('a', 'A'), ('b', 'B')):
            parts = [t['items'][side]]
            if kind == 'pixel':
                parts.append(t['cno'][side])
            pair_part = osum(parts)
            curve = (t.get('intercept') or {}).get(side)
            crp = (t.get('crp') or {}).get(side) if kind == 'pixel' else None
            whole = osum([pair_part, curve] + ([crp] if kind == 'pixel' else []))
            total = t[side]
            if whole is None or total is None:
                status = 'not_evaluated'
            else:
                status = 'passed' if math.isclose(whole, total, rel_tol=1e-9, abs_tol=1e-12) else 'failed'
            statuses.append(status)
            rows.append('<tr data-kind="%s" data-side="%s"><td class="l">%s%s %s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>%s</tr>' % (
                kind, label, chip(kind), kind, label, esc(vi_txt(pair_part)), esc(vi_txt(curve)),
                esc(vi_txt(crp)) if kind == 'pixel' else '<span class="muted">—</span>', esc(vi_txt(whole)), esc(vi_txt(total)),
                _check_cell(status)))
    return ('<table id="sum-check"><thead><tr><th class="l">종류 · 제품</th><th>쌍 합 (+CNO)</th><th>곡선 보정</th><th>CRP 줄</th><th>합</th><th>전체 vi</th>'
            '<th class="l">검산</th></tr></thead><tbody>%s</tbody></table>' % ''.join(rows)), statuses


def _item_checks(ctx):
    """(html, [status of the kind rows and of the pair check])."""
    checks = (ctx.doc.get('checks') or {}).get('item_breakdown') or {}
    rows, statuses = [], []
    for kind in KINDS:
        brk = ctx.doc['kinds'][kind]['item_breakdown']
        pair_delta = ctx.doc['totals'][kind]['items']['delta']
        total = ctx.doc['totals'][kind]['delta']
        core = osum([brk.get('same'), brk.get('new'), None if brk.get('gone') is None else -brk['gone']])
        extra = osum([brk.get('cno'), brk.get('crp')])
        kind_check = ((checks.get('kind') or {}).get(kind) or {})
        statuses.append(kind_check.get('status', 'not_evaluated'))
        rows.append('<tr data-kind="%s"><td class="l">%s%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>%s</tr>' % (
            kind, chip(kind), kind, esc(pp_txt(core)), esc(pp_txt(pair_delta)), esc(pp_txt(extra)), esc(pp_txt(osum([core, extra]))),
            esc(pp_txt(total)), _check_cell(kind_check.get('status', 'not_evaluated'))))
    pair = checks.get('pair') or {}
    statuses.append(pair.get('status', 'not_evaluated'))
    note = ('<p class="muted" id="pair-check">쌍마다 (같은 + 새 − 없어진) = 쌍 Δ: 확인 %s 쌍 · 실패 %s 쌍 · 확인 안 함(값 대기) %s 쌍 · %s</p>' % (
        esc(pair.get('checked', '—')), esc(pair.get('failed', '—')), esc(pair.get('not_evaluated', '—')),
        esc(CHECK_TEXT.get(pair.get('status'), pair.get('status', '—')))))
    return ('<table id="item-check"><thead><tr><th class="l">종류</th><th>같은 + 새 − 없어진</th><th>쌍 Δ 합</th><th>CNO + CRP</th>'
            '<th>쌍 Δ 합 + 나머지</th><th>종류 전체 Δ</th><th class="l">검산</th></tr></thead><tbody>%s</tbody></table>%s' % (''.join(rows), note)), statuses


def _checks_block(ctx):
    """Both check tables in one <details>; it is open when any check is not 'passed' (failed, pending, partial) or the ledger is partial."""
    sum_html, sum_status = _sum_checks(ctx)
    item_html, item_status = _item_checks(ctx)
    statuses = sum_status + item_status + [(ctx.doc.get('checks') or {}).get('status')]
    attention = any(s != 'passed' for s in statuses) or ctx.doc.get('status') == 'partial'
    summary = '검산 상세 (합 관계 · item 분해) — %s' % ('확인이 필요한 항목이 있습니다' if attention else '모두 통과')
    return ('<details id="checks-details"%s><summary>%s</summary>'
            '<h3>합 관계 검산 <span class="muted" style="font-weight:400">(쌍 합 + CNO + 곡선 보정 + CRP = vi, 제품 전체)</span></h3>%s%s'
            '<h3>item 분해 검산 <span class="muted" style="font-weight:400">(같은 + 새 − 없어진 = 쌍 Δ 합, + CNO + CRP = 종류 전체 Δ)</span></h3>%s</details>' % (
                ' open' if attention else '', esc(summary), sum_html, _crp_line(ctx), item_html))


def _crp_line(ctx):
    if not ctx.crp_differs():
        return ''
    products = ctx.doc['inputs']['products']
    return ('<p id="crp-line"><b>CRP 줄</b>: A CRP %s, B CRP %s. 차이 %s 가 pixel 전체 Δ 에 더해집니다(A = −d, B = 0, 전극에 나누지 않음).</p>' % (
        esc(num(products['reference'].get('crp'))), esc(num(products['target'].get('crp'))), esc(ctx.crp_pp())))


def _link07(ctx):
    link = ctx.report07
    if link['status'] == 'linked':
        return '<p id="link07">가정 계산과 조건 Δ 는 07 에서 → <a id="link07-a" href="%s">07 열기</a></p>' % esc(link['href'])
    if link['status'] == 'missing' and link.get('reason') == 'no_relative_path':
        return '<p id="link07" class="muted">07 보고서는 있지만 다른 드라이브라 상대 링크를 만들 수 없습니다 — 07 폴더에서 직접 여세요.</p>'
    if link['status'] == 'missing' and link.get('reason') == 'scheme':
        return '<p id="link07" class="muted">07 보고서는 있지만 경로가 링크로 쓸 수 없는 형태라 링크를 만들지 않았습니다 — 07 폴더에서 직접 여세요.</p>'
    return ''      # no 07 report (not given, or no report.html in it): nothing is drawn; the link shows only when 07 exists


def _inputs_block(ctx):
    doc = ctx.doc
    inputs = doc['inputs']
    out = ['<h3>입력 · 제외 · 경고</h3><table id="products"><tr><th class="l">제품</th><th class="l">model_group</th><th class="l">pair_table_hash</th><th>CRP</th></tr>']
    for role, name in PRODUCT_NAMES:
        p = inputs['products'][role]
        out.append('<tr><td class="l">%s</td><td class="l">%s</td><td class="l wrap">%s</td><td>%s</td></tr>' % (
            name, esc(p.get('model_group')), esc(p.get('pair_table_hash')), esc(num(p.get('crp')) if p.get('crp') is not None else '—')))
    out.append('</table>')
    excluded = _pair_exclusion_rows(doc)
    if excluded:
        out.append('<h3>계산에서 제외한 전극 쌍</h3><table id="pair-excluded"><tr><th class="l">제품</th><th class="l">전극 쌍</th>'
                   '<th class="l">pair_id</th><th class="l">사유</th><th>case 수 (실제 / 기대 전용)</th><th class="l">layer</th></tr>')
        out.extend('<tr><td class="l">%s</td><td class="l">%s</td><td class="l">%s</td><td class="l">%s</td><td>%d / %d</td>'
                   '<td class="l wrap">%s</td></tr>' % (
                       esc(name), esc(' – '.join(electrodes)), esc(pair_id) if pair_id else '—',
                       esc(PAIR_REASON_TEXT.get(reason, reason)), actual, expected, esc(', '.join(layers)) if layers else '—')
                   for name, electrodes, pair_id, reason, actual, expected, layers in excluded)
        out.append('</table>')
    out.append('<table id="inputs">')
    for label, value in (('compare2.json sha256', inputs.get('compare2_json_sha256')), ('config sha256', inputs.get('config_sha256')),
                         ('ledger_basis', inputs.get('ledger_basis')), ('ledger 상태', doc.get('status'))):
        out.append('<tr><td class="l"><b>%s</b></td><td class="l wrap">%s</td></tr>' % (esc(label), esc(value)))
    out.append('<tr id="inputs-size-range"><td class="l"><b>이물 크기 범위</b></td><td class="l wrap">%s</td></tr>' % esc(
        _size_range_parts(doc)[0].replace('이물 크기 범위: ', '', 1)))
    inventory = inputs.get('electrode_inventory') or {}
    for role, name in PRODUCT_NAMES:
        inv = inventory.get(role) or {}
        out.append('<tr><td class="l"><b>전극 목록 %s</b></td><td class="l wrap">출처 %s · 상태 %s · %d 개 · sha256 %s</td></tr>' % (
            name, esc(inv.get('source')), esc(inv.get('status')), len(inv.get('electrodes') or []), esc(inv.get('sha256'))))
    if ctx.maps is not None:
        out.append('<tr><td class="l"><b>ledger-maps</b></td><td class="l wrap">상태 %s · sha256 %s</td></tr>' % (
            esc(ctx.maps['doc'].get('status')), esc(ctx.maps['sha'])))
    out.append('</table>')
    equations = (doc.get('checks') or {}).get('equations') or {}
    if equations:
        out.append('<details><summary>합계식 %d 개(ledger checks.equations)</summary><table id="equations"><tr><th class="l">합계식</th>'
                   '<th class="l">상태</th></tr>%s</table></details>' % (len(equations), ''.join(
                       '<tr><td class="l">%s</td><td class="l">%s</td></tr>' % (esc(name), esc(e.get('status'))) for name, e in equations.items())))
    warnings = doc.get('warnings') or []
    out.append('<h3>경고</h3>')
    if warnings:
        out.append('<ul id="warnings">%s</ul>' % ''.join(
            '<li><b>%s</b> %s</li>' % (esc(w.get('code')), esc(json.dumps({k: v for k, v in w.items() if k != 'code'}, ensure_ascii=False)))
            for w in warnings))
    else:
        out.append('<p class="muted">경고가 없습니다.</p>')
    unavailable = doc.get('unavailable') or []
    out.append('<h3>계산 불가 항목 %d 개</h3>' % len(unavailable))
    if unavailable:
        rows = ''.join('<tr><td class="l">%s</td><td class="l wrap">%s</td><td class="l">%s</td><td class="l wrap">%s</td></tr>' % (
            esc(u.get('scope')), esc(u.get('id')), esc(u.get('side', '')), esc(', '.join(u.get('reasons') or []))) for u in unavailable)
        out.append('<details><summary>목록 보기</summary><table id="unavailable"><tr><th class="l">범위</th><th class="l">대상</th>'
                   '<th class="l">제품</th><th class="l">사유</th></tr>%s</table></details>' % rows)
    else:
        out.append('<p class="muted">없습니다.</p>')
    return ''.join(out)


NUMBER_RULES = (('없음', '그 제품에 그 전극·쌍·item 이 없습니다.'), ('0%', '있지만 값이 0 입니다.'),
                ('대기', '값을 계산할 수 없습니다. 합계에서 뺍니다(합계도 대기).'), ('해당 없음', '그 불량 종류와 관계가 없습니다.'),
                ('확인 불가', '입력이 부족해 판정하지 못했습니다(예: DL1 이 없어 전극 목록이 관측 기준).'))


BUNDLE_CHECK_TEXT = {
    'net_plus_crp': '순 변화 합 + CRP = 종류 전체 변화', 'direction_identity': '방향 검산 — 증가 요인 합 + 감소 요인 합 + CRP = 종류 전체 변화(L1)',
    'share_sum': '총 변동 몫 합 = 1', 'direction_share_sum': '방향별 몫 합 = 1(L2)', 'cover_plus_rest': '목록 검산 — 카드 몫 + 기타 몫 = 1(L2)',
    'each_once': '목록 검산 — 카드 + 기타 + 순 변화 0 에 모든 원인 후보가 한 번씩(L2)', 'types_plus_crp': '유형 검산 — 유형 순 변화 합 + CRP = 종류 전체 변화(L3)'}


def _hub_sensitivity(ctx):
    """M4: how many hubs (and how much of the total variation) each hub setting would make; the setting in use is marked."""
    bd = ctx.bundles
    grid = bd.get('hub_sensitivity') if isinstance(bd, dict) else None
    if not grid:
        return _rerun_line('허브 민감도')
    cur = (bd.get('hub_min_fraction'), bd.get('hub_min_share'))

    def cell(entry, kind, key, fmt):
        v = (entry.get('kinds') or {}).get(kind)
        return '<td>%s</td>' % (fmt(v[key]) if v and v.get(key) is not None else '—')
    body = []
    for e in grid:
        now = (e.get('hub_min_fraction'), e.get('hub_min_share')) == cur
        cells = ''.join(cell(e, k, 'hubs', str) + cell(e, k, 'share', share_txt) for k in KINDS)
        body.append('<tr%s><td class="l">%s</td><td class="l">%s</td>%s<td class="l">%s</td></tr>' % (
            ' class="sum"' if now else '', '%g%%' % ((e.get('hub_min_fraction') or 0.0) * 100), '%g%%' % (e['hub_min_share'] * 100) if e.get('hub_min_share') else '0',
            cells, '현재 설정' if now else ''))
    return ('<h3>허브 민감도 <span class="muted" style="font-weight:400">(허브 기준을 바꿨을 때 다중 쌍 변화 카드가 얼마나 달라지나)</span></h3>'
            '<table id="hub-sensitivity"><thead><tr><th class="l">변한 상대 비율 ≥</th><th class="l">상대 크기 ≥</th><th>line 허브 수</th><th>line 허브 몫</th>'
            '<th>pixel 허브 수</th><th>pixel 허브 몫</th><th class="l"></th></tr></thead><tbody>%s</tbody></table>'
            '<p class="muted small">허브 몫 = 허브로 묶인 item 의 총 변동이 그 종류 총 변동에서 차지하는 비율입니다. 상대 크기는 그 종류 총 변동에 대한 비율입니다.</p>' % ''.join(body))


def _appendix_extra(ctx):
    """Checks of the bundles and the rebase, and the occurrence state counts (kept in the appendix)."""
    out = []
    checks = ctx.doc.get('checks') or {}
    rows = []
    bd = checks.get('bundles')
    if isinstance(bd, dict):
        rows.append(('원인 후보 묶음 검산 전체(중복 0 · 배정 수 = 원자 수 포함)', _bundle_status(bd)))
        for kind in KINDS:
            for name, c in ((bd.get('kinds') or {}).get(kind) or {}).items():
                rows.append(('원인 후보 %s · %s' % (kind, BUNDLE_CHECK_TEXT.get(name, name)), c.get('status', 'not_evaluated')))
    rs = rebase_statuses(ctx)
    if rs:
        rows.append(('해상도 비교(rebase) 검산 %d 건' % len(rs), 'passed' if all(s == 'passed' for s in rs) else (
            'failed' if 'failed' in rs else 'not_evaluated')))
    if rows:
        out.append('<h3>원인 후보 · 해상도 비교 검산</h3><table id="bundle-checks">%s</table>' % ''.join(
            '<tr><td class="l">%s</td>%s</tr>' % (esc(n), _check_cell(s)) for n, s in rows))
    else:
        out.append(_rerun_line('원인 후보·해상도 비교 검산'))
    out.append(_hub_sensitivity(ctx))
    if ctx.occ is not None:
        counts = ctx.occ.get('counts') or {}
        out.append('<h3>연결 영역 지표 상태</h3><table id="occ-counts"><tr><th class="l">제품</th><th class="l">상태별 item 수</th></tr>%s</table>' % ''.join(
            '<tr><td class="l">%s</td><td class="l wrap">%s</td></tr>' % (name, esc(' · '.join('%s %s' % (k, v) for k, v in sorted((counts.get(role) or {}).items()))))
            for role, name in PRODUCT_NAMES))
    elif ctx.maps is not None:
        out.append(_rerun_line('연결 영역 지표'))
    return ''.join(out)


def _section_appendix(ctx):
    out = ['<section id="sA">', _hd('부록', '근거 · 검산', '믿을 수 있나, 어떻게 계산했나?',
                                   ['검산·입력·제외·경고와 숫자 표시 규칙이 여기에 있습니다.']), '<div class="g2"><div>',
           _checks_block(ctx), _appendix_extra(ctx),
           _link07(ctx),
           '<h3>숫자 표시 규칙</h3><table id="number-rules">%s</table></div><div>' % ''.join(
               '<tr><td class="l"><b>%s</b></td><td class="l wrap">%s</td></tr>' % (esc(k), esc(v)) for k, v in NUMBER_RULES),
           '<h3>조건 값 <span class="muted" style="font-weight:400">(이물 크기 · density 레이어 · 면적 · λ · 값)</span></h3>',
           '<p class="muted" id="cond-link">선택한 쌍의 조건 값은 <a href="#s3" data-go="s3">3 원인 후보의 상세 패널</a> 맨 아래에 있습니다. 전체는 아래에서 봅니다.</p>',
           '<details id="cond-all"><summary>전체 보기 (%d 쌍)</summary><div id="cond-all-body"></div></details>' % len(ctx.doc['pairs']),
           _inputs_block(ctx), '</div></div>', _nx([('s1', '1 전체 요약으로 돌아가기')]), '</section>']
    return ''.join(out)


# ---- section 2: product correspondence (electrode x layer table) ------------------------------------------------------------

LAYER_SYMBOLS = {'both': ('●', '양쪽', 'st-both'), 'a_only': ('◀', 'A 만', 'st-a'), 'b_only': ('▶', 'B 만', 'st-b')}
MOVE_TEXT = {'a_only': '레이어 이동: A 에만 있음(B 는 다른 레이어로 옮김)', 'b_only': '레이어 이동: B 에만 있음(A 에서 옮겨 옴)'}
# darkness steps of the electrode name colour: |change| against A, in %, upper bounds of steps 1..3 (step 4 = above)
SHADE_BOUNDS = (1.0, 5.0, 20.0)
SHADE_LEGEND = '진하기: A 대비 |Δ| 1% 미만 · 1–5% · 5–20% · 20% 이상'


def _shade(row):
    """('up'|'dn'|'', step 1-4) of the electrode name: the larger |A-relative change| of line and pixel; no colour for a null or
    0 change."""
    best = None
    for kind in KINDS:
        p = (row.get(kind) or {}).get('pct_delta')
        if isinstance(p, (int, float)) and p != 0 and (best is None or abs(p) > abs(best)):
            best = p
    if best is None:
        return '', 0
    step = 1 + sum(1 for bound in SHADE_BOUNDS if abs(best) >= bound)
    return ('up' if best > 0 else 'dn'), step


def _el_attrs(name):
    return 'data-el="%s" data-href="%s" tabindex="0"' % (esc(name), esc(_href('electrode:' + name)))


def _layer_cell(row, layer, verified):
    state = (row.get('layer_state') or {}).get(layer)
    cno_only = bool(row.get('cno_only'))
    def attrs(cls):
        return 'class="c %s" data-cell="1" data-layer="%s" data-href="%s"' % (cls, esc(layer), esc(_href('electrode:' + row['electrode']) + '&layer=' + quote(layer, safe='')))
    if state is None:
        if verified:
            return '<td %s></td>' % attrs('st-none')
        if cno_only:
            return '<td %s title="CNO 가 관측되지 않은 층입니다. 없다는 뜻은 아닙니다">%s</td>' % (attrs('st-unk'), muted('확인 불가'))
        return '<td %s title="DL1 이 없어 이 레이어를 확인할 수 없습니다">%s</td>' % (attrs('st-unk'), muted('확인 불가'))
    symbol, text, cls = LAYER_SYMBOLS[state]
    basis = '' if verified else (' · CNO 관측 층' if cno_only else ' · 관찰 기준(DL1 없음)')
    if row.get('moved') and state in MOVE_TEXT:
        return '<td %s title="%s">⇄<sub>%s</sub></td>' % (attrs(cls + ' st-mv'), esc(MOVE_TEXT[state] + basis), 'A' if state == 'a_only' else 'B')
    return '<td %s title="%s">%s</td>' % (attrs(cls), esc(text + basis), symbol)


def _layout_maps(ctx):
    """A/B layout pictures beside the table (shape layer of the chosen layer; a cell click chooses the layer and marks the electrode)."""
    if ctx.maps is None:
        return '<div class="s2map" id="s2-maps"><p class="muted">지도 없음 — --maps-dir 를 주면 A·B layout 이 이 자리에 나옵니다.</p></div>'
    reason = _shape_reason(ctx)
    if reason or not _map_layers(ctx):
        return '<div class="s2map" id="s2-maps"><p class="muted" id="s2-maps-reason">%s</p></div>' % esc(reason or '지도 PNG 가 없습니다.')
    return ('<div class="s2map" id="s2-maps"><h4>A · B layout <span class="muted" style="font-weight:400" id="s2-layer"></span></h4>%s'
            '<p class="muted" style="margin:3px 0">표의 칸을 누르면 그 레이어로 바뀌고 그 전극이 굵게 보입니다.</p></div>' % _pair_of_maps('s2'))


def _cno_only_fold(doc, cno_rows):
    """rexp/T9: the CNO-only names (no DL1 electrode folder, only CNO cases) apart from the main table: electrode, in A, in B, the layers
    where CNO was seen. A row keeps data-el / data-href so it selects and highlights like a row of the main table (data-row="corr": the
    search filters it too)."""
    body = []
    for row in cno_rows:
        layers = sorted(row.get('layer_state') or {})
        body.append('<tr %s data-row="corr"><td class="l nm"><b>%s</b></td><td>%s</td><td>%s</td><td class="l">%s</td></tr>' % (
            _el_attrs(row['electrode']), esc(row['electrode']), '●' if row.get('present_a') else '—', '●' if row.get('present_b') else '—',
            esc(', '.join(layers)) if layers else muted('—')))
    return ('<details id="cno-only"><summary>CNO 전용 전극 %d 개 — DL1 전극 폴더가 없고 CNO 케이스에만 있는 이름 ▸</summary>'
            '<p class="small" id="note-cno-only">CNO 전용 %d 개 — 레이어 칸은 CNO 가 관측된 층이며, 비어 있어도 없다는 뜻은 아닙니다.</p>'
            '<div class="scrl"><table id="cno-table"><thead><tr><th class="l">전극</th><th>A</th><th>B</th><th class="l">관측 층</th></tr></thead>'
            '<tbody>%s</tbody></table></div></details>' % (len(cno_rows), len(cno_rows), ''.join(body)))


def _section2(ctx):
    doc = ctx.doc
    all_rows = doc['electrodes']
    # rexp/T1 (G1) + rexp/T9: CNO-only electrodes (no DL1 folder, only CNO cases) are not rows of the main table; they are listed in a
    # fold under it. The columns (layers) and the counts of the main table come from the other rows only.
    cno_only_rows = [row for row in all_rows if row.get('cno_only')]
    rows = [row for row in all_rows if not row.get('cno_only')]
    layers = ctx.sort_layers({layer for row in rows for layer in (row.get('layer_state') or {})})
    unverified = [row for row in rows if row.get('layer_state_status') != 'verified']
    counts = {code: sum(1 for row in rows if row.get('status') == code) for code in ('common', 'new', 'removed')}
    moved = sum(1 for row in rows if row.get('moved'))
    summary = ('공통 <b>%d</b> · 신규 <b>%d</b> · 제거 <b>%d</b> · 레이어 이동 <b>%d</b>%s%s'
               '<span class="muted"> — 대응은 전극 이름 기준입니다. 이름이 바뀐 전극은 \'제거 + 신규\' 로 보입니다.</span>' % (
                   counts['common'], counts['new'], counts['removed'], moved,
                   (' · 확인 불가 <b>%d</b>' % sum(1 for row in rows if row.get('status') == 'unverifiable')) if any(
                       row.get('status') == 'unverifiable' for row in rows) else '',
                   (' · CNO 전용 <b>%d</b> (아래 접기)' % len(cno_only_rows)) if cno_only_rows else ''))
    # nothing new, removed or moved (and nothing unverifiable): the table says "no difference", so it is folded; any difference opens it
    unchanged = not (counts['new'] or counts['removed'] or moved or unverified)
    if unchanged:
        out = ['<section id="s2">', _hd('2', '제품 대응', '두 제품의 전극·레이어는 어떻게 다른가?'),
               '<details id="corr-details"><summary class="lead" id="corr-summary">%s <span class="muted">(차이 없음 — 눌러서 표 펼치기)</span></summary>' % summary]
    else:
        out = ['<section id="s2">', _hd('2', '제품 대응', '두 제품의 전극·레이어는 어떻게 다른가?'), '<p class="lead" id="corr-summary">%s</p>' % summary]
    # epres/T1: both lists complete (DL1 folders) -> the unverified rows are electrodes with no folder in either product
    inventory = (doc.get('inputs') or {}).get('electrode_inventory') or {}
    list_complete = bool(inventory) and all((inv or {}).get('status') == 'complete' for inv in inventory.values())
    if unverified and list_complete:
        out.append('<div class="band info" id="band-no-dl1"><b>확인 불가</b> — DL1 전극 폴더에 없는 전극 %d 개는 레이어 대응(공통·신규·제거·이동)을 '
                   '확인하지 못했습니다. 두 제품 어느 쪽 폴더에도 없는 이름이며, 빈 칸은 없다는 뜻이 아니라 알 수 없다는 뜻입니다.</div>' % len(unverified))
    elif unverified:
        out.append('<div class="band info" id="band-no-dl1"><b>확인 불가</b> — DL1 전극 목록이 없어 %d 개 전극의 레이어 대응(공통·신규·제거·이동)을 '
                   '확인하지 못했습니다. 빈 칸은 없다는 뜻이 아니라 알 수 없다는 뜻입니다.</div>' % len(unverified))
    out.append('<p class="legend" id="corr-legend"><span class="st-both">●</span> 양쪽 · <span class="st-a">◀</span> A 만 · '
               '<span class="st-b">▶</span> B 만 · <span class="st-mv">⇄</span> 레이어 이동(A 전용 칸 ⇄<sub>A</sub> → B 전용 칸 ⇄<sub>B</sub>)'
               ' &nbsp;|&nbsp; 전극 이름 칸 색: <span class="hd-up3">증가</span> <span class="hd-dn3">감소</span> '
               '(line·pixel 중 A 대비 |Δ%%| 큰 쪽) · %s%s</p>' % (esc(SHADE_LEGEND), (
                   ' <span id="corr-observed" class="muted">· <b>관찰 기준</b>: DL1 이 없는 전극의 기호는 관찰된 레이어만 보인 것입니다(없다는 뜻이 아님)</span>')
                   if unverified and not list_complete else ''))
    out.append('<p class="searchrow"><label>전극 검색 <input type="search" id="el-q" autocomplete="off" placeholder="이름 일부"></label> '
               '<span id="el-count" class="muted">%d / %d</span> <span class="muted small">(본 표 + CNO 전용 표)</span></p>' % (len(all_rows), len(all_rows)))
    head = ''.join('<th data-layer="%s" data-href="%s" title="이 레이어를 선택">%s</th>' % (esc(layer), esc(_href('layer:' + layer)), esc(layer))
                   for layer in layers)
    body = []
    for row in rows:
        dirn, step = _shade(row)
        shade = ' hd-%s%d' % (dirn, step) if dirn else ''
        verified = row.get('layer_state_status') == 'verified'
        badge = esc(next((c['label'] for c in doc.get('electrode_status') or [] if c['code'] == row.get('status')), row.get('status')))
        body.append('<tr %s data-row="corr"><td class="l nm%s"><b>%s</b></td><td class="l"><span class="badge">%s</span></td>%s</tr>' % (
            _el_attrs(row['electrode']), shade,
            esc(row['electrode']), badge,
            ''.join(_layer_cell(row, layer, verified) for layer in layers)))
    out.append('<div class="s2row"><div class="s2tab"><div class="scrl"><table id="corr-table"><thead><tr><th class="l">전극</th><th class="l">상태</th>%s</tr></thead><tbody>%s</tbody></table></div></div>%s</div>' % (
        head, ''.join(body), _layout_maps(ctx)))
    if unchanged:
        out.append('</details>')
    if cno_only_rows:
        out.append(_cno_only_fold(doc, cno_only_rows))
    out.append('<p class="small" id="cell-cards" data-has="%d"></p>' % (1 if ctx.bundles is not None else 0))
    out.append('<div id="el-partners" class="partners"><p class="muted">전극 이름이나 칸을 누르면 그 전극의 상대 전극과 쌍 Δ 가 이 자리에 나옵니다. '
               '이 목록은 스크립트가 그립니다.</p></div>')
    out.append(_nx([('s3', '3 원인 후보 — 그중 무엇이 vi 를 바꿨나'), ('s4', '4 결과')]))
    out.append('</section>')
    return ''.join(out)


# ---- maps: section 3 (map + toggles) and the A/B layout of section 2 (mrep/T7; the small maps of section 1 were removed in rtrim/T2) --
# One numbered PNG pair (item picture `it`, electrode picture `el`) per layer and product is embedded once (id="map-png"); the
# script decodes the layers it needs, checks the pixel count of every number against ledger_maps.json and draws the three views
# from the same decoded arrays. Electrode shapes are thin outlines (no fill), so the defect fill of the selected pair stays readable.

ROLE_KEYS = (('reference', 'A'), ('target', 'B'))


def _map_entries(ctx, role):
    return ((ctx.maps['doc'].get('sides') or {}).get(role) or {}).get('maps') or []


def _map_layers(ctx):
    return ctx.sort_layers({e['layer'] for role, _k in ROLE_KEYS for e in _map_entries(ctx, role) if isinstance(e, dict)})


def _has_el_maps(ctx):
    return any(e.get('kind') == 'el' for role, _k in ROLE_KEYS for e in _map_entries(ctx, role) if isinstance(e, dict))


def _shape_reason(ctx):
    """'' when the electrode outlines can be drawn, else the reason (DL1 electrode pictures are missing)."""
    if _has_el_maps(ctx):
        return ''
    if any(row.get('layer_state_status') != 'verified' and not row.get('cno_only') for row in ctx.doc['electrodes']):
        return '전극·레이어 형상을 그릴 수 없습니다: DL1 이 없어 전극 그림(el)이 만들어지지 않았습니다.'
    return '전극·레이어 형상을 그릴 수 없습니다: ledger-maps 에 전극 그림(el)이 없습니다.'


def _map_png_block(ctx):
    pngs = ctx.maps['pngs']
    return ('<script type="application/json" id="map-png">%s</script>' % _json_embed(
        {name: base64.b64encode(data).decode('ascii') for name, data in sorted(pngs.items())}))


def _mapcv(view, role, title, extra=''):
    return ('<figure class="mfig"><figcaption><b>%s</b></figcaption><canvas class="mapcv" data-view="%s" data-role="%s" width="1" height="1" '
            'role="img" aria-label="%s 지도" hidden%s></canvas><div class="mnote" data-view="%s" data-role="%s"></div></figure>' % (
                esc(title), view, role, esc(title), extra, view, role))


def _pair_of_maps(view):
    return '<div class="mpair">%s%s</div>' % (_mapcv(view, 'A', '기준 A'), _mapcv(view, 'B', '대상 B'))


def _missing_share(ctx):
    """The part of the change the maps cannot show: CNO, the curve intercept, the CRP line (display of ledger values)."""
    t = ctx.doc['totals']

    def part(label, value, kind):
        if value is None:
            return '%s 대기' % label
        p = ctx.pct(value, kind)
        return '%s %s' % (label, spct(p) if p is not None else '— (A 가 0)')
    cno = (t['pixel'].get('cno') or {}).get('delta')
    parts = [part('CNO', cno, 'pixel'),
             '곡선 보정 line %s / pixel %s' % (part('', (t['line'].get('intercept') or {}).get('delta'), 'line').strip(),
                                            part('', (t['pixel'].get('intercept') or {}).get('delta'), 'pixel').strip())]
    if ctx.crp_differs():
        parts.append(part('CRP', ctx.crp().get('delta'), 'pixel'))
    return '<p id="m3-missing" class="muted">지도에 없는 몫(A vi 대비): %s</p>' % esc(' · '.join(parts))


# ---- 3 detail panel shell and 4 pair finder (rexp/T2) ------------------------------------------------------------------------
# The detail panel of 3 is drawn by the script from the data bundle (pairs, items, cause candidates, map numbers). The pair finder of 4
# is plain HTML (one row per pair); the script only filters the rows by name and shows the first PAIR_SHOW.

PAIR_SHOW = 20


def _pair_sort_key(pair):
    pct, delta = pair.get('pct_delta'), pair.get('delta')
    if pct is not None:
        return (0, -abs(pct), pair['pair_id'])
    return (1, -abs(delta), pair['pair_id']) if delta is not None else (2, 0, pair['pair_id'])


def _pair_units(ctx):
    """{pair_id: [(|net| sum, bundle id)]}: the cause candidates the change of a pair went into, the largest first."""
    out = {}
    for b in (ctx.bundles or {}).get('list') or []:
        per = {}
        for r in b.get('rows') or []:
            if r.get('pair_id'):
                per[r['pair_id']] = per.get(r['pair_id'], 0.0) + (r.get('abs') or 0.0)
        for pid, total in per.items():
            out.setdefault(pid, []).append((total, b['id']))
    for mine in out.values():
        mine.sort(key=lambda x: (-x[0], x[1]))
    return out


def _pf_row(ctx, pair, index, units):
    pid = pair['pair_id']
    name = '–'.join(pair['electrodes'])
    mine = [bid for _t, bid in units.get(pid) or [] if bid in ctx.bundle_by_id]
    if mine:
        cand = esc(bundle_title(ctx.bundle_by_id[mine[0]])) + (' <span class="muted small">외 %d</span>' % (len(mine) - 1) if len(mine) > 1 else '')
    else:
        cand = muted('—')
    return ('<tr class="plrow%s" data-pair="%s" data-href="%s" data-scroll="explore" tabindex="0" data-name="%s" data-idx="%d">'
            '<td class="l"><b>%s</b> %s%s</td><td class="l">%s%s</td><td class="l wrap">%s</td><td>%s</td></tr>' % (
                ' hide' if index >= PAIR_SHOW else '', esc(pid), esc(_href('pair:' + pid)), esc((name + ' ' + pid).lower()), index,
                esc(name), ctx.change_badge(pair['change_kind']), _comp_text(pair), chip(pair['kind']), esc(pair['kind']), cand,
                pp_cell(pair.get('delta'), pair.get('pct_delta'))))


def _pair_find(ctx):
    pairs = sorted(ctx.doc['pairs'], key=_pair_sort_key)
    units = _pair_units(ctx)
    total = len(pairs)
    shown = min(PAIR_SHOW, total)
    rows = ''.join(_pf_row(ctx, p, i, units) for i, p in enumerate(pairs)) or '<tr><td colspan="4" class="l muted">계산한 쌍이 없습니다.</td></tr>'
    return ('<div id="pair-find"><h3>쌍 찾기 <span class="muted" style="font-weight:400">(원인 후보에 없는 쌍을 이름으로 찾을 때 · 누르면 3 의 상세 패널이 그 쌍으로 바뀝니다)</span></h3>'
            '<p class="searchrow"><label>쌍·전극 이름 검색 <input type="search" id="pf-q" autocomplete="off" placeholder="예: Scan3 또는 Anode–Vint"></label> '
            '<span id="pf-count" class="muted">결과 %d 쌍 · 표시 %d</span></p>'
            '<div class="scrl" id="pf-box"><table id="pf-table" data-total="%d" data-count="%d" data-shown="%d"><thead><tr><th class="l">쌍</th>'
            '<th class="l">종류</th><th class="l">원인 후보</th><th class="l">Δ (쌍 전체)</th></tr></thead><tbody>%s</tbody></table></div>'
            '<p id="pf-none" class="muted" hidden>조건에 맞는 쌍이 없습니다.</p>'
            '<p style="margin:5px 0 0"><button type="button" id="pf-more" data-f="more" data-v="1"%s>더 보기 (+%d 쌍)</button></p>'
            '<p class="muted">줄 = 쌍 하나. Δ 는 쌍 전체의 장부 값(vi 의 %%p)이며 A 대비 %% 는 각 종류의 vi A 기준입니다. '
            '기본으로 A 대비 변화가 큰 순의 상위 %d 쌍만 보입니다. 해상도 몫을 뺀 값은 <a href="#s6" data-go="s6">6 해상도 차이 비교</a>에 있습니다.</p></div>' % (
                total, shown, total, total, shown, rows, '' if total > PAIR_SHOW else ' hidden', max(0, total - PAIR_SHOW), PAIR_SHOW))


def _detail_shell(ctx):
    """The right column of 3: the script fills #s3-detail (head, pairs, failure types, places of the selection)."""
    return ('<div class="s3side"><div class="dpanel"><div id="s3-detail"><p class="muted" id="dp-empty">원인 후보 카드나 '
            '4 결과의 쌍을 누르면 이 자리에 그 후보의 쌍, 불량 방식, 자리가 나옵니다.</p></div>%s</div></div>' % (
                _missing_share(ctx) if ctx.maps is not None else ''))


# ---- section 4: electrode ledger and layer x size ----------------------------------------------------------------------------

def _excluded_only_electrodes(doc):
    """Electrodes that appear in a left-out pair and keep no calculated item (no registered pair, no CNO)."""
    kept = {r['electrode_id'] for r in doc.get('cno') or []}
    for row in doc['pairs']:
        kept.update(row['electrodes'])
    named = set()
    for _name, electrodes, _pid, _reason, _a, _e, _layers in _pair_exclusion_rows(doc):
        named.update(electrodes)
    return named - kept


def _applies(ctx, kind, row):
    """False when the electrode has no pair of this failure kind (pixel: nor a CNO row) — the cell says 해당 없음."""
    return row['electrode'] in ctx.kind_electrodes[kind]


def _ab_cell(ctx, kind, row):
    """A → B of an electrode row: 해당 없음 (no pair of this kind) / 없음 (the electrode is not in that product) / 대기 / 0% / value."""
    v = row.get(kind)
    if not _applies(ctx, kind, row):
        return muted('해당 없음')
    if v is None:
        return muted('—')
    return '%s → %s' % (_side_value(row, 'a', v), _side_value(row, 'b', v))


def _side_value(row, side, v):
    """One side of an A → B cell. 없음 when the electrode has no DL1 folder there and no value; a non-zero value is still
    shown, marked (DL1 폴더 없음) (epres/T1)."""
    value = v.get(side)
    if row.get('present_' + side):
        return esc(vi_txt(value))
    if isinstance(value, (int, float)) and value != 0:
        return esc(vi_txt(value)) + ' <span class="muted">(DL1 폴더 없음)</span>'
    return muted('없음')


def _delta_cell(ctx, kind, v, row=None):
    if row is not None and not _applies(ctx, kind, row):
        return muted('해당 없음')
    if v is None:
        return muted('—')
    return pp_cell(v.get('delta'), ctx.pct(v.get('delta'), kind))


def _ledger_total(doc, kind):
    t = doc['totals'][kind]
    if kind == 'line':
        return {k: t['items'][k] for k in ('a', 'b', 'delta')}
    return {k: osum([t['items'][k], t['cno'][k]]) for k in ('a', 'b', 'delta')}


def _ledger_rank(row):
    values = [abs(row[kind]['pct_delta']) for kind in KINDS if isinstance((row.get(kind) or {}).get('pct_delta'), (int, float))]
    return (0 if values else 1, -max(values or [0.0]), row['electrode'])


def _electrode_ledger(ctx):
    doc = ctx.doc
    only = _excluded_only_electrodes(doc)
    cno_names = {r['electrode_id'] for r in doc.get('cno') or []}
    rows = sorted(doc['electrodes'], key=_ledger_rank)
    body = []
    for row in rows:
        name = row['electrode']
        mark = ' <span class="muted">(계산 제외 쌍만 있음)</span>' if name in only else ''
        if name in cno_names:
            cno_v = row.get('cno') or {}
            cno = '<td>%s%s</td>' % (_side_value(row, 'a', cno_v).replace('<span class="muted">없음</span>', '없음') + ' → ' +
                                     _side_value(row, 'b', cno_v).replace('<span class="muted">없음</span>', '없음'),
                                     sub('Δ ' + pp_txt((row.get('cno') or {}).get('delta'))))
        else:
            cno = '<td>%s</td>' % muted('해당 없음')
        body.append('<tr %s data-row="ledger"><td class="l"><b>%s</b>%s</td><td>%s</td><td class="l">%s</td><td>%s</td><td class="l">%s</td>%s</tr>' % (
            _el_attrs(name), esc(name), mark, _ab_cell(ctx, 'line', row), _delta_cell(ctx, 'line', row.get('line'), row),
            _ab_cell(ctx, 'pixel', row), _delta_cell(ctx, 'pixel', row.get('pixel'), row), cno))
    tl, tp = _ledger_total(doc, 'line'), _ledger_total(doc, 'pixel')
    total = ('<tr class="sum" id="ledger-total"><td class="l">합계</td><td>%s</td><td class="l">%s</td><td>%s</td><td class="l">%s</td><td></td></tr>' % (
        esc(vi_txt(tl['a'])) + ' → ' + esc(vi_txt(tl['b'])), _delta_cell(ctx, 'line', tl),
        esc(vi_txt(tp['a'])) + ' → ' + esc(vi_txt(tp['b'])), _delta_cell(ctx, 'pixel', tp)))
    return ('<h3>전극 장부 <span class="muted" style="font-weight:400">(전극 쌍의 값은 두 전극에 절반씩, CNO 는 그 전극에 전부 · vi 는 %% 로 씀 · A 대비 %% 는 각 종류의 vi A 기준)</span></h3>'
            '<div class="scrl"><table id="ledger-table"><thead><tr><th class="l">전극</th><th>line A → B</th><th class="l">line Δ</th>'
            '<th>pixel A → B</th><th class="l">pixel Δ</th><th>그중 pixel CNO A → B</th></tr></thead><tbody>%s</tbody><tfoot>%s</tfoot></table></div>'
            '<p class="muted" id="ledger-total-note">합계 줄은 전극 장부의 합입니다(line = 전극 쌍 합, pixel = 전극 쌍 합 + CNO). 곡선 보정·CRP 차이는 전극에 나누지 않으므로 '
            '종류 전체 vi(1 전체 요약)와 다를 수 있습니다.</p>' % (''.join(body), total))


def _size_table(ctx, kind):
    rows = (ctx.doc.get('layers') or {}).get(kind) or []
    if not rows:
        return ''
    rows = sorted(rows, key=lambda r: ctx.lk(r['layer']))      # E10: the layer order of inputs.layer_order
    pct_kind = 'pixel' if kind == 'cno' else kind
    all_sizes = sorted({s['diameter_um'] for row in rows for s in row.get('by_size') or []})
    # a size whose value is 0 or absent in every row carries no information: it is left out (and said so)
    sizes = [d for d in all_sizes if any(s['diameter_um'] == d and s.get('delta') != 0 for row in rows for s in row.get('by_size') or [])]
    dropped = [d for d in all_sizes if d not in sizes]
    head = ''.join('<th>%s µm Δ</th>' % esc(num(d)) for d in sizes)
    body = []
    for row in rows:
        by = {s['diameter_um']: s for s in row.get('by_size') or []}
        cells = ''.join('<td>%s</td>' % (pp_cell(by[d]['delta'], ctx.pct(by[d]['delta'], pct_kind)) if d in by else muted('—')) for d in sizes)
        body.append('<tr data-layer="%s" data-href="%s" tabindex="0"><td class="l"><b>%s</b></td><td>%s → %s</td><td class="l">%s</td>%s</tr>' % (
            esc(row['layer']), esc(_href('layer:' + row['layer'])), esc(row['layer']), esc(vi_txt(row['a'])), esc(vi_txt(row['b'])),
            pp_cell(row['delta'], ctx.pct(row['delta'], pct_kind)), cells))
    title = {'line': 'line', 'pixel': 'pixel', 'cno': 'pixel CNO (pixel 의 일부)'}[kind]
    hidden = ('<p class="muted" id="size-hidden-%s">Δ 가 모두 0 인 크기 %d개 숨김 (%s µm)</p>' % (
        kind, len(dropped), esc(', '.join(num(d) for d in dropped)))) if dropped else ''
    return ('<h4>%s%s</h4>%s<div class="ovx"><table id="size-%s"><thead><tr><th class="l">레이어</th><th>A → B</th><th class="l">Δ</th>%s</tr></thead>'
            '<tbody>%s</tbody></table></div>' % (chip(pct_kind), esc(title), hidden, kind, head, ''.join(body)))


def _section5(ctx):
    out = ['<section id="s5">', _hd('5', '분포', '전극·레이어·이물 크기별로는 어떻게 달라졌나?',
                                   ['전극 장부와 레이어 × 이물 크기 표입니다. 숫자는 실제 vi 이고 line 과 pixel 은 더하지 않습니다.']),
           _electrode_ledger(ctx),
           '<h3>레이어 × 이물 크기 <span class="muted" style="font-weight:400">(레이어를 누르면 그 레이어를 선택)</span></h3>']
    out.extend(_size_table(ctx, kind) for kind in ('line', 'pixel', 'cno'))
    out.append(_nx([('s6', '6 해상도 차이 비교 — 제품 크기 차이를 빼면'), ('s3', '3 원인 후보')]))
    out.append('</section>')
    return ''.join(out)


# ---- 3 cause candidates -----------------------------------------------------------------------------------------------------

def _mag_pp(v):
    return '%.4f%%p' % (abs(v) * 100)


def _role_text(ctx, b, kind):
    """What the card is to the vi change of one kind: its direction share (older ledger: its share of the total variation)."""
    s = b['kinds'][kind]
    if s.get('net') is None:
        return '대기'
    if not ctx.v2:
        return '총 변동의 %s' % share_txt(s.get('share'))
    sign = s.get('sign')
    if sign == 'none':
        return '변화 없음'
    if sign == 'zero':
        return '순 변화 0'
    word, share = DIR_WORD.get(sign, ''), share_txt(s.get('direction_share'))
    direction = ctx.bundles['ranking'][kind].get('direction')
    if direction == 'balanced':
        text = '%s 요인의 %s' % (word, share)
    elif sign == direction:
        text = '%s(%s 요인)의 %s' % (_dir_name(ctx, kind), word, share)
    else:
        text = '반대 방향 요인(%s 요인의 %s)' % (word, share)
    return text + (' · 내부 상쇄(순 변화 ≈ 0)' if s.get('cancelling') else '')


def _layer_rows_html(b):
    """The per-layer table of an electrode card (new / removed electrode, layer change): every layer the bundle touches."""
    rows = b.get('layer_rows') or []
    if not rows:
        return ''
    t = btype(b)
    names = {'both': '그대로', 'a_only': '제거(A 에만)', 'b_only': '추가(B 에만)'}

    def cell(v):
        return '<td>%s</td>' % (pp_cell(v, None) if v else '<span class="muted">—</span>')
    body = ''.join('<tr><td class="l"><b>%s</b></td><td class="l">%s</td>%s%s%s</tr>' % (
        esc(r['layer']), '신규' if t == 'electrode_new' else '삭제' if t == 'electrode_removed' else esc(names.get(r.get('state'), '—')),
        cell(r.get('line')), cell(r.get('pixel')), cell(r.get('cno'))) for r in rows)
    return ('<h4>레이어별 내역</h4><table class="bt2"><thead><tr><th class="l">레이어</th><th class="l">상태</th><th>line</th><th>pixel</th>'
            '<th>그중 CNO</th></tr></thead><tbody>%s</tbody></table>' % body)


def _hub_partner_line(b):
    """R17b:2: 'changed partners n / all m' of the judgement, and how many of them went to another card."""
    hub = b.get('hub')
    if not hub:
        return ''
    elsewhere = len({e['partner'] for e in b.get('partners_elsewhere') or []})
    return '<p class="bp small hubnum">변한 상대 %d / 전체 %d%s</p>' % (
        hub['changed_count'], hub['partner_total'], ' · 그중 %d 개는 다른 카드' % elsewhere if elsewhere else '')


def _card_share(ctx, b, kind):
    """The share text of the folded card: 'direction word + 분의 n%' (v2: direction_share); an older ledger: its share of the total variation."""
    s = b['kinds'][kind]
    if s.get('net') is None or not ctx.v2:
        return _role_text(ctx, b, kind)
    sign = s.get('sign')
    if sign in DIR_WORD and s.get('direction_share') is not None:
        return '%s분의 %s%s' % (DIR_WORD[sign], share_txt(s['direction_share']), ' · 내부 상쇄' if s.get('cancelling') else '')
    return _role_text(ctx, b, kind)


def _updown(b, kind):
    """(down, up) of one candidate and kind: the sums of its decreases and its increases inside the candidate; down = (|Δ| sum − net) / 2,
    up = (|Δ| sum + net) / 2. None for an older ledger that has no |Δ| sum."""
    s = b['kinds'][kind]
    if s.get('net') is None or s.get('abs') is None:
        return None
    return (s['abs'] - s['net']) / 2.0, (s['abs'] + s['net']) / 2.0


def _ud_txt(x):
    """The size of a decrease or an increase inside a candidate, in %p without a unit: '0.24', '0.012', '0'."""
    p = abs(x) * 100
    return '0' if p < 5e-4 else ('%.2f' % p if p >= 0.095 else '%.3f' % p)


def _heavy_cancel(b, kind):
    """rexp/T13: a decrease and an increase are both inside the candidate and their |Δ| sum is at least twice the |net| ('내부 상쇄 큼')."""
    ud = _updown(b, kind)
    if ud is None:
        return False
    s = b['kinds'][kind]
    return ud[0] > 1e-12 and ud[1] > 1e-12 and s['abs'] >= 2 * abs(s['net']) - 1e-12


def _kind_gmax(ctx, kind):
    """The scale of the card bars of a kind: the largest decrease or increase inside any of its candidates (every card of the kind shares it)."""
    best = 0.0
    for b in ctx.bundles.get('list') or []:
        ud = _updown(b, kind)
        if ud is not None:
            best = max(best, ud[0], ud[1])
    return best


def _card_bar(b, kind, gmax):
    """rexp/T13: the full-width bar under a card: 0 in the middle, a strong thick bar = the net (the direction colour), light thin bars = the
    decrease (left) and the increase (right) inside the candidate; the same scale for every card of the kind."""
    ud = _updown(b, kind)
    if ud is None or not gmax:
        return ''
    net = b['kinds'][kind]['net']
    wd, wu, wn = (round(46.0 * v / gmax, 2) for v in (ud[0], ud[1], abs(net)))
    return ('<div class="cbar" title="순 %s · 후보 안 감소 %s · 증가 %s"><i class="cd" style="width:%s%%"></i><i class="cu" style="width:%s%%"></i>'
            '<i class="cn %s" style="width:%s%%"></i></div>' % (esc(pp_txt(net)), _ud_txt(ud[0]), _ud_txt(ud[1]), wd, wu, 'dn' if net < 0 else 'up', wn))


def _card_sub(ctx, b, kind):
    """The second line of the right side of a card: '몫 51% · ↓0.24 ↑0.06' (an older ledger: its one role text)."""
    s = b['kinds'][kind]
    ud = _updown(b, kind)
    if not ctx.v2 or ud is None or s.get('net') is None:
        return _card_share(ctx, b, kind)
    share = s.get('direction_share')
    return '%s · ↓%s ↑%s' % ('몫 ' + share_txt(share) if share is not None else '몫 —', _ud_txt(ud[0]), _ud_txt(ud[1]))


def _bundle_card(ctx, b, kind, rank, opposite=False, direction=None, dup=False, gmax=None):
    """One folded card (a row of the list of `kind`): rank circle, name, type, 'what changed' fact, net change, share, the bar under it.
    The ▸ unfolds the layer table, the amount and the notes; the row click opens the detail panel (the script).
    rexp/T11: a candidate that is in the list of the other kind too has a second card here; its element id is '<id>--<kind>' (`dup`),
    data-unit always holds the candidate id. rexp/T13: the card is 65 px high; `gmax` is the scale of the bar of its kind."""
    uid = esc(b['id'])
    eid = uid + '--' + kind if dup else uid
    s = b['kinds'][kind]
    net = s.get('net')
    word = direction or ('increase' if (net or 0) > 0 else 'decrease')
    rk_cls = 'rk %s%s' % ('dn' if word == 'decrease' else 'up', ' op' if opposite else '')
    what = unit_what(ctx, b)
    heavy = '<span class="badge b-cx">내부 상쇄 큼</span>' if ctx.v2 and _heavy_cancel(b, kind) else ''
    head = ('<span class="%s" title="%s">%d</span><div class="cmain"><div class="ct"><a class="ctl nm" href="%s">%s</a>%s%s</div><div class="cw">%s</div>'
            '</div><div class="cv">%s<div class="sh">%s</div></div>'
            '<span class="tw" data-x="1" role="button" tabindex="0" title="펼치기: 레이어별 내역 · 얼마나" aria-label="펼치기">▸</span>%s' % (
                rk_cls, '반대 방향 요인 순위' if opposite else '순위', rank, esc(_href('unit:' + b['id'])), esc(_card_name(b)), _card_badge(b), heavy,
                esc(_card_fact(b)), pp_cell(net, None), esc(_card_sub(ctx, b, kind)), _card_bar(b, kind, gmax) if ctx.v2 else ''))
    notes = []
    if b.get('conflict'):
        notes.append('<span class="badge b-mv">상충</span>')
    if any((b['kinds'][k].get('cancelling')) for k in KINDS):
        notes.append('<span class="badge b-mv">내부 상쇄(순 변화 ≈ 0)</span>')
    parts = ['<div class="crow" id="%s" data-unit="%s" data-k="%s" data-rank="%d"%s title="%s">%s<div class="cexp"><p class="bp cwhat">%s</p>' % (
        eid, uid, kind, rank, ' data-opp="1"' if opposite else '', esc(bundle_title(b) + '\n' + what), head, esc(what))]
    if notes:
        parts.append('<p class="bp">%s</p>' % ' '.join(notes))
    parts += [_layer_rows_html(b), _hub_partner_line(b)]
    if b.get('layer_state_unverified'):
        parts.append('<p class="bp muted small">레이어 상태 미확인 — DL1 전극 목록이 없어 이 전극의 레이어 상태를 확인하지 못했습니다(상태 그대로로 계산).</p>')
    if b.get('layer_state_unknown'):
        parts.append('<p class="bp muted small">일부 끝점의 레이어 상태 정보가 없습니다(상태 그대로로 계산).</p>')
    for el in b.get('partners_elsewhere') or []:
        other = ctx.bundle_by_id.get(el['bundle_id'])
        if other is not None:
            parts.append('<p class="bp small">%s 와의 변화는 \'%s\' 카드에 있습니다.</p>' % (esc(el['partner']), lk_bundle(other)))
    inc, dec = b['atoms']['increase'], b['atoms']['decrease']
    amount = ', '.join('%s %s%s' % (k, pp_txt(b['kinds'][k]['net']), (
        '(내부 상쇄 %s)' % _mag_pp(b['kinds'][k]['internal_cancel'])) if (b['kinds'][k].get('internal_cancel') or 0) > 1e-12 else '') for k in KINDS)
    parts.append('<h4>얼마나</h4><p class="bp">%s. 증가 item %d 건 · 감소 item %d 건.%s</p>' % (
        esc(amount), inc, dec, ' <b class="warn">line 과 pixel 의 방향이 반대입니다(상충).</b>' if b.get('conflict') else ''))
    tp = ['가장 큰 상대 %s 가 이 묶음 %s %s의 %s입니다.' % (esc(b['top_partner'][k]['partner']), k, '총 변동', share_txt(b['top_partner'][k]['fraction']))
          for k in KINDS if (b.get('top_partner') or {}).get(k) and (b.get('partner_count') or 0) > 1]
    if tp:
        parts.append('<p class="bp">%s</p>' % ' '.join(tp))
    parts.append('<p class="bp"><a class="lkbtn" href="#unit=%s&amp;to=s2" title="2 제품 대응 표의 이 칸 보기">제품 대응 ▸</a></p></div></div>' % uid)
    return ''.join(parts)


def _rest_rows(ctx, kind, entries):
    """entries: [(bundle id, direction word or '')]; the table of the 기타 fold (largest |net| first)."""
    bs = [(ctx.bundle_by_id[i], d) for i, d in entries if i in ctx.bundle_by_id]
    bs.sort(key=lambda x: (-abs(x[0]['kinds'][kind]['net'] or 0.0), x[0]['id']))

    def row(item):
        b, d = item
        s = b['kinds'][kind]
        share = s.get('direction_share') if ctx.v2 else s.get('share')
        return ('<tr id="rest-%s-%s"><td class="l"><a class="lk" href="#unit=%s&amp;to=s2">%s</a></td><td class="l">%s</td><td class="l">%s</td>'
                '<td>%s <span class="muted small">%s</span></td></tr>' % (
                    kind, esc(b['id']), esc(b['id']), esc(bundle_title(b)), _unit_badge(b), esc(d or '—'),
                    pp_cell(s['net'], None), '' if share is None else share_txt(share)))
    head = ('<table><thead><tr><th class="l">원인 후보</th><th class="l">유형</th><th class="l">방향</th><th>%s 순 변화 (몫)</th></tr></thead><tbody>' % kind)
    shown, tail = bs[:50], bs[50:]
    out = '<div class="scrl">%s%s</tbody></table></div>' % (head, ''.join(row(x) for x in shown))
    if tail:
        out += '<details><summary class="small">%d 개 더 ▸</summary>%s%s</tbody></table></details>' % (len(tail), head, ''.join(row(x) for x in tail))
    return out


def _types_table(ctx):
    """M2: the type table of 3 — per kind: count, increase, decrease, net. No CRP row; the sum is the sum of the cause candidates."""
    types = ctx.bundles.get('types') or {}

    def cells(kind, r, show_count=True):
        if r is None:
            return '<td colspan="4" class="muted">대기</td>'
        def v(x):
            return pp_cell(x, None) if x else '<span class="muted">—</span>'
        return '<td>%s</td><td>%s</td><td>%s</td><td>%s</td>' % (r['count'] if show_count else '', v(r['increase']), v(r['decrease']),
                                                              v(r['net']) if r['count'] else '<span class="muted">—</span>')
    head = ('<table id="type-table"><thead><tr><th class="l" rowspan="2">유형</th>%s</tr><tr>%s</tr></thead><tbody>' % (
        ''.join('<th colspan="4" class="l">%s%s</th>' % (chip(k), k) for k in KINDS),
        ''.join('<th>원인 후보</th><th>증가</th><th>감소</th><th>순 변화</th>' for _k in KINDS)))
    body = ''.join('<tr data-type="%s"><td class="l">%s</td>%s</tr>' % (name, esc(TYPE_NAME[name]), ''.join(
        cells(k, (types.get(k) or {}).get(name) if types.get(k) else None) for k in KINDS)) for name in TYPE_ORDER)
    sums = []
    for k in KINDS:
        t = types.get(k)
        if not t:
            sums.append(cells(k, None))
            continue
        rows = [t[name] for name in TYPE_ORDER]
        sums.append(cells(k, {'count': sum(r['count'] for r in rows), 'increase': math.fsum(r['increase'] for r in rows),
                              'decrease': math.fsum(r['decrease'] for r in rows), 'net': math.fsum(r['net'] for r in rows)}))
    body += '<tr class="sum" id="type-sum"><td class="l">합(원인 후보)</td>%s</tr>' % ''.join(sums)
    ref = ''.join('<td></td><td></td><td></td><td>%s</td>' % (
        (pp_cell(types[k]['cno_all']['net'], None) if types[k]['cno_all']['net'] else '<span class="muted">해당 없음</span>') if types.get(k) else
        '<span class="muted">대기</span>') for k in KINDS)
    body += '<tr class="muted" id="type-cno-all"><td class="l">모든 CNO 합(합계 제외)<span class="sub2">위 유형에 나뉘어 들어 있음 · 합에 더하지 않음</span></td>%s</tr>' % ref
    return head + body + '</tbody></table>'


def _types_crp_line(ctx):
    lines = []
    for k in KINDS:
        crp = _crp_outside(ctx, k)
        if crp is not None:
            lines.append('%s 의 보정값(CRP) 차이 %s 는 위 합계에 들어 있지 않습니다(종류 전체 변화 = 합(원인 후보) + 보정값).' % (k, pp_txt(crp)))
    return '<p class="muted small" id="types-crp" style="margin:3px 0">%s</p>' % esc(' '.join(lines)) if lines else ''


def _dir_wide(ctx, kind):
    """The one wide cell of a kind that has no number row: the skip sentences (no candidates to name), or an older ledger's vi change and sentences."""
    skip = _skip_lines(ctx, kind)
    if skip is not None:
        return ' '.join(skip)
    delta = ctx.doc['totals'][kind]['delta']
    return '<span class="v">%s</span> %s' % (esc(pp_txt(delta)), ' '.join(_kind_lines_legacy(ctx, kind))) if not ctx.v2 else ''


def _dir_pp(value):
    """A signed %p value of the number block, with its direction colour."""
    return '<span class="kv %s">%s</span>' % ('' if not value else 'up' if value > 0 else 'down', esc(pp_txt(value)))


def _dir_row(ctx, kind):
    """rexp/T13: one row of the number block of 3 (a click switches the list): kind | A → B (% of A) | net increase or decrease and the number of
    candidates | net-direction sum, count, first candidate | opposite sum, count, cancel ratio."""
    sp = _split(ctx, kind)
    kd, delta = ctx.doc['kinds'][kind], ctx.doc['totals'][kind]['delta']
    main, other = sp['main'], sp['other']
    net = math.fsum(sp['total'].values())
    p = pct_of(net, kd['vi_a'])
    word = DIR_WORD[main]
    first = sp['all'][main][0] if sp['all'][main] else None
    ratio = _cancel_ratio(sp)
    top = ''
    if first is not None:
        fs = first['kinds'][kind]
        top = ' · 1 위 <b>%s</b> %s (%s)' % (esc(_card_name(first)), esc(pp_txt(fs['net'])), esc(share_txt(fs.get('direction_share'))))
    return ('<div class="dc dname"><span class="k">%s%s vi</span></div>'
            '<div class="dc dab"><span class="kl">A → B</span><span class="kv plain">%s → %s</span><span class="ks">%s</span></div>'
            '<div class="dc dnet %s"><span class="kl">순 %s</span>%s<span class="ks">후보 %d 개</span></div>'
            '<div class="dc dmain"><span class="kl">%s 요인 합</span>%s<span class="ks">후보 %d 개%s</span></div>'
            '<div class="dc dopp"><span class="kl">%s 요인 합</span>%s<span class="ks">후보 %d 개%s</span></div>' % (
                chip(kind), kind, esc(vi_txt(kd['vi_a'])), esc(vi_txt(kd['vi_b'])), 'A 대비 ' + esc(spct(p)) if p is not None else '',
                'down' if net < 0 else 'up' if net > 0 else '', word if net else '변화', _dir_pp(net), len(sp['all'][main]) + len(sp['all'][other]),
                word, _dir_pp(sp['total'][main]), len(sp['all'][main]), top,
                DIR_WORD[other], _dir_pp(sp['total'][other]), len(sp['all'][other]),
                '' if ratio is None else ' · <b>상쇄 비율 %d%%</b>' % round(ratio * 100)))


def _dirband(ctx):
    """The number block of 3 (one row per kind; a click on a row switches the list). Older ledger: its one sentence first."""
    note = '' if ctx.v2 else '<p class="dirnote">이 ledger 는 원인 후보를 총 변동 기준으로 순위 매긴 옛 방식입니다. 증가·감소 방향 기준으로 보려면 %s</p>' % RERUN
    rows = []
    for kind in KINDS:
        sp = _split(ctx, kind)
        if sp is None:
            body = '<div class="dc dname"><span class="k">%s%s vi</span></div><div class="dc dwide">%s</div>' % (chip(kind), kind, _dir_wide(ctx, kind))
        else:
            body = _dir_row(ctx, kind)
        rows.append('<div class="dir %s" data-k="%s" role="button" tabindex="0">%s</div>' % (kind, kind, body))
    crp = ''.join('<p class="muted small dircrp" id="dircrp-%s">%s 의 보정값(CRP) 차이 %s 는 위 합에 없음</p>' % (k, k, esc(pp_txt(_crp_outside(ctx, k))))
                  for k in KINDS if ctx.v2 and _crp_outside(ctx, k) is not None)
    return '<div id="dirband">%s<div class="dirs">%s</div>%s</div>' % (note, ''.join(rows), crp)


def _fold_id(kind, sp, name):
    """The id of the fold of one compartment (the net direction: bd-rest-<kind>; the opposite one: bd-rest-<kind>-opposite)."""
    return 'bd-rest-%s' % kind if name == sp['main'] else 'bd-rest-%s-opposite' % kind


def _fold_table(ctx, kind, bs, first_rank):
    """rexp/T13: the small table inside a fold: rank, name, type, net, ↓↑; a click on a row selects the candidate. first_rank None = no rank (net 0)."""
    def row(n, b):
        s, ud = b['kinds'][kind], _updown(b, kind)
        return ('<tr id="rest-%s-%s" class="frow" data-unit="%s" data-rank="%s" data-href="%s" role="button" tabindex="0" title="%s"><td class="muted">%s</td>'
                '<td class="l nm">%s</td><td class="l">%s</td><td>%s</td><td class="muted">%s</td></tr>' % (
                    kind, esc(b['id']), esc(b['id']), '' if first_rank is None else first_rank + n, esc(_href('unit:' + b['id'])), esc(bundle_title(b)),
                    '–' if first_rank is None else first_rank + n, esc(_card_name(b)), _card_badge(b), pp_cell(s['net'], None),
                    '' if ud is None else '↓%s ↑%s' % (_ud_txt(ud[0]), _ud_txt(ud[1]))))
    return ('<div class="scrl"><table class="ftab"><thead><tr><th>순위</th><th class="l">이름</th><th class="l">유형</th><th>%s 순</th><th>↓ ↑</th></tr></thead><tbody>%s</tbody></table></div>' % (
        kind, ''.join(row(n, b) for n, b in enumerate(bs))))


def _compartment(ctx, kind, sp, name, gmax, dup_ids):
    """rexp/T13: one compartment of the card list: the head band (direction, cancel ratio, sum, count), a line when the shares are even, the shown
    cards, and the fold of the others (the net direction first, the opposite direction second)."""
    bs, shown, folded, total = sp['all'][name], sp['shown'][name], sp['folded'][name], sp['total'][name]
    if not bs:
        return ''
    is_main = name == sp['main']
    ratio = _cancel_ratio(sp)
    title = '%s %s 요인' % ('①' if is_main else '②', DIR_WORD[name]) + ('' if is_main or ratio is None else ' · 상쇄 비율 %d%%' % round(ratio * 100))
    out = ['<div class="cgrp" id="grp-%s-%s"><div class="gh %s"><span>%s</span><span><span class="gv">%s</span> <span class="gc">· 후보 %d 개</span></span></div>' % (
        kind, 'main' if is_main else 'opp', 'd' if name == 'decrease' else 'u', esc(title), esc(pp_txt(total)), len(bs))]
    top = abs(bs[0]['kinds'][kind]['net'] or 0.0) / abs(total) if total else 1.0
    if top < FLAT_TOP_SHARE:
        cum = math.fsum(abs(b['kinds'][kind]['net'] or 0.0) for b in shown) / abs(total)
        out.append('<div class="spread" id="spread-%s-%s">고르게 퍼짐 — 1 위 <b>%s</b> · 상위 %d 개 <b>%s</b></div>' % (
            kind, 'main' if is_main else 'opp', esc(share_txt(top)), len(shown), esc(share_txt(cum))))
    out.append('<div class="bgrid" id="cards-%s%s">%s</div>' % (kind, '' if is_main else '-opposite', ''.join(
        _bundle_card(ctx, b, kind, n, not is_main, name, dup=b['id'] in dup_ids, gmax=gmax) for n, b in enumerate(shown, 1))))
    if folded:
        rest_sum = math.fsum(b['kinds'][kind]['net'] or 0.0 for b in folded)
        out.append('<details class="rest" id="%s"><summary><span><b>접힌 %d 개</b> <span class="muted small">각 %s 이하</span></span>'
                   '<span class="n">합 %s <span class="muted small">(%s)</span></span></summary>%s</details>' % (
                       _fold_id(kind, sp, name), len(folded), esc(_mag_pp(folded[0]['kinds'][kind]['net'] or 0.0)), esc(pp_txt(rest_sum)),
                       esc(share_txt(abs(rest_sum) / abs(total) if total else None)), _fold_table(ctx, kind, folded, len(shown) + 1)))
    out.append('</div>')
    return ''.join(out)


def _kind_cards(ctx, kind, rk, line_rk=None):
    """rexp/T13: the card list of one kind in two compartments: the net change direction (at most 7) and the opposite direction (at most 3), each
    with a fold of the others; the candidates whose net is 0 are folded last. The ranks are the numbers of the balance bar and its legend."""
    sp = _split(ctx, kind)
    shown_line = set(_ranked_ids(ctx, 'line')) if line_rk is not None else set()
    gmax = _kind_gmax(ctx, kind)
    parts = [] if sp is None else [_compartment(ctx, kind, sp, name, gmax, shown_line) for name in (sp['main'], sp['other'])]
    if not any(parts):
        return '<h3>%s 을 바꾼 변화</h3><p class="muted" id="cards-%s-none">%s 변화는 원인 후보마다 아주 작아 따로 꼽지 않습니다.</p>' % (kind, kind, kind)
    zero = ''
    if sp['zero']:
        zero = '<details class="rest" id="bd-zero-%s"><summary>순 변화 0 %d 개 ▸</summary>%s</details>' % (kind, len(sp['zero']), _fold_table(ctx, kind, sp['zero'], None))
    head = '%s은 %s 입니다' % (_dir_name(ctx, kind), DIR_WORD[sp['main']])
    return '<h3>%s · %s</h3>%s%s' % (chip(kind) + esc(kind), esc(head), ''.join(parts), zero)


def _fit_text(text, px, size):
    """Cut a text to fit px at a font size (a wide character = 1 em, the others 0.6 em), with … at the cut."""
    width = lambda t: sum(size if ord(c) > 0x2e80 else size * 0.6 for c in t)
    if width(text) <= px:
        return text
    out = ''
    for c in text:
        if width(out + c) > px - size:
            break
        out += c
    return out + '…'


BAL_W, BAL_Y, BAL_BH, BAL_H = 1420, 22, 44, 86      # rexp/T13: the viewBox of the balance bar (it shrinks with the page width)
BAL_LABEL2, BAL_LABEL1 = 110, 18                    # a piece at least 110 wide shows 'number. name' + 'type · share', at least 18 wide the number only


def _balance(ctx, kind):
    """rexp/T13: '감소 요인 · 증가 요인 균형' of one kind: the equation line, the horizontal bar (0 in the middle, decreases left, increases right, a ▲ at
    the net change) and a two-column legend. The pieces and the legend rows are the shown candidates of the compartments; a click selects one."""
    sp = _split(ctx, kind)
    if sp is None:
        return ''
    dec_t, inc_t = sp['total']['decrease'], sp['total']['increase']
    net = dec_t + inc_t
    mx = max(-dec_t, inc_t) * 1.04
    if mx <= 0:
        return ''
    cx = BAL_W / 2.0
    sc = (cx - 16) / mx
    ratio = _cancel_ratio(sp)
    cls = lambda v: 'down' if v < 0 else 'up' if v > 0 else ''
    eq = '순 변화 <b class="%s">%s</b> = 감소 요인 <b class="down">%s</b> + 증가 요인 <b class="up">%s</b>%s' % (
        cls(net), esc(pp_txt(net)), esc(pp_txt(dec_t)), esc(pp_txt(inc_t)), '' if ratio is None else ' · 상쇄 비율 <b>%d%%</b>' % round(ratio * 100))
    svg = ['<svg class="balsvg" viewBox="0 0 %d %d" width="100%%" preserveAspectRatio="xMidYMid meet" role="img" aria-label="감소 요인과 증가 요인 균형 막대">' % (BAL_W, BAL_H)]
    legend = {}
    y, bh = BAL_Y, BAL_BH
    for name in ('decrease', 'increase'):
        left = name == 'decrease'
        side = 'dn' if left else 'up'
        tot = abs(sp['total'][name])
        x, used, rows = cx, 0.0, []
        for i, b in enumerate(sp['shown'][name], 1):
            s = b['kinds'][kind]
            v = s['net'] or 0.0
            w = abs(v) * sc
            x0 = x - w if left else x
            share = abs(v) / tot if tot else None
            ud = _updown(b, kind)
            tip = '%d. %s · %s\n순 %s (%s)%s' % (i, _card_name(b), CARD_BADGE.get(btype(b), TYPE_NAME.get(btype(b), '')), pp_txt(v), share_txt(share),
                                                '' if ud is None else ' · ↓%s ↑%s' % (_ud_txt(ud[0]), _ud_txt(ud[1])))
            g = ['<g class="bseg %s" data-unit="%s" data-href="%s" role="button" tabindex="0"><title>%s</title>' % (side, esc(b['id']), esc(_href('unit:' + b['id'])), esc(tip)),
                 '<rect x="%.1f" y="%d" width="%.1f" height="%d" opacity="%.2f"/>' % (x0, y, w, bh, max(0.5, 1 - (i - 1) * 0.07))]
            mid = x0 + w / 2
            if w >= BAL_LABEL2:
                g.append('<text class="bl1" x="%.1f" y="%d" text-anchor="middle">%s</text><text class="bl2" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (
                    mid, y + 19, esc(_fit_text('%d. %s' % (i, _card_name(b)), w - 8, 12.5)), mid, y + 35,
                    esc(_fit_text('%s · %s' % (CARD_BADGE.get(btype(b), TYPE_NAME.get(btype(b), '')), share_txt(share)), w - 8, 11))))
            elif w >= BAL_LABEL1:
                g.append('<text class="bl1" x="%.1f" y="%.1f" text-anchor="middle">%d</text>' % (mid, y + bh / 2 + 5, i))
            g.append('</g>')
            svg.append(''.join(g))
            x = x0 if left else x0 + w
            used += abs(v)
            rows.append('<div class="lgr" data-unit="%s" data-href="%s" role="button" tabindex="0"><span class="lgn %s">%d</span> <b>%s</b> <span class="badge">%s</span> '
                        '<span class="%s n">%s</span> <span class="muted">%s</span></div>' % (
                            esc(b['id']), esc(_href('unit:' + b['id'])), side, i, esc(_card_name(b)), esc(CARD_BADGE.get(btype(b), TYPE_NAME.get(btype(b), ''))),
                            cls(v), esc(pp_txt(v)), esc(share_txt(share))))
        folded = sp['folded'][name]
        rw = (tot - used) * sc
        if folded and rw > 0.5:
            rx = x - rw if left else x
            rest_sum = math.fsum(b['kinds'][kind]['net'] or 0.0 for b in folded)
            svg.append('<g class="brest %s"><title>%s</title><rect x="%.1f" y="%d" width="%.1f" height="%d"/>%s</g>' % (
                side, esc('그 밖 %d 개 · 합 %s' % (len(folded), pp_txt(rest_sum))), rx, y, rw, bh,
                '<text x="%.1f" y="%.1f" text-anchor="middle">그 밖</text>' % (rx + rw / 2, y + bh / 2 + 4) if rw > 60 else ''))
        if folded:
            rows.append('<div class="lgr lgmore muted" data-fold="%s" role="button" tabindex="0">접힌 %d 개 · 합 %s ▸</div>' % (
                _fold_id(kind, sp, name), len(folded), esc(pp_txt(math.fsum(b['kinds'][kind]['net'] or 0.0 for b in folded)))))
        legend[name] = ''.join(rows)
        svg.append('<text class="bh %s" x="%.1f" y="%d" text-anchor="%s">%s %s</text>' % (
            side, cx + (-tot if left else tot) * sc, y - 6, 'start' if left else 'end', DIR_WORD[name] + ' 요인', esc(pp_txt(sp['total'][name]))))
    svg.append('<line class="bzero" x1="%.1f" x2="%.1f" y1="%d" y2="%d"/>' % (cx, cx, y - 12, y + bh + 6))
    nx = cx + net * sc
    svg.append('<g class="bnet"><path d="M%.1f %d L%.1f %d L%.1f %d Z"/><text x="%.1f" y="%d" text-anchor="%s">순 %s</text></g>' % (
        nx - 7, y + bh + 14, nx + 7, y + bh + 14, nx, y + bh + 3, nx + (12 if net < 0 else -12), y + bh + 15, 'start' if net < 0 else 'end', esc(pp_txt(net))))
    svg.append('</svg>')
    return ('<div class="kbal" id="bal-%s" data-k="%s"><div class="bal"><div class="hd"><b>감소 요인 · 증가 요인 균형</b> <span class="eqline" id="eq-%s">%s</span></div>%s'
            '<div class="lg2"><div class="lgc lgl">%s</div><div class="lgc lgr2">%s</div></div></div></div>' % (
                kind, kind, kind, eq, ''.join(svg), legend['decrease'], legend['increase']))


def _section3(ctx):
    if ctx.bundles is None:
        return ('<section id="s3">%s%s%s%s</section>' % (_hd('3', '원인 후보', '그 layout 변화 중 무엇이 vi 를 바꿨나?'),
                                                           _rerun_line('원인 후보'), _detail_shell(ctx), _nx([('s4', '4 결과'), ('s2', '2 제품 대응')])))
    design = ctx.design or {}
    same_res = all((design.get(k) or {}).get('status') == 'available' and 'resolution' in (design[k].get('negligible') or []) for k in KINDS)
    how = ['<p style="margin:3px 0">한 layout 변화는 여러 전극 쌍의 불량을 함께 바꿉니다. 그래서 변화 하나(쌍 × 방식 × 레이어)를 \'무엇이 바뀌었나\' 기준으로 정확히 한 원인 후보에 통째로 넣습니다. '
           '카드의 값은 증가한 것과 감소한 것을 부호대로 더한 순 변화입니다. 모든 원인 후보의 순 변화를 더하면 보정값(CRP)을 뺀 전체 변화와 같고, 같은 변화가 두 카드에 나오지 않습니다.</p>',
           '<p style="margin:3px 0">원인 후보는 종류(line·pixel)마다 두 구획으로 나뉩니다. <b>순 변화 방향 요인</b>은 원인 후보 순 변화(보정값 차이 제외)의 부호와 같은 방향의 후보이고, <b>반대 방향 요인</b>은 나머지입니다. 보정값 차이가 커서 vi 의 부호와 달라지면 \'원인 후보 순 변화 방향\' 이라고 씁니다. '
           '각 구획은 |순 변화|가 큰 순서로 세운 순위입니다. 순 변화 방향은 최대 7 개(최소 5), 반대 방향은 최대 3 개(최소 2)를 보여 주고, 구획 합의 2%% 미만이 나오면 거기서 멈춥니다. 나머지는 접힌 표에 있고, 순 변화가 0 인 후보는 맨 끝 줄에 있습니다. '
           '번호는 구획 안 순위이며 균형 막대·범례·카드 목록이 같습니다. 몫 = 후보 순 변화 ÷ 그 구획 합입니다. 상쇄 비율 = |반대 방향 요인 합| ÷ |순 변화 방향 요인 합| 입니다. 총 변동은 %s 입니다.</p>' % TOTAL_TEXT,
           '<p style="margin:3px 0">균형 막대는 가운데가 0 이고 왼쪽이 감소 요인, 오른쪽이 증가 요인입니다. 조각 하나가 후보 하나이고 ▲ 가 순 변화입니다. '
           '카드 아래 막대도 가운데가 0 입니다. 진하고 굵은 막대는 후보의 순 기여, 옅고 얇은 막대는 후보 안의 감소(왼쪽)·증가(오른쪽)이며 같은 종류의 카드는 눈금이 같습니다. '
           '후보 안에서 감소와 증가가 모두 있고 그 합이 |순 변화|의 2 배 이상이면 "내부 상쇄 큼" 으로 표시합니다.</p>',
           '<p style="margin:3px 0"><a href="#s4" data-go="s4">4 결과</a>의 \'가장 많이 달라진 쌍\' 은 쌍마다 모든 레이어의 변화를 합친 값이고, 여기 카드는 같은 변화를 \'무엇이 바뀌었나\' 기준으로 다시 묶은 값입니다. '
           '쌍 하나가 두 카드 이상으로 나뉠 수 있어 1 의 쌍 기준과 다를 수 있습니다.</p>',
           '<p style="margin:3px 0">카드(한 줄)를 누르면 오른쪽 상세 패널이 그 원인 후보의 쌍·불량 방식·자리로 바뀝니다. 카드 끝의 ▸ 를 누르면 그 자리에서 레이어별 내역과 얼마나를 펼칩니다. 어떤 불량이 어떻게 변했는지는 상세 패널의 \'불량 방식\' 탭에 있습니다. '
           '[제품 대응] 은 2 제품 대응의 그 칸을 강조합니다. 위 수치 블록의 줄을 누르면 균형 막대와 목록이 line 과 pixel 로 바뀝니다.</p>']
    if not same_res:
        how.append('<p style="margin:3px 0">원인 후보 값은 실제 vi 차이이며 해상도 차이 몫도 들어 있습니다. 해상도 몫을 뺀 값은 <a href="#s6" data-go="s6">6 해상도 차이 비교</a>에 있습니다.</p>')
    hub = ctx.bundles.get('hub_min') or 3
    fraction = ctx.bundles.get('hub_min_fraction')
    share = ctx.bundles.get('hub_min_share')
    how.append('<details id="assign-rules"><summary class="small">배정 규칙 ▸</summary><ol class="small">'
               '<li>CNO 는 전극 속성으로만 정합니다. 신규·삭제 전극이면 그 전극 카드, 바뀐 레이어가 있는 공통 전극이면 그 전극의 레이어 변경 카드, 아니면 CNO 단독 카드입니다.</li>'
               '<li>전극이 하나인 불량은 그 전극이 신규·삭제면 전극 카드, 그 레이어 상태가 바뀌었으면 레이어 변경 카드, 아니면 동일 전극 카드입니다. 전극이 3 개 이상 걸리면 쌍 카드입니다.</li>'
               '<li>두 전극 중 한쪽만 신규·삭제이면 그 전극 카드(모든 레이어와 CNO)입니다. 둘 다이면 쌍 카드입니다.</li>'
               '<li>두 전극이 모두 공통이고 한쪽만 그 레이어 상태가 바뀌었으면 그 전극의 레이어 변경 카드입니다. 둘 다 바뀌었으면 쌍 카드입니다.</li>'
               '<li>나머지에서는 설계가 변한 상대가 많은 전극을 다중 쌍 변화(허브)로 봅니다. 증가인지 감소인지는 묻지 않습니다. 한쪽만 허브이면 그 허브 카드, 양쪽이면 변한 상대가 더 많은 쪽, 같으면 쌍 카드입니다. 허브가 없으면 쌍 카드입니다.</li></ol>'
               '<p class="small" style="margin:3px 0">허브 기준: 변한 상대가 %d 개 이상이고 전체 상대의 %s 이상입니다. 허용 오차 안의 변화(잡음)는 변한 상대로 세지 않습니다%s. 기준을 바꿨을 때의 차이는 부록의 허브 민감도 표에 있습니다.</p>'
               '<p class="small" style="margin:3px 0">상대별 증가·감소는 line·pixel 순 변화를 합친 부호입니다.</p></details>' % (
                   hub, share_txt(fraction if fraction is not None else 0.5),
                   '' if not share else '. 한 종류의 총 변동 %s 이상인 상대만 셉니다' % share_txt(share)))
    out = ['<section id="s3">', _hd('3', '원인 후보', '그 layout 변화 중 무엇이 vi 를 바꿨나?'), _dirband(ctx), ''.join(_balance(ctx, kind) for kind in KINDS)]
    notes = []                    # folded under the lists: the type table, how to read, the assignment rules
    if ctx.v2:
        notes.append('<h3>유형별 합계<span class="muted" style="font-weight:400">(원인 후보를 유형으로 묶은 값 · 증가는 순 변화 &gt; 0 인 후보의 합, 감소는 &lt; 0 인 후보의 합)</span></h3>'
                   '<div class="scrl" style="max-height:none">%s</div>%s'
                   '<p class="muted small" style="margin:3px 0">CNO 는 전극 카드(신규·삭제·레이어 변경)에 들어가면 그 유형에 포함됩니다. 독립 CNO 만 "CNO 단독" 행에 있습니다.</p>' % (
                       _types_table(ctx), _types_crp_line(ctx)))
    notes.append('<div class="how" id="how-to-read"><b>읽는 법</b>%s</div>' % ''.join(how))
    notes.append('<div class="ex"><b>용어 예시</b><table><tr><td class="muted">전극 카드</td><td>Test2 — B 에서 삭제(SD1·PXL) <span class="muted">(전극의 모든 레이어와 CNO 를 한 카드에)</span></td></tr>'
               '<tr><td class="muted">레이어 변경</td><td>Data — 레이어 변경(GAT2 추가 · SD1 제거)</td></tr>'
               '<tr><td class="muted">다중 쌍 변화</td><td>Scan3 · SD1 — 다중 쌍 변화(8 쌍: 증가 4 · 감소 4)</td></tr>'
               '<tr><td class="muted">몫</td><td>그 종류·그 방향 합계에서 카드의 순 변화가 차지하는 비율</td></tr></table></div>')
    ranking = ctx.bundles['ranking']
    out.append('<div class="s3two"><div class="s3cards" id="s3-cards">')       # left: the cards; right: the detail panel (sticky)
    for kind in KINDS:                                    # one list per kind; the band (or #k=) shows one of them
        rk = ranking[kind]
        out.append('<div class="klist" id="klist-%s" data-k="%s">' % (kind, kind))
        if rk.get('status') != 'ok':
            out.append('<h3>%s 을 바꾼 변화</h3><p class="muted" id="cards-%s-none">%s</p></div>' % (kind, kind, ' '.join(_skip_lines(ctx, kind) or [])))
            continue
        if not ctx.v2:
            cards = _cards_of(ctx, rk.get('cards'))
            if not cards:
                out.append('<h3>%s 을 바꾼 변화</h3><p class="muted" id="cards-%s-none">%s 변화는 원인 후보마다 아주 작아 따로 꼽지 않습니다.</p></div>' % (kind, kind, kind))
                continue
            out.append('<h3>%s 을 바꾼 변화 %d 개 <span class="muted" style="font-weight:400">(총 변동 몫 큰 순 · 카드 몫 합 %s)</span></h3><div class="bgrid" id="cards-%s">%s</div>' % (
                kind, len(cards), share_txt(rk.get('cover')), kind, ''.join(_bundle_card(ctx, b, kind, n) for n, b in enumerate(cards, 1))))
            if rk.get('rest'):
                out.append('<details id="bd-rest-%s"><summary>기타 %s 원인 후보 %d 개 ▸</summary>%s</details>' % (
                    kind, kind, len(rk['rest']), _rest_rows(ctx, kind, [(i, '') for i in rk['rest']])))
            out.append('</div>')
            continue
        out.append(_kind_cards(ctx, kind, rk, ranking['line'] if kind == 'pixel' else None))
        out.append('</div>')
    out.append('</div>%s</div>' % _detail_shell(ctx))
    out.append('<details id="s3-notes"><summary>유형별 합계 · 읽는 법 · 배정 규칙 ▸</summary>%s</details>' % ''.join(notes))
    out.append(_nx([('s4', '4 결과 — 불량은 어떻게 변했나'), ('s2', '2 제품 대응'), ('s6', '6 해상도 차이 비교')]))
    out.append('</section>')
    return ''.join(out)


# ---- 4 results ---------------------------------------------------------------------------------------------------------------

STAGE_OMIT = {'n_lt_8': '항목이 8 개 미만', 'tie_at_cut': '경계 동률', 'no_change': '변화 없음', 'atom_null': '값 없는 항목이 있음',
              'kind_not_computable': '계산 불가'}
VERDICT_TEXT = {'concentrated': '집중입니다', 'dispersed': '분산입니다', 'middle': '중간입니다'}


def _conc_html(ctx):
    if ctx.conc is None:
        return _rerun_line('집중도')
    out = ['<div class="note">막대는 각 쌍이 총 변동(증가·감소 크기 합)에서 차지하는 비율입니다. 실제 vi 기준이고 line 과 pixel 은 나누어 봅니다.</div><div class="g2">']
    for kind in KINDS:
        st = ((ctx.conc.get(kind) or {}).get('actual') or {}).get('pair') or {}
        if st.get('verdict') == 'omitted':
            verdict = '판정을 생략했습니다(%s)' % ' · '.join(STAGE_OMIT.get(r, r) for r in st.get('omit_reasons') or [])
        else:
            verdict = VERDICT_TEXT.get(st.get('verdict'), '—')
        k80 = st.get('k80')
        out.append('<div><h3>%s%s 쌍 단계</h3><p class="small">%s 총 변동의 80%% 는 쌍 %s 개에 있고, %s.</p>' % (
            chip(kind), kind, kind, '—' if k80 is None or st.get('status') != 'ok' else k80, esc(verdict)))
        rows = []
        for g in st.get('groups') or []:
            key = g['key']
            pair = ctx.pair_by_id.get(key)
            label = esc('–'.join(pair['electrodes'])) if pair else esc(key)
            if pair:
                label = '<a class="lk" href="%s&amp;to=explore">%s</a>' % (esc(_href('pair:' + key)), label)
            share = g.get('share')
            width = 0 if share is None else max(1, int(round(share * 100)))
            rows.append('<tr><td class="l">%s</td><td class="l"><span class="bar %s" style="width:%dpx"></span> %s</td></tr>' % (
                label, kind, min(width, 100) * 2, share_txt(share)))
        rest = st.get('rest')
        if rest:
            rows.append('<tr><td class="l muted">기타 %d 개</td><td class="l">%s</td></tr>' % (rest['count'], share_txt(rest.get('share'))))
        out.append('<table>%s</table></div>' % ''.join(rows))
    out.append('</div>')
    return ''.join(out)


def _section4(ctx):
    def line(kind):
        b = ctx.doc['kinds'][kind]['item_breakdown']
        gone = None if b.get('gone') is None else -b['gone']
        extra = ', CNO %s' % pp_txt(b.get('cno')) if kind == 'pixel' else ''
        if kind == 'pixel' and ctx.crp_differs():
            extra += ', 보정값(CRP) 차이 %s' % pp_txt(ctx.crp().get('delta'))
        return '%s 변화는 같은 불량의 증감 %s, 새 방식 %s, 없어진 방식 %s%s 로 이루어집니다.' % (
            kind, pp_txt(b.get('same')), pp_txt(b.get('new')), pp_txt(gone), extra)
    sums = [esc(line(k)) for k in KINDS]
    layers = (((ctx.conc or {}).get('line') or {}).get('actual') or {}).get('layer') or {}
    top = [g for g in layers.get('groups') or [] if not str(g['key']).startswith('CNO@') and g['key'] != 'CRP' and g.get('share') is not None]
    if layers.get('status') == 'ok' and top:
        sums.append('레이어로는 <a class="lk" href="#sel=layer:%s&amp;to=s2">%s</a> 에 line 변화가 가장 많이 몰려 있습니다(%s).' % (
            quote(top[0]['key'], safe=''), esc(top[0]['key']), share_txt(top[0]['share'])))
    out = ['<section id="s4">', _hd('4', '결과', '불량은 어떻게 변했나(어느 조합, 어떤 방식)?', sums),
           '<div class="g2"><div><h3>변화의 구성 — item 단위 분해 <span class="muted" style="font-weight:400">(위 = 증가, 아래 = 감소 · 합이 종류 전체 Δ)</span></h3>%s</div>' % _breakdown_table(ctx),
           '<div><h3>가장 많이 달라진 쌍 <span class="muted" style="font-weight:400">(실제 vi Δ, 종류별로 나눔 · 위 5)</span></h3>%s</div></div>' % _lists(ctx),
           _pair_find(ctx),
           '<details id="conc-pairs"><summary>쌍 기준으로 보기 ▸ (집중도 그래프)</summary>%s</details>' % _conc_html(ctx),
           _nx([('s5', '5 분포 — 전극·레이어·이물 크기별'), ('s6', '6 해상도 차이 비교'), ('s3', '3 원인 후보로 돌아가기')]), '</section>']
    return ''.join(out)


# ---- 6 resolution difference ----------------------------------------------------------------------------------------------------

DESIGN_REASONS = {'rebase_check_failed': '환산 검산이 맞지 않습니다', 'compare2_without_rebase': '이 입력에는 환산 값이 없습니다'}


def _fmt_val(v):
    if v is None:
        return '—'
    if isinstance(v, (list, tuple)):
        return '×'.join('%g' % x for x in v)
    if isinstance(v, dict):
        return ' / '.join('%s %g' % (k, x) for k, x in sorted(v.items()))
    return ('%g' % v) if isinstance(v, (int, float)) else str(v)


def _design_unavailable(kind, d):
    st = d.get('status')
    if st == 'not_available':
        return '%s 은 해상도 비교 값이 이 입력에 없습니다. %s' % (kind, RERUN)
    reasons = ' · '.join(DESIGN_REASONS.get(r, r) for r in d.get('reasons') or []) or '사유 없음'
    if st == 'not_evaluated':
        return '%s 은 판정을 생략했습니다(%s).' % (kind, reasons)
    return '%s 은 지금 나눌 수 없습니다(%s).' % (kind, reasons)


def _design_list(ctx, kind, d, increase, area=False):
    entries = d.get('top_increase' if increase else 'top_decrease') or []
    allp = sorted((p for p in ctx.doc['pairs'] if p['kind'] == kind and isinstance(p.get('delta'), (int, float))),
                  key=lambda p: (-p['delta'] if increase else p['delta'], p['pair_id']))
    rank = {p['pair_id']: i + 1 for i, p in enumerate(allp)}
    lis = []
    for i, e in enumerate(entries):
        pid = e.get('pair_id')
        label = '–'.join(e.get('electrodes') or []) if pid else e['key']
        name = '<a class="lk" href="%s&amp;to=explore">%s</a>' % (esc(_href('pair:' + pid)), esc(label)) if pid in ctx.pair_by_id else esc(label)
        moved = pid in rank and rank[pid] != i + 1
        lis.append('<li><span class="n">%d</span><span><b>%s</b>%s</span><span class="v">%s<span class="sub2">A 대비 %s · 실제 Δ %s</span></span></li>' % (
            i + 1, name, ('<span class="badge b-mv">실제 순위 %d위</span>' % rank[pid]) if moved else '',
            pp_cell(e['design_delta'], None), esc(spct(e['design_a_relative']) if e.get('design_a_relative') is not None else '—'), esc(pp_txt(e.get('actual_delta')))))
    return '<div><div class="muted">%s%s · %s 상위 5 쌍</div><ul class="lst" id="d-%s-%s">%s</ul></div>' % (
        chip(kind), kind, ('악화(%s 증가)' if increase else '개선(%s 감소)') % ('면적당 설계 몫' if area else '설계 몫'), 'w' if increase else 'b', kind,
        ''.join(lis) or '<li class="muted">해당하는 쌍이 없습니다</li>')


def _unit_area(product):
    """unit area (µm²) of a product from `unit_size_um` (config value), or None."""
    size = product.get('unit_size_um')
    if isinstance(size, (list, tuple)) and len(size) == 2 and all(isinstance(v, (int, float)) for v in size):
        return size[0] * size[1]
    return None


def _screen_name(d):
    """The name of the unit size + unit count segment: '화면 면적 차이', or '화면 면적·조건 차이' when other conditions differ."""
    return '화면 면적·조건 차이' if (d.get('names') or {}).get('condition_differences') else '화면 면적 차이'


def _area_rows(A, B, row, area):
    """The unit area and screen area rows of the condition table (area reading only)."""
    if not area:
        return ''
    ua, ub = _unit_area(A), _unit_area(B)
    res_a, res_b = A.get('resolution'), B.get('resolution')
    out = row('unit 면적(크기², µm²)', ua, ub, '' if not ua or not ub else '%.3f' % (ub / ua), ua == ub)
    if ua and ub and isinstance(res_a, (list, tuple)) and isinstance(res_b, (list, tuple)) and len(res_a) == len(res_b) == 2:
        sa, sb = ua * res_a[0] * res_a[1], ub * res_b[0] * res_b[1]
        out += row('화면 면적(unit 면적 × unit 수, µm²)', sa, sb, '%.3f' % (sb / sa) if sa else '', sa == sb)
    return out


def _unit_basis_reason(ctx):
    """R18:5: why 6 reads on the unit basis, from the area basis of compare2 (`rebase.area_basis.reasons`; when the ledger does not carry it,
    from the unit sizes of the two products): a missing unit size, any other reason named, or an older compare2 with no area basis at all."""
    design = ctx.design or {}
    kinds = [design.get(k) or {} for k in KINDS if (design.get(k) or {}).get('status') == 'available']
    if kinds and all('basis' not in d for d in kinds):
        return '이 ledger 는 면적 기준이 없는 옛 compare2 로 만들었습니다. 02·08 을 새 번들로 다시 실행하면 면적 기준이 나옵니다.'
    basis = (ctx.rebase or {}).get('area_basis') or {}
    reasons = [str(r) for r in basis.get('reasons') or []]
    if not reasons:
        products = (ctx.doc.get('inputs') or {}).get('products') or {}
        for role in ('reference', 'target'):
            unit = (products.get(role) or {}).get('unit_size_um')
            if not (isinstance(unit, (list, tuple)) and len(unit) == 2 and all(isinstance(v, (int, float)) and v > 0 for v in unit)):
                reasons = ['unit_size_missing']
                break
    if reasons == ['unit_size_missing']:
        return 'unit 크기 정보가 없어 unit 기준으로 비교합니다.'
    if reasons:
        return '면적 기준을 계산할 수 없어(%s) unit 기준으로 비교합니다.' % esc(' · '.join(reasons))
    return '면적 기준을 계산할 수 없어 unit 기준으로 비교합니다.'


def _section6(ctx):
    head = lambda sums=None, q='제품 크기(unit 개수) 차이를 빼면 설계 차이는?': _hd('6', '해상도 차이 비교', q, sums)
    nxt = _nx([('sA', '부록 근거·검산'), ('s4', '4 결과로 돌아가기')])
    design = ctx.design
    if design is None:
        return '<section id="s6">%s%s%s</section>' % (head(), _rerun_line('해상도 차이 비교'), nxt)
    avail = {k: (design.get(k) or {}) for k in KINDS}
    ok = [k for k in KINDS if avail[k].get('status') == 'available']
    outside = any((avail[k].get('names') or {}).get('show_outside_conditions') for k in ok)
    # K3 / L6: the area reading (per-area design + screen area) when every calculable kind has it; else the unit reading
    area = bool(ok) and all(avail[k].get('basis') == 'area' and 'design_area' in (avail[k].get('segments') or {}) and
                            'unit_size' in (avail[k].get('segments') or {}) for k in ok)
    if ok and len(ok) == 2 and all('resolution' in (avail[k].get('negligible') or []) for k in ok) and not outside and (
            not area or all('unit_size' in (avail[k].get('negligible') or []) for k in ok)):
        return '<section id="s6">%s%s</section>' % (head(['두 제품의 해상도가 같아 나눌 몫이 없습니다.']), nxt)
    ratios = ((ctx.rebase or {}).get('ratios') or {})
    wh = (ratios.get('line') or {}).get('r_WH')
    prods = ctx.doc['inputs']['products']
    A, B = prods['reference'], prods['target']
    u_a, u_b = _unit_area(A), _unit_area(B)
    if area:
        sums = ['<b>불량 면적(CA)을 unit 면적으로 나눈 "면적당 설계" 와 "화면 면적(unit 면적 × unit 수)" 으로 나눠 봤습니다(기준 = A).</b>']
        if wh is not None and u_a and u_b:
            sums.append('B 는 unit 수가 A 의 <b>%.2f 배</b>, unit 면적이 A 의 <b>%.2f 배</b>입니다.' % (wh, u_b / u_a))
    else:
        sums = ['<b>B 의 설계를 A 의 해상도(unit 개수)에 맞춰 다시 계산했습니다(기준 = A).</b>']
        if wh is not None:
            sums.append('B 는 panel 의 unit 개수(해상도 W×H)가 A 의 <b>%.1f 배</b>입니다.' % wh)
    tops_present = {}
    for k in KINDS:
        d = avail[k]
        if d.get('status') != 'available':
            sums.append(esc(_design_unavailable(k, d)))
            continue
        seg, neg = d['segments'], d.get('negligible') or []
        tops_present[k] = bool(d.get('top_increase') or d.get('top_decrease'))
        parts = ['%s vi 차이 <b>%s</b>' % (k, esc(pp_txt(d['delta'])))]
        out_txt = ''
        if (d.get('names') or {}).get('show_outside_conditions'):
            out_txt = ', 해상도 외 조건 차이는 <b>%s</b>' % esc(pp_txt(math.fsum([seg['crp'] or 0.0, seg['density'] or 0.0])))
        if area:
            screen = math.fsum([seg['unit_size'], seg['resolution']])
            sums.append('%s 중 면적당 설계 차이는 <b>%s</b>, %s는 <b>%s</b>(unit 크기 %s + unit 수 %s)%s 입니다.' % (
                parts[0], esc(pp_txt(0.0 if 'design_area' in neg else seg['design_area'])), _screen_name(d),
                esc(pp_txt(screen)), esc(pp_txt(seg['unit_size'])), esc(pp_txt(seg['resolution'])), out_txt))
            continue
        if 'design' in neg:
            tail = ('설계 차이 합은 0 에 가깝지만 쌍 사이에서 오르고 내린 곳이 있습니다.' if tops_present[k] else '설계 차이는 거의 없습니다.')
            sums.append('%s 중 해상도 차이는 <b>%s</b>%s 입니다. %s' % (
                parts[0], esc(pp_txt(0.0 if 'resolution' in neg else seg['resolution'])), out_txt, tail))
        else:
            res = '해상도 차이는 <b>%s</b>' % esc(pp_txt(0.0 if 'resolution' in neg else seg['resolution']))
            sums.append('%s 중 설계 차이는 <b>%s</b>, %s%s 입니다.' % (parts[0], esc(pp_txt(seg['design'])), res, out_txt))
    out = ['<section id="s6">', head(sums, '같은 면적이면 설계 차이는, 화면 면적 차이는?') if area else head(sums)]
    if not area and ok:
        out.append('<p class="muted" id="s6-unit-basis">%s 설계 차이에 unit 크기 차이가 함께 들어 있습니다.</p>' % _unit_basis_reason(ctx))
    if outside:
        out.append('<p class="muted" id="s6-outside-note">이 값은 환산 경로(설계 → 해상도 → 보정값 → 밀도)로 나눈 몫이라 '
                   '1·4 의 보정값(CRP) 차이와 다를 수 있습니다.</p>')
    if any(avail[k].get('status') == 'unavailable' for k in KINDS):
        out.append('<div class="band"><b>환산 대기</b> — 한 종류의 환산 값이 없어 그 종류는 나누지 않았습니다. 다른 종류는 정상입니다.</div>')
    # the conditions of the two products

    def ratio(key):
        r = (ratios.get('line') or {}).get(key)
        return '' if r is None else '%.3f' % r

    def row(label, a, b, r, same, unknown=False):
        verdict = '<span class="muted">판정 불가</span>' if unknown else (
            '<span class="muted">같음</span>' if same else '<span class="warn">다름</span>')
        return '<tr><td class="l">%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            esc(label), esc(_fmt_val(a)), esc(_fmt_val(b)), verdict, r)
    same_names, diff_rows = [], []
    for label, key, rk in (('유효 해상도 규칙(비)', None, 'r_rule'), ('glass 면적(µm²)', 'glass_um2', 'r_G'), ('가중 M', 'weights', 'r_M'),
                           ('이물 밀도 표(sha256)', 'density_sha256', None), ('CRP', 'crp', None)):
        if key is None:
            value = (ratios.get('line') or {}).get(rk)
            if not isinstance(value, (int, float)):       # R12:5: no ratio = cannot be judged (a dash, no error)
                diff_rows.append(row(label, '—', '—', '—', False, unknown=True))
                continue
            same = abs(value - 1.0) < 1e-12
            (same_names if same else diff_rows).append(label if same else row(label, '—', '—', ratio(rk), False))
            continue
        a, b = A.get(key), B.get(key)
        if key == 'density_sha256':
            a, b = {k: (v or '')[:8] for k, v in (a or {}).items()}, {k: (v or '')[:8] for k, v in (b or {}).items()}
        (same_names if a == b else diff_rows).append(label.split('(')[0] if a == b else row(label, a, b, ratio(rk) if rk else '', False))
    out.append('<h3>비교 조건</h3><table id="cond6"><tr><th class="l">항목</th><th>A</th><th>B</th><th>판정</th><th>비 B/A</th></tr>%s%s%s%s</table>' % (
        row('해상도 W×H', A.get('resolution'), B.get('resolution'), ratio('r_WH'), A.get('resolution') == B.get('resolution')),
        row('유효 해상도', A.get('effective_resolution'), B.get('effective_resolution'), '', A.get('effective_resolution') == B.get('effective_resolution')),
        row('unit 크기(µm)', A.get('unit_size_um'), B.get('unit_size_um'), '', A.get('unit_size_um') == B.get('unit_size_um')),
        _area_rows(A, B, row, area) + ''.join(diff_rows)))
    if same_names:
        out.append('<p class="muted small" id="cond6-same" style="margin:3px 0">같음: %s</p>' % esc(', '.join(same_names)))
    if outside:
        out.append('<div class="band"><b>해상도 외 조건이 다름</b> — 이 분리는 그 차이도 함께 담습니다(아래 "해상도 외 조건 몫").</div>')
    # the result table
    if area:
        cols = ('<th class="l">종류</th><th>실제 vi 차이</th><th>= 면적당 설계 차이</th><th>+ %s<span class="sub2">(unit 크기 + unit 수)</span></th>%s'
                '<th>면적당 설계 차이(A 대비 %%)</th>' % (esc(_screen_name(avail[ok[0]])), '<th>+ 해상도 외 조건</th>' if outside else ''))
    else:
        cols = '<th class="l">종류</th><th>실제 vi 차이</th><th>= 설계 차이 몫</th><th>+ %s 몫</th>%s<th>설계 차이(A 대비 %%)</th>' % (
            esc(((avail[ok[0]].get('names') or {}).get('resolution')) if ok else '해상도 차이'), '<th>+ 해상도 외 조건 몫</th>' if outside else '')
    rows = []
    for k in KINDS:
        d = avail[k]
        if d.get('status') != 'available':
            rows.append('<tr><td class="l"><span class="badge">%s</span></td><td colspan="%d" class="l muted">%s</td></tr>' % (
                k, 4 + (1 if outside else 0), esc(_design_unavailable(k, d))))
            continue
        seg, neg, rel = d['segments'], d.get('negligible') or [], d.get('a_relative') or {}

        def cell(name, value):
            return '<td>%s</td>' % (pp_cell(0.0, None) if name in neg else pp_cell(value, None))
        if area:
            screen = '<td>%s</td>' % pp_cell(math.fsum([seg['unit_size'], seg['resolution']]), None)
            rows.append('<tr><td class="l"><span class="badge">%s</span></td><td>%s <span class="muted small">(A 대비 %s)</span></td>%s%s%s<td><b>%s</b></td></tr>' % (
                k, pp_cell(d['delta'], None), esc(spct(d['delta_a_relative']) if d.get('delta_a_relative') is not None else '—'),
                cell('design_area', seg['design_area']), screen,
                ('<td>%s</td>' % pp_cell(math.fsum([seg['crp'] or 0.0, seg['density'] or 0.0]), None)) if outside else '',
                esc('0%' if 'design_area' in neg else (spct(rel.get('design_area')) if rel.get('design_area') is not None else '—'))))
            continue
        rows.append('<tr><td class="l"><span class="badge">%s</span></td><td>%s <span class="muted small">(A 대비 %s)</span></td>%s%s%s<td><b>%s</b></td></tr>' % (
            k, pp_cell(d['delta'], None), esc(spct(d['delta_a_relative']) if d.get('delta_a_relative') is not None else '—'),
            cell('design', seg['design']), cell('resolution', seg['resolution']),
            ('<td>%s</td>' % pp_cell(math.fsum([seg['crp'] or 0.0, seg['density'] or 0.0]), None)) if outside else '',
            esc('0%' if 'design' in neg else (spct(rel.get('design')) if rel.get('design') is not None else '—'))))
    if area:
        out.append('<h3>결과 — 실제 차이 = 면적당 설계 차이 + %s%s</h3><table id="design-table"><tr>%s</tr>%s</table>' % (
            esc(_screen_name(avail[ok[0]])), ' + 해상도 외 조건' if outside else '', cols, ''.join(rows)))
        split = ''.join('<tr><td class="l"><span class="badge">%s</span></td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            k, pp_cell(avail[k]['segments']['unit_size'], None), pp_cell(avail[k]['segments']['resolution'], None),
            pp_cell(math.fsum([avail[k]['segments']['unit_size'], avail[k]['segments']['resolution']]), None)) for k in ok)
        out.append('<details id="area-split"><summary>화면 면적 차이를 unit 크기와 unit 수로 나눠 보기 ▸</summary><table><tr><th class="l">종류</th>'
                   '<th>unit 크기 몫</th><th>unit 수 몫</th><th>합 = 화면 면적 차이</th></tr>%s</table>'
                   '<p class="muted small">%s</p></details>' % (split, 'unit 크기가 같아 unit 크기 몫은 0 입니다.' if u_a == u_b else
                   'unit 면적이 커지면 같은 면적당 설계라도 unit 당 불량 면적이 늘고(unit 크기 몫), unit 수가 줄면 그만큼 줄어듭니다(unit 수 몫).'))
    else:
        out.append('<h3>결과 — 실제 차이 = 설계 차이 몫 + 해상도 차이 몫%s</h3><table id="design-table"><tr>%s</tr>%s</table>' % (
            ' + 해상도 외 조건 몫' if outside else '', cols, ''.join(rows)))
    # top pairs
    if ok:
        if area:
            out.append('<div class="note">면적당 설계 몫은 B 의 불량 면적을 unit 면적비(A ÷ B)로 맞춰 구합니다. 그래서 unit 크기가 달라도 같은 면적끼리 비교됩니다. '
                       '쌍마다 %p 순위가 바뀔 수 있고, 실제로는 악화지만 설계로는 개선으로 보일 수 있습니다. A 대비 % 의 순서는 바뀌지 않습니다.</div>')
        else:
            out.append('<div class="note">해상도 몫을 뺄 때 모든 쌍의 B 값에 같은 비율(A unit 개수 ÷ B unit 개수)을 곱합니다. 그래서 빠지는 양은 B 값이 큰 쌍일수록 큽니다. '
                       '%p 순위가 바뀌고, 실제로는 악화지만 증가한 폭이 unit 개수 증가율보다 작으면 설계로는 개선으로 보일 수 있습니다. A 대비 % 의 순서는 바뀌지 않습니다.</div>')
        lists = ''.join(_design_list(ctx, k, avail[k], inc, area) for k in ok for inc in (True, False))
        out.append('<h3 id="design-top">%s 상위 쌍 <span class="muted" style="font-weight:400">(%s 기준 · 실제 Δ 순위와 달라진 쌍에 "실제 순위" 표시)</span></h3>'
                   '<p class="muted small">"A 대비 %%" = %s ÷ A 값입니다. 쌍을 누르면 3 의 상세 패널이 그 쌍으로 바뀝니다.</p><div class="g2">%s</div>' % (
                       '면적당 설계 차이' if area else '설계 차이', '면적당 설계 차이 몫' if area else '설계 차이 몫',
                       '면적당 설계 몫' if area else '설계 차이 몫', lists))
    if area:
        out.append('<div class="how"><b>어떻게 계산하나 (요약)</b><ol>'
                   '<li>λ = 이물 밀도 × 불량 면적(CA) × 화면 환산 계수입니다. CA × unit 수 = (CA ÷ unit 면적) × (unit 면적 × unit 수) 이므로 "면적당 불량 면적" 과 "화면 면적" 의 곱으로 쓸 수 있습니다.</li>'
                   '<li>B 의 CA 를 A 의 unit 면적으로 환산한 상태를 중간에 둡니다. 경로는 A → <b>면적당 설계</b> → <b>unit 크기</b> → <b>unit 수</b>%s → B 입니다.</li>'
                   '<li>합 = 실제 차이이고, 구간마다 그 단계의 시작·끝 값으로 비율을 따로 구합니다(vi 는 λ 에 정비례하지 않기 때문).</li></ol></div>' % (
                       ' → 해상도 외 조건' if outside else ''))
        out.append('<div class="g2"><div class="panel"><b>장점</b><ul style="margin:4px 0 0;padding-left:18px"><li>unit 크기가 달라도 같은 면적 기준으로 설계를 비교할 수 있습니다.</li>'
                   '<li>같은 제품군이면 추가 가정 없이 계산합니다.</li></ul></div>'
                   '<div class="panel"><b>한계</b><ul style="margin:4px 0 0;padding-left:18px"><li>2×2 창 경계 효과는 면적당 설계 몫에 남습니다.</li>'
                   '<li>면적당 설계 차이는 "같은 면적이면" 의 비교입니다. 실제 panel 의 면적 차이는 화면 면적 차이에 있습니다.</li>'
                   '<li>unit 크기가 config 값일 때만 씁니다(유도 값이면 unit 기준으로 내립니다).</li>'
                   '<li>몫의 순서 기준이며, 쌍 하나의 몫 합은 그 쌍의 실제 Δ 와 조금 다를 수 있습니다. 같아지는 것은 종류 전체 합계뿐입니다.</li></ul></div></div>')
    else:
        out.append('<div class="how"><b>어떻게 계산하나 (요약)</b><ol>'
                   '<li>같은 제품군이면 밀도 표·glass·가중·CRP 가 같아 해상도만 다릅니다.</li>'
                   '<li>λ 는 해상도(unit 개수)에 정비례합니다. B 의 λ 를 A/B unit 개수 비로 줄이면 "B 설계 @ A 해상도"가 됩니다.</li>'
                   '<li>기준은 A 입니다. B 의 unit 개수가 A 보다 많으면 줄이고, 적으면 늘려서 A 해상도에 맞춥니다.</li>'
                   '<li><b>설계 차이</b> = vi(B@A) − vi(A), <b>해상도 차이</b> = vi(B) − vi(B@A) 이며, 합 = 실제 차이입니다.</li>'
                   '<li>vi 는 λ 에 정비례하지 않아, 각 몫을 나눌 때 그 단계의 시작·끝 값으로 비율을 따로 구합니다.</li></ol></div>')
        out.append('<div class="g2"><div class="panel"><b>장점</b><ul style="margin:4px 0 0;padding-left:18px"><li>unit 개수 차이로 달라진 vi 몫을 따로 보여 주어, 설계끼리 비교하기 쉽습니다.</li>'
                   '<li>같은 제품군이면 추가 가정 없이 계산합니다(λ 가 해상도에 정비례합니다).</li></ul></div>'
                   '<div class="panel"><b>한계</b><ul style="margin:4px 0 0;padding-left:18px"><li>unit 크기(형상) 차이는 설계 몫에 남습니다.</li>'
                   '<li>몫의 순서(설계 먼저 → 해상도) 기준이며, 반대 순서와 약간 다를 수 있습니다.</li>'
                   '<li>쌍 하나의 설계 몫 + 해상도 몫은 그 쌍의 실제 Δ 와 조금 다를 수 있습니다. 같아지는 것은 종류 전체 합계뿐입니다.</li></ul></div></div>')
    # detail steps + missing cells
    steps = []
    for k in ok:
        stt = ((ctx.rebase or {}).get(k) or {}).get('states') or {}
        steps.append('<tr><td class="l"><span class="badge">%s</span></td><td class="l wrap">%s</td></tr>' % (
            k, esc(' → '.join('%s %s' % (s, vi_txt(stt[s].get('vi'))) for s in ('S0', 'S1a', 'S1', 'S2', 'S3', 'S4') if s in stt))))
    miss = []
    for k in KINDS:
        for c in avail[k].get('missing_cells') or []:
            partial = ' · 일부 몫 없음' if c.get('scope') == 'item' and c.get('share_partial') else ''   # R10b:3: cno / cell never make a share
            miss.append('<tr><td class="l">%s</td><td class="l">%s</td><td class="l">%s</td><td class="l">%s</td><td>%s</td><td>%d / %d%s</td></tr>' % (
                k, esc(c.get('scope')), esc(c.get('density_layer')), esc(_fmt_val(c.get('diameter_um'))), esc(num(c.get('area_b_um2'))),
                c.get('item_count') or 0, c.get('cno_count') or 0, esc(partial)))
    out.append('<details><summary>계산 단계 자세히 ▸ (상태 · 구간 표)</summary><table>%s</table>%s</details>' % (
        ''.join(steps) or '<tr><td class="muted">없음</td></tr>',
        ('<h4>빠진 밀도 표 칸</h4><table id="missing-cells"><tr><th class="l">종류</th><th class="l">범위</th><th class="l">레이어</th><th class="l">크기</th><th>B 면적</th><th>item / CNO 수</th></tr>%s</table>' % ''.join(miss)) if miss else ''))
    rs = rebase_statuses(ctx)
    if rs:
        n_ok = sum(1 for s in rs if s == 'passed')
        line = ('<span class="ok">✓ 해상도 비교 검산 %d 건 모두 통과</span>' % n_ok) if n_ok == len(rs) else (
            '<span class="bad">해상도 비교 검산 %d 건 확인 필요</span>' % (len(rs) - n_ok))
    else:
        line = '<span class="muted">해상도 비교 검산 없음(대기)</span>'
    out.append('<div class="trust" id="trust6">%s</div>' % line)
    out.append(nxt)
    out.append('</section>')
    return ''.join(out)


# ---- the data bundle ----------------------------------------------------------------------------------------------------

def _item_label(item):
    upper, lower = item.get('upper_layer'), item.get('lower_layer')
    layer = upper if upper == lower else '%s–%s' % (upper, lower)
    return '%s %s' % (MODE_LABELS.get(item.get('failure_type'), item.get('failure_type')), layer)


def _item_occurrence(ctx, item_id):
    """[connected regions in A, in B] of an item from ledger_maps `occurrence` (the observed values), or None (no maps, no value,
    suppressed, or an older ledger-maps without the occurrence block)."""
    row = ctx.occ_by_item.get(item_id)
    if row is None or ((row.get('compare') or {}).get('kind') == 'suppressed'):
        return None
    na, nb = (row.get('reference') or {}).get('n'), (row.get('target') or {}).get('n')
    for n in (na, nb):
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            return None
    return [na, nb]


def _pair_bundle(ctx, pair):
    items = []
    for item in pair['items']:
        items.append({'id': item['item_id'], 'ft': item.get('failure_type'), 'ul': item.get('upper_layer'), 'll': item.get('lower_layer'),
                      'al': item.get('analysis_layer'), 'label': _item_label(item), 'pres': item.get('presence'),
                      'a': item.get('a'), 'b': item.get('b'), 'd': item.get('delta'), 'sizes': item.get('by_size') or [],
                      'af': bool(item.get('a_filtered')), 'bf': bool(item.get('b_filtered')),
                      'oc': _item_occurrence(ctx, item['item_id'])})
    return {'id': pair['pair_id'], 'no': pair.get('rank'), 'e': list(pair['electrodes']), 'kind': pair['kind'], 'ck': pair['change_kind'],
            'a': pair['a'], 'b': pair['b'], 'd': pair['delta'], 'pa': bool(pair['present_a']), 'pb': bool(pair['present_b']),
            'pct_a': pair.get('pct_a'), 'pct_b': pair.get('pct_b'), 'pct_d': pair.get('pct_delta'),
            'la': list(pair.get('layers_a') or []), 'lb': list(pair.get('layers_b') or []),
            'brk': pair.get('breakdown'), 'comp': pair.get('composition'), 'items': items}


def _num_pair(value):
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        return [value[0], value[1]]
    return None


def _map_scale(ctx, role, side):
    """The physical scale of one product's maps (pitch, unit window, region) from ledger_maps_v2; None when a value is missing
    (the report then draws the maps as before: no scale bar, no window outline)."""
    mdoc = ctx.maps['doc']
    pitch = (side.get('frame') or {}).get('pitch_um')
    window = (mdoc.get('window') or {}).get(role) or {}
    region = side.get('region') or {}
    wo, ws, ro = _num_pair(window.get('origin_px')), _num_pair(window.get('size_px')), _num_pair(region.get('origin_px'))
    shape = _num_pair((side.get('frame') or {}).get('shape_yx'))
    unit = _num_pair(window.get('unit_um'))
    region_size = _num_pair(region.get('size_px'))             # the caption writes "영역 W × H µm" from this x the pitch
    if not isinstance(pitch, (int, float)) or isinstance(pitch, bool) or pitch <= 0 or not (wo and ws and ro and shape):
        return None
    whole = ws[0] >= shape[1] and ws[1] >= shape[0]
    return {'p': pitch, 'wo': wo, 'ws': ws, 'ro': ro, 'rs': region_size, 'whole': whole, 'u': unit,
            'us': 'derived' if window.get('unit_source') == 'derived' else 'config',
            'r': (mdoc.get('options') or {}).get('range_um')}


def _map_sides(ctx):
    """Per product: the numbered pictures (file, layer, kind, size, origin, block, pixel count of every number), the item numbers
    (item_id, pair, layer, status) and the electrode numbers. The PNG bytes are in id="map-png"."""
    out = {}
    side_doc = ctx.maps['doc'].get('sides') or {}
    for role, key in ROLE_KEYS:
        side = side_doc.get(role) or {}
        maps = []
        for entry in sorted(side.get('maps') or [], key=lambda m: m['file']):
            height, width = entry['shape_yx']
            maps.append({'f': entry['file'], 'l': entry['layer'], 'k': entry['kind'], 'w': width, 'h': height,
                         'o': list(entry.get('origin_px') or [0, 0]), 'b': entry.get('block_px') or 1, 'n': entry.get('numbers') or {}})
        out[key] = {'sc': _map_scale(ctx, role, side), 'maps': maps, 'els': [{'n': e['number'], 'name': e['name']} for e in side.get('electrodes') or []],
                    'items': [{'n': i['number'], 'id': i['item_id'], 'p': i['pair_id'], 'k': i['kind'], 'l': (i.get('layers') or [None])[0],
                               's': i.get('status')} for i in side.get('items') or []]}
    return out


def _maps_bundle(ctx):
    if ctx.maps is None:
        return None
    mdoc = ctx.maps['doc']
    return {'status': mdoc.get('status'), 'reasons': list(mdoc.get('reasons') or []), 'bands': mdoc.get('bands') or [],
            'options': mdoc.get('options') or {}, 'sha256': ctx.maps['sha'], 'report': mdoc.get('report') or {},
            'sides': _map_sides(ctx), 'layers': _map_layers(ctx), 'shape_reason': _shape_reason(ctx)}


def _bundle(ctx, ledger_sha):
    doc = ctx.doc
    kinds = {}
    for kind in KINDS:
        k = doc['kinds'][kind]
        kinds[kind] = {'vi_a': k['vi_a'], 'vi_b': k['vi_b'], 'delta': doc['totals'][kind]['delta'],
                       'pct_delta': ctx.pct(doc['totals'][kind]['delta'], kind), 'breakdown': k['item_breakdown'],
                       'change_kinds': k.get('change_kinds')}
    changed = {kind: [p['pair_id'] for p in top_changed(doc, kind)] for kind in KINDS}
    risk = {kind: [p['pair_id'] for p in top_risk(doc, kind)] for kind in KINDS}
    demo = (changed['line'] or changed['pixel'] or [None])[0]
    if demo is None and doc['pairs']:
        demo = doc['pairs'][0]['pair_id']
    return {
        'schema_version': SCHEMA_VERSION, 'ledger': {'sha256': ledger_sha, 'status': doc.get('status'), 'reasons': list(doc.get('reasons') or [])},
        'options': {'top_pairs': ctx.top_pairs, 'top_electrodes': ctx.top_electrodes}, 'kinds': kinds,
        'crp': {'present': ctx.crp_differs(), 'delta': ctx.crp().get('delta')},
        'labels': {'change_kinds': ctx.change_labels, 'checks': CHECK_TEXT},
        'pairs': [_pair_bundle(ctx, p) for p in doc['pairs']],
        'electrodes': [{'n': e['electrode'], 's': e.get('status'), 'la': e.get('layers_a'), 'lb': e.get('layers_b'),
                        'pa': bool(e.get('present_a')), 'pb': bool(e.get('present_b')), 'ls': e.get('layer_state'),
                        'lss': e.get('layer_state_status'), 'mv': e.get('moved')} for e in doc['electrodes']],
        'cno': [{'e': r['electrode_id'], 'l': r['density_layer'], 'a': r['a'], 'b': r['b'], 'd': r['delta'], 'pres': r['presence']} for r in doc['cno']],
        'checks': doc.get('checks'), 'top': {'changed': changed, 'risk': risk}, 'demo_pair': demo,
        'default_unit': next((ids[0] for ids in (_ranked_ids(ctx, k) for k in KINDS if ctx.bundles and ctx.bundles['ranking'][k].get('status') == 'ok') if ids), None),
        'maps': _maps_bundle(ctx), 'report07': ctx.report07,
        'layer_order': ctx.layer_order, 'bundles': _bundles_data(ctx)}


def _bundles_data(ctx):
    """What the script needs of the cause candidates: id, title, the (electrode, layer) cells, the pairs with their share, card flag."""
    if ctx.bundles is None:
        return []
    in_cards = {i for kind in KINDS for i in _ranked_ids(ctx, kind)}
    positions = {item['item_id']: (p['pair_id'], n) for p in ctx.doc['pairs'] for n, item in enumerate(p['items'])}
    out = []
    for b in ctx.bundles.get('list') or []:
        t = btype(b)
        if t == 'pair':
            cells = [[e, b['layer']] for e in b.get('electrodes') or []]
        elif t == 'layer_change' and b.get('from') is not None and not b.get('layers'):
            cells = [[b['electrode'], l] for l in (b.get('from') or []) + (b.get('to') or [])]
        elif t in ('electrode_new', 'electrode_removed', 'layer_change'):
            cells = [[b['electrode'], l] for l in b.get('layers') or ([b['layer']] if b.get('layer') else [])]
        else:
            cells = [[b['electrode'], b['layer']]]
        # R18:6: a pair carries the positions of its items in D.pairs[pair].items (a small integer each), not the item ids
        pairs, cno = {}, set()
        for r in b.get('rows') or []:
            ids = r.get('item_ids') or []
            cno.update(str(i) for i in ids if str(i).startswith('CNO:'))
            if r.get('pair_id'):
                p = pairs.setdefault(r['pair_id'], [0.0, 0.0, 0, set()])
                p[0] += r['net']
                p[1] += r['abs']
                p[2] += len(ids)
                for i in ids:
                    pos = positions.get(i)
                    if pos is not None and pos[0] == r['pair_id']:
                        p[3].add(pos[1])
        pairs = {k: [v[0], v[1], v[2], sorted(v[3])] for k, v in pairs.items()}
        out.append({'id': b['id'], 'title': bundle_title(b), 'ty': TYPE_NAME.get(t, t), 'nk': {k: (b.get('kinds') or {}).get(k, {}).get('net') for k in KINDS},
                    'ic': b['id'] in in_cards, 'cells': cells, 'pairs': pairs,
                    'cno': len(cno), 'layers': ctx.sort_layers({c[1] for c in cells}), 'els': sorted({c[0] for c in cells})})
    return out


# ---- document -----------------------------------------------------------------------------------------------------------

def _bar():
    nav = ' '.join('<a class="chip" href="#%s" data-go="%s">%s</a>' % (i, i, esc(name)) for i, name in SECTION_NAMES)
    return ('<div id="bar">%s<span class="sp"></span><span id="selinfo"><span class="muted">지금 선택:</span> '
            '<span id="sel-label"><span class="muted">없음</span></span></span> '
            '<button type="button" id="sel-clear" data-clear disabled>선택 해제</button></div>' % nav)


def render_html(doc, ledger_sha, top_pairs, top_electrodes, maps=None, report07=None, size_warning=False):
    """-> (html, data_bytes)."""
    ctx = Ctx(doc, top_pairs, top_electrodes, maps=maps, report07=report07)
    products = doc['inputs']['products']
    data = _json_embed(_bundle(ctx, ledger_sha))
    body = [
        '<!doctype html>\n<html lang="ko"><head><meta charset="utf-8"><title>설계 변경 영향 보고서 (합친 비교)</title><style>%s</style></head><body><main>' % CSS,
        '<h1>설계 변경 영향 보고서 — 합친 비교 <span class="muted" style="font-size:13px;font-weight:400">기준 A: %s · 대상 B: %s · ledger %s</span></h1>' % (
            esc(products['reference'].get('model_group')), esc(products['target'].get('model_group')), esc(doc.get('status'))),
        '<p class="purpose" id="purpose"><b>목적</b> 기준 제품 A 와 설계 변경 제품 B 를 비교해, 어떤 layout(전극·레이어) 변화가 이물 불량 지표 vi(line·pixel)를 '
        '얼마나 바꿨는지 한눈에 보이고, 설계자가 그 근거를 차례로 따라가 확인할 수 있게 합니다.</p>',
        _bar(), _bands(ctx, size_warning), _section1(ctx),
        _section2(ctx), _section3(ctx), _section4(ctx), _section5(ctx), _section6(ctx),
        _section_appendix(ctx),
        '</main><script type="application/json" id="report-data">%s</script>%s<script>%s</script></body></html>\n' % (
            data, _map_png_block(ctx) if ctx.maps is not None else '', SCRIPT)]
    return ''.join(body), len(data.encode('utf-8'))


# ---- output folder (same convention as ledger) ------------------------------------------------------------------------

def _owned_output(out):
    if not out.exists():
        return True
    if not out.is_dir():
        return False
    for path in sorted(out.iterdir()):
        if path.name.startswith(TMP_PREFIX):
            continue
        if path.name not in OWNED_NAMES:
            return False
        if path.name in ('execution.json', 'error.json'):
            try:
                if json.loads(path.read_text(encoding='utf-8')).get('command') != COMMAND:
                    return False
            except (OSError, ValueError, AttributeError):
                return False
    return True


def _publish(out, files):
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=TMP_PREFIX, dir=str(out)))
    try:
        for name, text in files:
            with open(str(tmp / name), 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(text)
        for name, _text in files:
            os.replace(str(tmp / name), str(out / name))
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def run_ledger_report(ledger_dir, out, top_pairs=DEFAULT_TOP, top_electrodes=DEFAULT_TOP, maps_dir=None, report07_dir=None):
    """Build and publish the report; returns the execution record (exit_code 0, or 3 for a partial ledger) or raises."""
    started = time.time()
    output = Path(out)
    if not _owned_output(output):
        raise NumericContractError('ledger_report_output_not_owned', f'{output}: use a new folder or a previous ledger-report folder')
    if (isinstance(top_pairs, bool) or isinstance(top_electrodes, bool) or not isinstance(top_pairs, int)
            or not isinstance(top_electrodes, int) or top_pairs < 1 or top_electrodes < 1):
        raise NumericContractError('ledger_report_option_invalid', '--top-pairs 와 --top-electrodes 는 1 이상의 정수여야 합니다')
    doc, sha, execution = _read_ledger(ledger_dir)
    maps = None if maps_dir is None else _read_maps(maps_dir, sha)
    link = _report07_link(report07_dir, output)
    page, data_bytes = render_html(doc, sha, top_pairs, top_electrodes, maps=maps, report07=link)
    html_bytes = len(page.encode('utf-8'))
    if html_bytes > HTML_WARN_BYTES:
        page, data_bytes = render_html(doc, sha, top_pairs, top_electrodes, maps=maps, report07=link, size_warning=True)
        html_bytes = len(page.encode('utf-8'))
    status = 'partial' if doc.get('status') == 'partial' else 'ok'
    reasons = list(doc.get('reasons') or []) if status == 'partial' else []
    exit_code = 0 if status == 'ok' else 3
    report = {'command': COMMAND, 'schema_version': SCHEMA_VERSION, 'status': status, 'reasons': reasons,
              'ledger_json_sha256': sha, 'ledger_status': doc.get('status'),
              'ledger_execution': None if execution is None else {'status': execution.get('status'),
                                                                    'exit_code': execution.get('exit_code')},
              'options': {'top_pairs': top_pairs, 'top_electrodes': top_electrodes},
              'sections': {'present': ['s1', 's2', 's3', 's4', 's5', 's6', 'sA'], 'placeholders': []},
              'report07': link['status'], 'html_bytes': html_bytes, 'data_bytes': data_bytes}
    if maps is not None:
        report['ledger_maps_json_sha256'] = maps['sha']
        report['ledger_maps_status'] = maps['doc'].get('status')
    record = {'command': COMMAND, 'status': status, 'reasons': reasons,
              'outputs': [str(output / name) for name in (HTML_NAME, 'report.json')],
              'ledger_dir': str(ledger_dir), 'options': report['options'],
              'started_at': datetime.fromtimestamp(started, timezone.utc).isoformat(),
              'finished_at': datetime.now(timezone.utc).isoformat(),
              'elapsed_seconds': round(time.time() - started, 3), 'exit_code': exit_code}
    if maps is not None:
        record['maps_dir'] = str(maps_dir)
    if report07_dir is not None:
        record['report07_dir'] = str(report07_dir)
    _publish(output, [(HTML_NAME, page), ('report.json', _json_text(report)), ('execution.json', _json_text(record))])
    error = output / 'error.json'
    if error.exists():
        error.unlink()
    return record


def main_ledger_report(ledger_dir, out, print_result, top_pairs=DEFAULT_TOP, top_electrodes=DEFAULT_TOP, error_stream=None,
                       maps_dir=None, report07_dir=None):
    """CLI body: prints the record, or leaves error.json + execution.json only and returns 2."""
    error_stream = error_stream or sys.stderr
    output = Path(out)
    try:
        record = run_ledger_report(ledger_dir, out, top_pairs=top_pairs, top_electrodes=top_electrodes, maps_dir=maps_dir,
                                   report07_dir=report07_dir)
    except (NumericContractError, OSError, ValueError, TypeError, KeyError) as exc:
        code = getattr(exc, 'code', 'input_or_config_error')
        print(f'오류: {exc}', file=error_stream)
        if code != 'ledger_report_output_not_owned' and _owned_output(output):
            try:
                for name in OWNED_NAMES - {'error.json', 'execution.json'}:  # a failed run leaves no stale report
                    path = output / name
                    if path.exists():
                        path.unlink()
                now = datetime.now(timezone.utc).isoformat()
                _publish(output, [
                    ('error.json', _json_text({'command': COMMAND, 'code': code, 'detail': str(exc), 'exit_code': 2})),
                    ('execution.json', _json_text({'command': COMMAND, 'status': 'error', 'reasons': [code], 'outputs': [],
                                                   'ledger_dir': str(ledger_dir), 'finished_at': now, 'exit_code': 2}))])
            except Exception as publish_error:  # still exit 2; say why error.json is missing
                print(f'오류: error.json 을 쓰지 못했습니다 (원래 오류 {code}): {publish_error}', file=error_stream)
        return 2
    print_result(record)
    return record['exit_code']
