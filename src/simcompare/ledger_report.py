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
BAND_TEXT = {
    'link_no_candidates': '04(candidates-change) 결과 폴더를 주지 않아 변경 위치(형상 태그, 위치 연결)를 보이지 않습니다.',
    'link_stale': '04 결과가 지금 입력과 다릅니다(지문이 맞지 않음). 변경 위치 연결을 껐습니다.',
    'link_invalid': '04 결과의 구조를 읽을 수 없어 변경 위치 연결을 껐습니다.'}


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
        self.changes = (maps['doc'].get('changes') if maps is not None else None)
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
        bundles = self.summary.get('bundles') if isinstance(self.summary.get('bundles'), dict) else None
        self.bundles = bundles
        self.bundle_by_id = {b['id']: b for b in (bundles or {}).get('list') or []}
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

    def link_status(self):
        """'ok' / 'no_candidates' / 'stale' / 'invalid' of the 04 link, or 'no_maps' when there is no --maps-dir."""
        if self.maps is None:
            return 'no_maps'
        return (self.changes or {}).get('link_status') or 'no_candidates'


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
        out.append('<div class="band info" id="band-no-maps"><b>지도 없음</b> — <code>--maps-dir</code> 를 주지 않아 지도·카드·변경 위치 자리가 '
                   '비어 있습니다. ledger-maps 를 실행한 뒤 다시 만드세요.</div>')
    else:
        mdoc = ctx.maps['doc']
        if mdoc.get('status') == 'partial':
            out.append('<div class="band partial" id="maps-partial"><b>지도 부분 결과</b> — ledger-maps 가 partial 로 끝났습니다(%s). '
                       '지도·카드의 값을 그대로 믿지 마세요.</div>' % esc(', '.join(mdoc.get('reasons') or ['partial'])))
        dropped = mdoc.get('cards_dropped_for_budget') or []
        if dropped:
            out.append('<div class="band partial" id="band-cards-budget"><b>카드 %d 장이 빠졌습니다</b> — 메모리 예산이 모자라 ledger-maps 가 '
                       '카드를 건너뛰었습니다(cards_budget_reduced). 지도와 값은 그대로입니다.</div>' % len(dropped))
        status = ctx.link_status()
        if status != 'ok':
            out.append('<div class="band info" id="band-changes"><b>변경 위치 없음</b> — %s</div>' % esc(
                BAND_TEXT.get('link_' + status, '04 변경 위치를 쓸 수 없습니다(%s).' % status)))
    if size_warning:
        out.append('<div class="band info" id="band-size"><b>큰 보고서</b> — HTML 이 8 MB 를 넘습니다. 열기가 느리면 '
                   '--top-cards 를 줄여 ledger-maps 를 다시 실행하세요.</div>')
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
    # the breakdown rows open the pair entry of section 3 and scroll there; CNO is no pair: it points to the electrode ledger (4)
    href = ' data-go="s5" title="CNO 는 쌍이 아니라 5 분포의 전극 장부에서 봅니다"' if cat == 'cno' else (
        ' data-href="#cat=%s:%s&amp;entry=pair" data-scroll="explore"' % (kind, cat))
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
            '%s<p class="muted" style="margin:3px 0 0">item = 불량 방식 × 양 끝 레이어. 쌍의 불량 구성이 달라도 이 분해에는 모두 들어갑니다. '
            '줄을 누르면 아래 탐색의 쌍 목록이 그 범주로 걸러집니다.</p>' % (chip('line'), chip('pixel'), ''.join(rows), note))


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


def _tag_names():
    try:
        from .compare2_report import KIND_TEXT
        return dict(KIND_TEXT)
    except Exception:  # the names are only labels; the codes still show
        return {}


def _tag_block(ctx):
    status = ctx.link_status()
    head = '<h3>두 번째 축 — 형상 변경 태그(04) <span class="muted" style="font-weight:400">관련 쌍 수</span></h3>'
    if status != 'ok':
        return head + '<p class="muted" id="tags-folded">04 변경 위치가 없어 접었습니다. %s</p>' % esc(
            BAND_TEXT.get('link_' + status, '지도 폴더(--maps-dir)와 04 결과(ledger-maps --candidates)가 필요합니다.'))
    counts = ((ctx.changes.get('summary') or {}).get('tag_pair_counts')) or {}
    names = _tag_names()
    rows = ''.join('<tr data-href="#cat=tag:%s&amp;entry=pair" data-scroll="explore"><td class="l">%s</td><td>%d</td><td>%d</td><td>%d</td></tr>' % (
        esc(quote(tag, safe='')), esc(names.get(tag, tag)), c.get('line', 0), c.get('pixel', 0), c.get('line', 0) + c.get('pixel', 0))
        for tag, c in sorted(counts.items()))
    if not rows:
        return head + '<p class="muted" id="tags-empty">연결된 형상 변경 태그가 없습니다.</p>'
    return (head + '<table id="tags"><thead><tr><th class="l">태그</th><th>line</th><th>pixel</th><th>합</th></tr></thead><tbody>%s</tbody></table>'
            '<p class="muted" style="margin:3px 0 0">쌍 수만 보입니다. 한 쌍에 태그가 여러 개 붙을 수 있어 Δ 를 더하면 중복됩니다. '
            '쌍 수는 04 가 고른 상위 위치에 연결된 쌍만 셉니다.</p>' % rows)


def _locations_line(ctx):
    status = ctx.link_status()
    if status != 'ok':
        return '<div class="trust2" id="locations">변경 위치: 04 결과 없음 — 위 띠를 보세요.</div>'
    s = ctx.changes.get('summary') or {}
    return ('<div class="trust2" id="locations">변경 위치 <b>%d곳</b>(04) · 위치에 연결된 쌍 <b>%d</b> · 미연결 쌍 <b>%d</b> '
            '<span class="muted">(보조 정보) (분모 %d 쌍 = 변화 없음을 뺀 쌍. 미연결은 "04 가 고른 상위 위치에 없음"이지 "변경 없음"이 아닙니다)</span></div>' % (
                s.get('roi_count', 0), s.get('linked_pairs', 0), s.get('unlinked_pairs', 0), s.get('pair_denominator', 0)))


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
DEF_TEXT = '(증가와 감소의 크기를 모두 더한 값)'
WHY_TEXT = {'both': '두 전극 모두 이 레이어의 상태가 바뀌었습니다.',
            'hubs': '두 전극 모두 상대 여럿과 함께 변하므로, 한쪽에 맡기지 않고 이 쌍 하나로 묶었습니다.',
            'multi': '전극이 셋 이상 걸린 item 이라 이 쌍 하나로 묶었습니다.'}
MIXED_WHY = '두 전극 중 한쪽에 맡길 수 없어 이 쌍 하나로 묶었습니다.'


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


def bundle_title(b):
    if b['type'] == 'pair':
        return '%s · %s' % ('–'.join(b.get('electrodes') or []), b.get('layer'))
    if b['type'] == 'move':
        return '%s · %s → %s' % (b['electrode'], '·'.join(b.get('from') or []) or '—', '·'.join(b.get('to') or []) or '—')
    return '%s · %s' % (b['electrode'], b.get('layer'))


def lk_bundle(b, label=None, to='s3'):
    return '<a class="lk" href="#unit=%s&amp;to=%s">%s</a>' % (esc(b['id']), to, esc(label or bundle_title(b)))


def _bundle_up(b):
    ln, pn = b['kinds']['line']['net'], b['kinds']['pixel']['net']
    big = ln if abs(ln or 0.0) >= abs(pn or 0.0) else pn
    return (big or 0.0) > 0


def unit_first(b):
    """What changed, in one sentence (the card head and the 1 summary use the same words)."""
    t, n = b['type'], b.get('partner_count') or 0
    e, layer = b.get('electrode'), b.get('layer')
    if t == 'new':
        return '%s 전극이 B 에서 %s 에 새로 놓였습니다.' % (e, layer)
    if t == 'gone':
        return '%s 전극이 B 의 %s 에서 빠졌습니다.' % (e, layer)
    if t == 'move':
        return '%s 전극이 A 의 %s 에서 B 의 %s 로 옮겨졌습니다.' % (e, '·'.join(b.get('from') or []) or '—', '·'.join(b.get('to') or []) or '—')
    if t == 'hub':
        if n == 0:                      # R10:6: a hub with no partner is one electrode with itself at both ends
            return '%s 전극의 %s 에서 이 전극 안의 불량이 변했습니다.' % (e, layer)
        inc, dec = b['atoms']['increase'], b['atoms']['decrease']
        if inc and dec:                 # R8:7: the signs are mixed
            return '%s 전극의 %s 에서 상대 %d 개와의 불량이 함께 변했습니다(늘어난 %d · 줄어든 %d).' % (e, layer, n, inc, dec)
        return '%s 전극의 %s 에서 상대 %d 개와의 불량이 함께 %s.' % (e, layer, n, '늘었습니다' if _bundle_up(b) else '줄었습니다')
    if t == 'pair':
        return '%s 쌍의 %s 불량이 변했습니다.' % ('–'.join(b.get('electrodes') or []), layer)
    return '%s 전극의 %s CNO 가 변했습니다.' % (e, layer)


def unit_what(ctx, b):
    t, n = b['type'], b.get('partner_count') or 0
    first = unit_first(b)
    if t == 'new':
        return first + (' 그 전극과 상대 %d 개 사이에 새 불량 방식이 생겼습니다.' % n if n else '')
    if t == 'gone':
        return first + (' 상대 %d 개와의 불량 방식이 없어졌습니다.' % n if n else '')
    if t == 'move':
        return first + (' 상대 %d 개와의 불량이 두 레이어에서 함께 바뀌었습니다.' % n if n else '')
    if t == 'pair':
        why = b.get('why') or []
        if len(why) > 1:
            return first + ' ' + MIXED_WHY
        if why == ['nohub']:
            return first + ' 같은 방향으로 함께 변한 상대가 %d 개 미만이어서 이 쌍 하나로 묶었습니다.' % (ctx.bundles.get('hub_min') or 3)
        return first + (' ' + WHY_TEXT[why[0]] if why and why[0] in WHY_TEXT else '')
    if t == 'hub':
        return first
    return first + ' 전극 단독 항목입니다.'


def _unit_badge(b):
    return {'new': '<span class="badge b-new">B 에 새로 놓임</span>', 'gone': '<span class="badge b-gone">B 에서 빠짐</span>',
            'move': '<span class="badge b-mv">레이어 이동</span>', 'hub': '<span class="badge">상대 여럿과 함께 변함</span>',
            'pair': '<span class="badge">이 쌍만 변함</span>', 'cno': '<span class="badge">CNO 만</span>'}[b['type']]


def _kind_phrase(kind, b):
    s = b['kinds'][kind]
    return '%s %s(%s 변화량의 %s)' % (kind, pp_txt(s['net']), kind, share_txt(s['share']))


def _kind_lines(ctx, kind, mode, state):
    """The sentences of one kind about its cause candidates (H4 suppression). mode 's1' or 's3'; state['defined'] says the
    amount of change was already defined once."""
    bd = ctx.bundles
    rk, st = bd['ranking'][kind], (bd.get('status') or {}).get(kind)
    if st == 'not_evaluated':
        why = ' · '.join(KIND_REASON.get(r, r) for r in (bd.get('reasons') or {}).get(kind) or []) or '값이 없습니다'
        return ['%s 원인 후보는 계산할 수 없습니다(%s).' % (kind, esc(why))]
    if st == 'no_change':
        out = ['%s 에는 item 변화가 없습니다.' % kind]
        if kind == 'pixel' and not _tiny((bd.get('crp') or {}).get('pixel')):
            out.append('pixel 변화는 보정값(CRP) 차이에서 나왔습니다.')
        return out
    cards = [ctx.bundle_by_id[i] for i in rk.get('cards') or [] if i in ctx.bundle_by_id]
    if not cards:
        return ['%s 변화는 원인 후보마다 아주 작아 따로 꼽지 않습니다.' % kind]
    where = '아래 카드' if mode == 's3' else '상위'
    amount = '변화량' if state['defined'] else '변화량' + DEF_TEXT
    state['defined'] = True
    rest = rk.get('rest') or []
    if not rest:
        out = ['%s %s은 원인 후보 %d 개에 모두 있습니다.' % (kind, amount, len(cards))]
    else:
        out = ['%s %s의 %s 는 %s %d 원인 후보의 변화입니다.' % (kind, amount, share_txt(rk['cover']), where, len(cards)),
               '80%% 까지는 %d 원인 후보가 필요합니다.' % rk['k80'],
               '나머지 %d 원인 후보가 %s 를 나눠 가집니다.' % (len(rest), share_txt(rk['rest_share']))]
    top = cards[0]
    out.append('가장 큰 %s 원인 후보는 %s 입니다.' % (kind, lk_bundle(top)))
    out.append(esc(unit_first(top)) + ('' if mode == 's3' else ' 이 원인 후보의 %s 변화는 %s 입니다.' % (
        kind, '%s(%s 변화량의 %s)' % (pp_txt(top['kinds'][kind]['net']), kind, share_txt(top['kinds'][kind]['share'])))))
    return out


def _vi_sentence(ctx, kind):
    kd, d = ctx.doc['kinds'][kind], ctx.doc['totals'][kind]['delta']
    if d is None:
        return '%s vi 는 값이 대기라 비교하지 않습니다.' % kind
    if d == 0:
        return '%s vi 는 A %s 에서 B %s 로 변하지 않았습니다.' % (kind, esc(vi_txt(kd['vi_a'])), esc(vi_txt(kd['vi_b'])))
    return '%s vi 는 A <b>%s</b> 에서 B <b>%s</b> 로 <b>%s</b> %s.' % (
        kind, esc(vi_txt(kd['vi_a'])), esc(vi_txt(kd['vi_b'])), esc(pp_txt(d)), '높아졌습니다' if d > 0 else '낮아졌습니다')


def check_statuses(ctx, with_rebase=False):
    """Every check status of the page: the ledger sums, the item breakdown, the bundles (and the rebase when asked)."""
    statuses = _sum_checks(ctx)[1] + _item_checks(ctx)[1]
    checks = ctx.doc.get('checks') or {}
    bd = checks.get('bundles')
    if isinstance(bd, dict):
        statuses.append('passed' if bd.get('duplicates') == 0 and bd.get('assigned') == bd.get('atoms') and bd.get('id_unique') else 'failed')
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
    ops = [_vi_sentence(ctx, kind) for kind in KINDS]
    if ctx.bundles is None:
        ops.append('원인 후보 요약은 이 입력에 없어 쓰지 않습니다. ' + RERUN)
    else:
        state = {'defined': False}
        for kind in KINDS:
            ops.extend(_kind_lines(ctx, kind, 's1', state))
        cards = [ctx.bundle_by_id[i] for kind in KINDS for i in ctx.bundles['ranking'][kind].get('cards') or [] if i in ctx.bundle_by_id]
        conflict = sorted((b for b in cards if b.get('conflict')), key=lambda b: (-max(b['kinds'][k]['share'] or 0.0 for k in KINDS), b['id']))
        if conflict:
            b = conflict[0]
            ops.append('%s 변화는 line 을 %s. 같은 변화가 pixel 을 %s.' % (
                lk_bundle(b), '늘렸습니다' if b['kinds']['line']['net'] > 0 else '줄였습니다',
                '늘렸습니다' if b['kinds']['pixel']['net'] > 0 else '줄였습니다'))
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
    if link['status'] == 'missing':
        return '<p id="link07" class="muted">07 보고서 없음 — --report07-dir 폴더에 report.html 이 없습니다.</p>'
    return '<p id="link07" class="muted">07 보고서 없음 — --report07-dir 를 주지 않았습니다. 가정 계산과 조건 Δ 는 07 에서 봅니다.</p>'


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


def _appendix_extra(ctx):
    """Checks of the bundles and the rebase, and the occurrence state counts (kept in the appendix)."""
    out = []
    checks = ctx.doc.get('checks') or {}
    rows = []
    bd = checks.get('bundles')
    if isinstance(bd, dict):
        rows.append(('원인 후보 묶음: 중복 0 · 배정 수 = 원자 수', 'passed' if bd.get('duplicates') == 0 and bd.get('assigned') == bd.get('atoms') and bd.get('id_unique') else 'failed'))
        for kind in KINDS:
            for name, c in ((bd.get('kinds') or {}).get(kind) or {}).items():
                rows.append(('원인 후보 %s · %s' % (kind, name), c.get('status', 'not_evaluated')))
    rs = rebase_statuses(ctx)
    if rs:
        rows.append(('해상도 비교(rebase) 검산 %d 건' % len(rs), 'passed' if all(s == 'passed' for s in rs) else (
            'failed' if 'failed' in rs else 'not_evaluated')))
    if rows:
        out.append('<h3>원인 후보 · 해상도 비교 검산</h3><table id="bundle-checks">%s</table>' % ''.join(
            '<tr><td class="l">%s</td>%s</tr>' % (esc(n), _check_cell(s)) for n, s in rows))
    else:
        out.append(_rerun_line('원인 후보·해상도 비교 검산'))
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
           '<p class="muted" id="cond-link">선택한 쌍의 조건 값은 <a href="#explore" data-go="explore">4 결과의 쌍 상세</a> 맨 아래에 있습니다. 전체는 아래에서 봅니다.</p>',
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
    def attrs(cls):
        return 'class="c %s" data-cell="1" data-layer="%s" data-href="%s"' % (cls, esc(layer), esc(_href('electrode:' + row['electrode']) + '&layer=' + quote(layer, safe='')))
    if state is None:
        if verified:
            return '<td %s></td>' % attrs('st-none')
        return '<td %s title="DL1 이 없어 이 레이어를 확인할 수 없습니다">%s</td>' % (attrs('st-unk'), muted('확인 불가'))
    symbol, text, cls = LAYER_SYMBOLS[state]
    basis = '' if verified else ' · 관찰 기준(DL1 없음)'
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


def _section2(ctx):
    doc = ctx.doc
    rows = doc['electrodes']
    layers = ctx.sort_layers({layer for row in rows for layer in (row.get('layer_state') or {})})
    unverified = [row for row in rows if row.get('layer_state_status') != 'verified']
    counts = {code: sum(1 for row in rows if row.get('status') == code) for code in ('common', 'new', 'removed')}
    moved = sum(1 for row in rows if row.get('moved'))
    summary = ('공통 <b>%d</b> · 신규 <b>%d</b> · 제거 <b>%d</b> · 레이어 이동 <b>%d</b>%s'
               '<span class="muted"> — 대응은 전극 이름 기준입니다. 이름이 바뀐 전극은 \'제거 + 신규\' 로 보입니다.</span>' % (
                   counts['common'], counts['new'], counts['removed'], moved,
                   (' · 확인 불가 <b>%d</b>' % sum(1 for row in rows if row.get('status') == 'unverifiable')) if any(
                       row.get('status') == 'unverifiable' for row in rows) else ''))
    # nothing new, removed or moved (and nothing unverifiable): the table says "no difference", so it is folded; any difference opens it
    unchanged = not (counts['new'] or counts['removed'] or moved or unverified)
    if unchanged:
        out = ['<section id="s2">', _hd('2', '제품 대응', '두 제품의 전극·레이어는 어떻게 다른가?'),
               '<details id="corr-details"><summary class="lead" id="corr-summary">%s <span class="muted">(차이 없음 — 눌러서 표 펼치기)</span></summary>' % summary]
    else:
        out = ['<section id="s2">', _hd('2', '제품 대응', '두 제품의 전극·레이어는 어떻게 다른가?'), '<p class="lead" id="corr-summary">%s</p>' % summary]
    if unverified:
        out.append('<div class="band info" id="band-no-dl1"><b>확인 불가</b> — DL1 전극 목록이 없어 %d 개 전극의 레이어 대응(공통·신규·제거·이동)을 '
                   '확인하지 못했습니다. 빈 칸은 없다는 뜻이 아니라 알 수 없다는 뜻입니다.</div>' % len(unverified))
    out.append('<p class="legend" id="corr-legend"><span class="st-both">●</span> 양쪽 · <span class="st-a">◀</span> A 만 · '
               '<span class="st-b">▶</span> B 만 · <span class="st-mv">⇄</span> 레이어 이동(A 전용 칸 ⇄<sub>A</sub> → B 전용 칸 ⇄<sub>B</sub>)'
               ' &nbsp;|&nbsp; 전극 이름 칸 색: <span class="hd-up3">증가</span> <span class="hd-dn3">감소</span> '
               '(line·pixel 중 A 대비 |Δ%%| 큰 쪽) · %s%s</p>' % (esc(SHADE_LEGEND), (
                   ' <span id="corr-observed" class="muted">· <b>관찰 기준</b>: DL1 이 없는 전극의 기호는 관찰된 레이어만 보인 것입니다(없다는 뜻이 아님)</span>')
                   if unverified else ''))
    out.append('<p class="searchrow"><label>전극 검색 <input type="search" id="el-q" autocomplete="off" placeholder="이름 일부"></label> '
               '<span id="el-count" class="muted">%d / %d</span></p>' % (len(rows), len(rows)))
    head = ''.join('<th data-layer="%s" data-href="%s" title="이 레이어를 선택">%s</th>' % (esc(layer), esc(_href('layer:' + layer)), esc(layer))
                   for layer in layers)
    body = []
    for row in rows:
        dirn, step = _shade(row)
        shade = ' hd-%s%d' % (dirn, step) if dirn else ''
        verified = row.get('layer_state_status') == 'verified'
        badge = esc(next((c['label'] for c in doc.get('electrode_status') or [] if c['code'] == row.get('status')), row.get('status')))
        body.append('<tr %s data-row="corr"><td class="l nm%s"><b>%s</b></td><td class="l"><span class="badge">%s</span></td>%s</tr>' % (
            _el_attrs(row['electrode']), shade, esc(row['electrode']), badge,
            ''.join(_layer_cell(row, layer, verified) for layer in layers)))
    out.append('<div class="s2row"><div class="s2tab"><div class="scrl"><table id="corr-table"><thead><tr><th class="l">전극</th><th class="l">상태</th>%s</tr></thead><tbody>%s</tbody></table></div></div>%s</div>' % (
        head, ''.join(body), _layout_maps(ctx)))
    if unchanged:
        out.append('</details>')
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
OVERLAY_REASONS = {'coords_no_candidates': '04 결과가 없어 A 와 B 의 위치 차이(offset)를 알 수 없습니다',
                   'coords_stale': '04 결과가 지금 입력과 달라 위치 차이를 쓸 수 없습니다'}


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
    if any(row.get('layer_state_status') != 'verified' for row in ctx.doc['electrodes']):
        return '전극·레이어 형상을 그릴 수 없습니다: DL1 이 없어 전극 그림(el)이 만들어지지 않았습니다.'
    return '전극·레이어 형상을 그릴 수 없습니다: ledger-maps 에 전극 그림(el)이 없습니다.'


def _overlay_info(ctx):
    overlay = ctx.maps['doc'].get('overlay') or {}
    if overlay.get('available'):
        return True, ''
    reason = overlay.get('reason')
    return False, OVERLAY_REASONS.get(reason, '겹쳐 보기를 쓸 수 없습니다') + (' (%s)' % reason if reason else '')


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


def _m_legend():
    def sw(css, text):
        return '<div><i class="sw" style="%s"></i>%s</div>' % (css, esc(text))
    return ('<div class="mleg" id="m3-legend"><b>범례</b>'
            + sw('border:1px solid var(--common);background:none', '전극 윤곽 (얇은 회색, 채움 없음)')
            + sw('border:3px solid var(--common);background:rgba(141,140,133,.18)', '선택 전극 (굵은 윤곽 + 옅은 채움)')
            + sw('border:2px dashed var(--common);background:none', '선택 쌍의 다른 전극 (점선 윤곽)')
            + sw('background:var(--line)', 'line 불량 영역 (선택 쌍)') + sw('background:var(--pixel)', 'pixel 불량 영역 (선택 쌍)')
            + '<details><summary class="small">범례 전체 ▸</summary>'
            + sw('border:2px dashed var(--pixel);background:none', '한쪽에만 있는 item (점선 윤곽)')
            + sw('background:var(--common);opacity:.3', '다른 쌍 (옅게)')
            + sw('border:3px dashed #ffd21f;background:#141414', 'unit 창 (노란 긴 점선 + 어두운 테두리, 왼쪽 위 "unit")')
            + sw('border:2px dashed var(--text-primary);background:none', '변경 위치 (04, 번호) · 보조 정보')
            + sw('border:2px dashed var(--text-primary);border-style:dotted double;background:none', '반복 위치로 옮겨 표시한 위치')
            + '<div class="muted">⌐ 창 경계에서 잘림 · ≫ 창보다 큼</div></details></div>')


# ---- section 3 panel (mrep/T8): two entries, pair list, card tabs, presence-split range tables -----------------------------------
# The list rows, the ROI rows, the card chooser and the range tables of every card are plain HTML (values copied from ledger.json /
# ledger_maps.json, nothing is added up here). The script only filters the list, shows one tab and draws the detail of the selection.

PAIR_SHOW = 20
CARD_TABS = (('same', '같은 불량 증감 상위'), ('comp', '구성 변경 상위'), ('all', '전체 |Δ|'))
CARD_MATCH_TEXT = {'matched': '같은 좌표로 맞췄습니다', 'not_compared': '같은 좌표로 비교하지 못했습니다', 'a_only': 'A 에만 있는 쌍입니다',
                   'b_only': 'B 에만 있는 쌍입니다'}
BASIS_CHIP = '<span class="basis">범위 · 장부</span>'
UNLINKED_TEXT = '변경 위치 미연결 — 04 가 고른 상위 위치에 없음'


def _roi_numbers(ctx):
    """{roi_key: number} (the order of the 04 list, from 1; the same numbers the maps draw)."""
    if not isinstance(ctx.changes, dict):
        return {}
    return {roi.get('roi_key'): i + 1 for i, roi in enumerate(ctx.changes.get('rois') or [])}


def _pair_link(ctx, pair_id):
    """([roi keys], [tag codes]) of the 04 link of a pair (empty without a usable 04)."""
    if ctx.link_status() != 'ok':
        return [], []
    entry = (ctx.changes.get('pairs') or {}).get(pair_id) or {}
    return list(entry.get('roi_keys') or []), list(entry.get('tags') or [])


def _roi_rows(ctx):
    if ctx.maps is None:
        return '<p class="muted" id="roi-none">지도 폴더(--maps-dir)가 없어 변경 위치 목록이 없습니다.</p>'
    status = ctx.link_status()
    if status != 'ok':
        return '<p class="muted" id="roi-none">04 결과가 없어 변경 위치 목록이 없습니다. %s</p>' % esc(
            BAND_TEXT.get('link_' + status, '지도 폴더(--maps-dir)와 04 결과(ledger-maps --candidates)가 필요합니다.'))
    rois = ctx.changes.get('rois') or []
    if not rois:
        return '<p class="muted" id="roi-none">04 가 고른 변경 위치가 없습니다.</p>'
    linked = {}
    for entry in (ctx.changes.get('pairs') or {}).values():
        for key in entry.get('roi_keys') or []:
            linked[key] = linked.get(key, 0) + 1
    body = []
    for i, roi in enumerate(rois):
        key = roi.get('roi_key')
        tags = ' '.join('<span class="badge b-tag">%s</span>' % esc(t) for t in roi.get('change_kind_text') or []) or muted('판정 불가')
        n = linked.get(key, 0)
        parts = []
        if (roi.get('map') or {}).get('status') == 'outside_map':
            parts.append('지도 밖')
        if roi.get('status') not in (None, 'ready'):
            parts.append('상태 %s' % roi.get('status'))
        if n == 0:
            parts.append('연결된 쌍 없음(미연결)')
        body.append('<tr data-roi="%s" data-href="%s&amp;entry=loc" tabindex="0"><td><b>#%d</b></td><td class="l">%s</td><td>%d</td><td class="l wrap">%s</td></tr>' % (
            esc(key), esc(_href('roi:' + str(key))), i + 1, tags, n, esc(' · '.join(parts)) if parts else muted('연결됨')))
    ambiguous = (ctx.changes.get('summary') or {}).get('ambiguous_cases') or 0
    note = ('<p class="muted" id="roi-ambiguous">모호한 case %d 건은 어느 쌍인지 정할 수 없어 연결하지 않았습니다(미연결과 같이 취급).</p>' % ambiguous) if ambiguous else ''
    return ('<div class="scrl"><table id="roi-table"><thead><tr><th>#</th><th class="l">형상 태그</th><th>연결된 쌍 수</th><th class="l">상태</th></tr></thead>'
            '<tbody>%s</tbody></table></div>%s<p class="muted">위치별 합계는 만들지 않습니다. 한 쌍이 여러 위치에 붙을 수 있고, 관련은 연결 근거이지 원인 확정이 아닙니다.</p>' % (
                ''.join(body), note))


def _pair_cats(pair):
    """Breakdown categories of a pair (the same rule as the pair counts of section 1: it has an item of that category)."""
    brk = pair.get('breakdown') or {}
    return [cat for cat, key in (('same', 'n_both'), ('new', 'n_new'), ('gone', 'n_gone')) if (brk.get(key) or 0) > 0]


def _pair_sort_key(pair):
    pct, delta = pair.get('pct_delta'), pair.get('delta')
    if pct is not None:
        return (0, -abs(pct), pair['pair_id'])
    return (1, -abs(delta), pair['pair_id']) if delta is not None else (2, 0, pair['pair_id'])


def _b_ranks(doc):
    """{pair_id: rank by B value} with the rule of the 'riskiest in B' list of section 1 (B vi as % of the A vi of the kind; pairs
    present in B with a value only; ties by pair_id). Pairs without a B value get no rank."""
    rows = sorted((p for p in doc['pairs'] if p['present_b'] and p.get('pct_b') is not None), key=lambda p: (-p['pct_b'], p['pair_id']))
    return {p['pair_id']: i + 1 for i, p in enumerate(rows)}


def _pl_b_cell(pair):
    if not pair['present_b']:
        return muted('없음')
    return esc(pct_txt(pair['pct_b'])) if pair.get('pct_b') is not None else muted('대기')


def _pl_row(ctx, pair, index, tag_names, b_ranks):
    pid = pair['pair_id']
    name = '–'.join(pair['electrodes'])
    roi_keys, tags = _pair_link(ctx, pid)           # the 04 link is data of the filters only: no column, no badge in the list (R8:6)
    hidden = ' hide' if index >= PAIR_SHOW else ''
    return ('<tr class="plrow%s" data-pair="%s" data-href="%s" tabindex="0" data-kind="%s" data-ck="%s" data-cat="%s" data-tags="%s" data-linked="%d" '
            'data-name="%s" data-idx="%d" data-brank="%d"><td><b>%s</b></td><td class="l"><b>%s</b></td><td class="l">%s%s</td><td class="l wrap">%s%s</td>'
            '<td class="l">%s</td><td>%s</td></tr>' % (
                hidden, esc(pid), esc(_href('pair:' + pid)), esc(pair['kind']), esc(pair.get('change_kind')), ' '.join(_pair_cats(pair)),
                esc(' '.join(tags)), 1 if roi_keys else 0, esc((name + ' ' + pid).lower()), index, b_ranks.get(pid, 0),
                esc(pair.get('rank') or '—'), esc(name),
                chip(pair['kind']), esc(pair['kind']), ctx.change_badge(pair['change_kind']), _comp_text(pair),
                pp_cell(pair.get('delta'), pair.get('pct_delta')), _pl_b_cell(pair)))


def _filter_buttons(key, label, options):
    """options: [(value, text)]; the first (value '') is the 'all' button. The script marks the one of the hash."""
    return '<span class="muted">%s</span> %s' % (esc(label), ''.join(
        '<button type="button" class="fbtn" data-f="%s" data-v="%s">%s</button>' % (key, esc(v), esc(t)) for v, t in options))


def _pair_list(ctx):
    doc = ctx.doc
    pairs = sorted(doc['pairs'], key=_pair_sort_key)
    names = _tag_names()
    counts = doc['kinds']['change_kind_counts']
    pair_counts = {}                       # pairs per change kind, straight from ledger.json pairs[] (the buttons show these, not the shown rows)
    for p in doc['pairs']:
        pair_counts[p['change_kind']] = pair_counts.get(p['change_kind'], 0) + 1
    ck_opts = [('', '전체')] + [(c['code'], '%s (%d)' % (c['label'], pair_counts.get(c['code'], 0))) for c in doc['change_kinds']
                              if c['code'] not in ('cno',) and counts.get(c['code'], 0) > 0]
    b_ranks = _b_ranks(doc)
    tag_opts = [('', '전체')]
    if ctx.link_status() == 'ok':
        tag_opts += [(t, names.get(t, t)) for t in sorted(((ctx.changes.get('summary') or {}).get('tag_pair_counts')) or {})]
    main_filters = [_filter_buttons('kind', '불량', [('', '전체'), ('line', 'line'), ('pixel', 'pixel')]),
                    _filter_buttons('sort', '정렬', [('', '변화 큰 순'), ('b', 'B 위험 큰 순')])]
    more_filters = [_filter_buttons('ck', '변화 종류', ck_opts),
                    _filter_buttons('cat', 'item 분해 범주', [('', '전체'), ('same', '같은 불량의 증감'), ('new', '새로 생긴 불량 방식'), ('gone', '없어진 불량 방식')])]
    if len(tag_opts) > 1:
        more_filters.append(_filter_buttons('tag', '형상 태그(04, 보조)', tag_opts))
    filters = main_filters
    total = len(pairs)
    shown = min(PAIR_SHOW, total)
    rows = ''.join(_pl_row(ctx, p, i, names, b_ranks) for i, p in enumerate(pairs)) or (
        '<tr><td colspan="6" class="l muted">계산한 쌍이 없습니다.</td></tr>')
    return ('<div class="chips fbar" id="pl-filters">%s</div><details id="pl-more-filters"><summary class="small">필터 더 보기 ▸</summary>'
            '<div class="chips fbar">%s</div></details>'
            '<p class="searchrow"><label>쌍·전극 이름 검색 <input type="search" id="pl-q" autocomplete="off" placeholder="예: Scan3 또는 Anode–Vint"></label> '
            '<span id="pl-count" class="muted">결과 %d 쌍 · 표시 %d</span></p>'
            '<div class="scrl" id="pl-box"><table id="pl-table" data-total="%d" data-count="%d" data-shown="%d"><thead><tr><th>#</th><th class="l">쌍</th>'
            '<th class="l">종류</th><th class="l">변화 종류</th><th class="l">Δ (쌍 전체)</th><th>B 값</th></tr></thead><tbody>%s</tbody></table></div>'
            '<p id="pl-none" class="muted" hidden>조건에 맞는 쌍이 없습니다.</p>'
            '<p style="margin:5px 0 0"><button type="button" id="pl-more" data-f="more" data-v="1"%s>더 보기 (+%d 쌍)</button></p>'
            '<p class="muted">줄 = 쌍 하나. Δ 는 쌍 전체의 장부 값(vi 의 %%p)이며 A 대비 %% 는 각 종류의 vi A 기준입니다. '
            'B 값 = 그 쌍의 B vi 를 종류별 A vi 에 대한 %% 로 쓴 값입니다. '
            '기본으로 정렬 순서의 상위 %d 쌍만 보입니다. 해상도 몫을 뺀 값은 <a href="#s6" data-go="s6">6 해상도 차이 비교</a>에 있습니다.</p>' % (
                ''.join(['<div>%s</div>' % f for f in filters]), ''.join(['<div>%s</div>' % f for f in more_filters]),
                total, shown, total, total, shown, rows,
                '' if total > PAIR_SHOW else ' hidden', max(0, total - PAIR_SHOW), PAIR_SHOW))


def _card_summary(pair):
    """The parts of the pair change that are not 0 (a zero component is left out)."""
    brk = pair.get('breakdown') or {}
    parts = []
    if brk.get('status') not in (None, 'ok') or any(brk.get(k) is None for k in ('same_delta', 'new_value', 'gone_value')):
        return '대기'
    if brk['same_delta'] != 0:
        parts.append('같은 불량 %s' % pp_txt(brk['same_delta']))
    if brk['new_value'] != 0:
        parts.append('새 방식 +%s' % raw_pct(brk['new_value']))
    if brk['gone_value'] != 0:
        parts.append('없어진 방식 −%s' % raw_pct(brk['gone_value']))
    return ' · '.join(parts) or '구성 요소 모두 0'


def _cards(ctx):
    if ctx.maps is None:
        return []
    return [c for c in ctx.maps['doc'].get('cards') or [] if isinstance(c, dict) and c.get('pair_id') in ctx.pair_by_id]


def _card_chooser(ctx):
    if ctx.maps is None:
        return '<p class="muted" id="cards-none">지도 폴더(--maps-dir)가 없어 위치 카드가 없습니다.</p>'
    cards = _cards(ctx)
    if not cards:
        return '<p class="muted" id="cards-none">위치 카드가 없습니다.</p>'
    buttons = ''.join('<button type="button" class="fbtn" data-f="tab" data-v="%s">%s</button>' % (tab, esc(label)) for tab, label in CARD_TABS)
    out = ['<div class="chips" id="card-tabs"><span class="muted">카드 고르기</span> %s</div>' % buttons]
    for tab, label in CARD_TABS:
        inner = []
        for kind in KINDS:
            mine = sorted((c for c in cards if c.get('kind') == kind and tab in (c.get('tabs') or [])),
                          key=lambda c: (((c.get('tab_ranks') or {}).get(tab) or 10 ** 9), c['pair_id']))
            if not mine:
                continue
            lis = []
            for c in mine:
                pair = ctx.pair_by_id[c['pair_id']]
                lis.append('<li data-card="%s" data-href="%s" tabindex="0"><span class="n">%s</span><span><b>%s</b> %s%s<span class="sub2">%s</span></span>'
                           '<span class="v">%s</span></li>' % (
                               esc(c['pair_id']), esc(_href('pair:' + c['pair_id'])), esc(pair.get('rank') or '—'), esc('–'.join(pair['electrodes'])),
                               ctx.change_badge(pair['change_kind']), _comp_text(pair), esc(_card_summary(pair)),
                               pp_cell(pair.get('delta'), pair.get('pct_delta'))))
            inner.append('<div class="muted">%s%s</div><ul class="lst">%s</ul>' % (chip(kind), kind, ''.join(lis)))
        out.append('<div class="cardtab" data-tab="%s"%s>%s</div>' % (
            tab, '' if tab == 'same' else ' hidden', ''.join(inner) or '<p class="muted">이 기준에 해당하는 카드가 없습니다.</p>'))
    dropped = ctx.maps['doc'].get('cards_dropped_for_budget') or []
    if dropped:
        out.append('<p class="muted" id="cards-dropped">메모리 예산이 모자라 카드 %d 장이 빠졌습니다(cards_budget_reduced). 그 쌍은 선택하면 사유를 보입니다.</p>' % len(dropped))
    out.append('<p class="muted">카드 = 쌍의 위치 범위별 값. 요약은 0 인 구성 요소를 숨깁니다.</p>')
    return ''.join(out)


def _row_item_value(row, item_id):
    for entry in row.get('items') or []:
        if isinstance(entry, dict) and entry.get('item_id') == item_id:
            return entry.get('value')
    return None


def _range_ab(a, b):
    return '%s → %s' % (esc(vi_txt(a)), esc(vi_txt(b))) if (a is not None or b is not None) else muted('—')


def _card_tables(ctx, card):
    """The two range tables of a card, split by `items[].presence` (not by `row.match`): same-coordinate A → B of the items both
    products have, and the failure types only one product has. Every number is a ledger_maps.json value."""
    rows = [r for r in card.get('rows') or [] if isinstance(r, dict)]
    by_id = {r.get('id'): r for r in rows}
    pair = ctx.pair_by_id.get(card.get('pair_id')) or {}
    labels = {item['item_id']: _item_label(item) for item in pair.get('items') or []}
    match = card.get('match') or {}
    both, only, unmatched, excluded = [], [], [], 0
    for row in rows:
        if row.get('items') is None:
            excluded += 1
            continue
        side = row.get('side')
        counterpart = by_id.get((row.get('match') or {}).get('counterpart')) if side == 'reference' else None
        status = (row.get('match') or {}).get('status')
        if side == 'reference' and status == 'matched' and counterpart is not None and counterpart.get('items') is not None:
            rng = '%s ↔ %s' % (row.get('id'), counterpart.get('id'))
            for entry in row['items']:
                if entry.get('presence') == 'both':
                    both.append('<tr data-range="%s"><td class="l">%s</td><td class="l">%s</td><td class="l"><span class="badge">같은 좌표 A→B</span></td>'
                                '<td>%s</td><td>%s</td><td class="l">%s</td></tr>' % (
                                    esc(card.get('pair_id')), esc(rng), esc(labels.get(entry.get('item_id'), entry.get('item_id'))),
                                    _range_ab(entry.get('value'), _row_item_value(counterpart, entry.get('item_id'))),
                                    _range_ab(row.get('range_total'), counterpart.get('range_total')), BASIS_CHIP))
        elif side == 'reference' and status == 'matched' and counterpart is not None and any(e.get('presence') == 'both' for e in row['items']):
            # the matched B range lies outside the map window (no items): say it, do not drop the A range silently
            both.append('<tr data-range="%s" class="range-outside"><td class="l">%s ↔ %s</td><td class="l muted" colspan="5">B 범위가 지도 창 밖 — 값 없음</td></tr>' % (
                esc(card.get('pair_id')), esc(row.get('id')), esc(counterpart.get('id'))))
        elif status != 'matched' and any(e.get('presence') == 'both' for e in row['items']):
            unmatched.append('%s(%s)' % (row.get('id'), (row.get('match') or {}).get('reason') or '짝 없음'))
        for entry in row['items']:
            presence = entry.get('presence')
            if (side == 'reference' and presence == 'a_only') or (side == 'target' and presence == 'b_only'):
                a_side = presence == 'a_only'
                only.append('<tr data-range="%s"><td class="l">%s</td><td class="l">%s</td><td class="l"><span class="badge %s">%s</span></td>'
                            '<td>%s %s</td><td>%s %s</td><td class="l">%s</td></tr>' % (
                                esc(card.get('pair_id')), esc(row.get('id')), esc(labels.get(entry.get('item_id'), entry.get('item_id'))),
                                'b-gone' if a_side else 'b-new', 'A 에만' if a_side else 'B 에만', 'A' if a_side else 'B', esc(vi_txt(entry.get('value'))),
                                'A' if a_side else 'B', esc(vi_txt(row.get('range_total'))), BASIS_CHIP))
    why = CARD_MATCH_TEXT.get(match.get('status'), match.get('status'))
    reason = (' (%s)' % match['reason']) if match.get('reason') else ''
    empty_both = ('<tr><td colspan="6" class="l muted">같은 좌표로 비교할 항목이 없습니다 — %s%s. 양쪽에 있는 item 이 없거나 짝이 맞은 범위가 없습니다.</td></tr>' % (
        esc(why), esc(reason)))
    notes = []
    if unmatched:
        notes.append('짝이 맞지 않은 범위: %s' % ', '.join(unmatched))
    if excluded:
        notes.append('창 밖이라 값을 쓰지 않은 범위 %d 개' % excluded)
    return ('<h4>같은 좌표 비교 A → B <span class="muted" style="font-weight:400">(양쪽에 있는 item)</span></h4>'
            '<table class="rt rt-both"><thead><tr><th class="l">범위</th><th class="l">item</th><th class="l">구분</th>'
            '<th>범위 안 이 item 값%s</th><th>범위 합계 (모든 쌍)%s</th><th class="l">기준</th></tr></thead><tbody>%s</tbody></table>'
            '<h4>이 제품에만 있는 불량 방식 <span class="muted" style="font-weight:400">(A→B 비교 없음 · 있는 쪽의 범위 값)</span></h4>'
            '<table class="rt rt-only"><thead><tr><th class="l">범위</th><th class="l">item</th><th class="l">구분 (있는 쪽)</th>'
            '<th>범위 안 이 item 값%s</th><th>범위 합계 (모든 쌍)%s</th><th class="l">기준</th></tr></thead><tbody>%s</tbody></table>%s'
            '<p class="muted">범위 안 이 item 값 = 그 item 의 범위 안 장부 값. 범위 합계 = 같은 범위에 걸친 모든 쌍의 합. 근사 값(07 · 반복 n)은 이 보고서에 넣지 않습니다.</p>' % (
                sub('A → B'), sub('A → B'), ''.join(both) or empty_both, sub('있는 쪽'), sub('있는 쪽'),
                ''.join(only) or '<tr><td colspan="6" class="l muted">없음 — 이 쌍의 item 은 모두 양쪽에 있습니다.</td></tr>',
                ('<p class="muted">%s</p>' % esc(' · '.join(notes))) if notes else ''))


def _card_store(ctx):
    if ctx.maps is None:
        return ''
    out = ['<div id="card-store" hidden>']
    for card in _cards(ctx):
        out.append('<div data-range="%s">%s</div>' % (esc(card['pair_id']), _card_tables(ctx, card)))
    out.append('</div>')
    return ''.join(out)


def _s3_panel(ctx):
    # R8:6 the 04 change locations (entry, tags, list) are one folded reference; the pair list and the cards come first
    roi = ('<details id="roi-ref"><summary>변경 위치(04, 보조) ▸</summary><div id="pn-loc">%s</div></details>' % (
        '<p class="muted" id="loc-note">변경 위치(04)는 A·B 설계가 비슷할 때 비교 의미가 큽니다. unit 크기·배치가 다르면 같은 위치에 같은 전극이 없을 수 있습니다. '
        '판단은 쌍·전극 장부를 기준으로 하세요.</p>' + _locations_line(ctx) + _tag_block(ctx) + _roi_rows(ctx)))
    cards = ('<details id="card-ref"><summary>위치 카드로 찾기 ▸</summary>%s</details>' % _card_chooser(ctx))
    return ('<div id="s3-panel" class="s3panel"><div id="pn-pair">%s</div>'
            '<div id="pn-detail"><p class="muted">선택한 것이 없습니다. 목록·카드·지도에서 하나를 누르세요.</p></div>%s%s%s</div>' % (
                _pair_list(ctx), cards, roi, _card_store(ctx)))


def _explore(ctx):
    """The exploration of 4 (maps + pair list + pair detail); the 04 locations are in a folded reference."""
    head = '<h3 id="explore-h" style="margin-top:14px">탐색 — 쌍을 고르면 그 레이어의 지도가 나옵니다</h3>'
    if ctx.maps is None:
        return head + ('<div class="s3grid"><div class="s3maps" id="s3-maps"><div class="slot" data-slot="s3">지도 없음 — --maps-dir 를 주면 지도가 이 자리에 나옵니다.'
                       '</div></div>%s</div>' % _s3_panel(ctx))
    layers = _map_layers(ctx)
    reason = _shape_reason(ctx)
    ov_ok, ov_reason = _overlay_info(ctx)
    chips = ''.join('<button type="button" class="mchip" data-mlayer="%s">%s</button>' % (esc(layer), esc(layer)) for layer in layers)
    chips += '<button type="button" class="mchip" data-mlayer="all">전체</button>'
    toggles = ''.join('<button type="button" class="mtog" data-t="%s"%s>%s</button>' % (
        key, (' disabled title="%s"' % esc(reason)) if key == 'shape' and reason else '', text)
        for key, text in (('shape', '전극·레이어 형상'), ('defect', '불량 영역(선택 쌍)'), ('others', '다른 쌍도 옅게'), ('roi', '변경 위치')))
    toggles += '<button type="button" class="mtog" id="ov-btn" data-t="ov"%s>겹쳐 보기</button>' % (
        '' if ov_ok else ' disabled title="%s"' % esc(ov_reason))
    out = [head, '<div class="s3grid"><div class="s3maps" id="s3-maps"><div class="mbar"><span class="muted">레이어</span> %s'
           '<details id="view-opts"><summary class="small">보기 옵션 ▸</summary><span class="muted">보기</span> %s '
           '<label class="muted">불량 영역 투명도 <input type="range" id="m-alpha" min="10" max="100" value="80"></label></details></div>' % (
               chips, toggles)]
    if reason:
        out.append('<p class="muted" id="shape-reason">%s</p>' % esc(reason))
    if not ov_ok:
        out.append('<p class="muted" id="ov-reason">겹쳐 보기 꺼짐: %s</p>' % esc(ov_reason))
    out.append('<div class="mrow"><div class="mview"><div id="m3-view">%s</div><div id="m3-ov" hidden>%s</div><div id="m3-all" hidden></div></div>%s</div>' % (
        _pair_of_maps('s3'), _mapcv('ov', 'A', 'A 위에 B (A 회색 실선 · B 점선)'), _m_legend()))
    out.append(_missing_share(ctx))
    outside = (ctx.changes or {}).get('outside_map') or [] if isinstance(ctx.changes, dict) else []
    if outside:
        index = {roi.get('roi_key'): i + 1 for i, roi in enumerate(ctx.changes.get('rois') or [])}
        out.append('<details id="m3-outside"><summary class="small">지도 밖 변경 위치(04, 보조) ▸</summary><p class="muted">창 밖이라 그리지 않고 목록으로만 보입니다.</p><ul id="outside-map">%s</ul></details>' % ''.join(
            '<li data-roi="%s" data-href="%s" tabindex="0">변경 위치 %s <span class="muted">%s</span></li>' % (
                esc(key), esc(_href('roi:' + key)), esc(index.get(key, '?')), esc(key)) for key in outside))
    out.append('</div>%s</div>' % _s3_panel(ctx))
    return ''.join(out)


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
    a = muted('없음') if not row.get('present_a') else esc(vi_txt(v.get('a')))
    b = muted('없음') if not row.get('present_b') else esc(vi_txt(v.get('b')))
    return '%s → %s' % (a, b)


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
            cno = '<td>%s%s</td>' % (('없음' if not row.get('present_a') else esc(vi_txt((row.get('cno') or {}).get('a')))) + ' → ' +
                                     ('없음' if not row.get('present_b') else esc(vi_txt((row.get('cno') or {}).get('b')))),
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

def _occ_evidence(ctx, b):
    """The evidence line of a card: observed connected regions A -> B summed over the items of the card, or '—' and why
    (any item that is not observed on both sides: the sum would be partial)."""
    if ctx.maps is None:
        return '— (지도 폴더가 없어 연결 영역 지표가 없습니다)'
    if ctx.occ is None:
        return '— (연결 영역 지표 없음. %s)' % RERUN
    ids = sorted({i for r in b.get('rows') or [] for i in r.get('item_ids') or [] if not str(i).startswith('CNO:')})
    if not ids:
        return '— (CNO 줄만 있어 연결 영역 지표가 없습니다)'
    na = nb = pend = nobs = 0
    for i in ids:
        row = ctx.occ_by_item.get(i)
        cmp = (row or {}).get('compare') or {}
        if row is None or cmp.get('kind') == 'suppressed':
            if (cmp.get('reason') == 'pending'):
                pend += 1
            else:
                nobs += 1
            continue
        na += (row['reference'].get('n') or 0)
        nb += (row['target'].get('n') or 0)
    if pend or nobs:
        why = []
        if pend:
            why.append('대기 %d 건(메모리 예산 부족)' % pend)
        if nobs:
            why.append('지도 지름 자료 없음 %d 건' % nobs)
        return '— (%s)' % ', '.join(why)
    return '관측 연결 영역 합(%g µm): A %d → B %d' % (ctx.occ.get('diameter_um') or 0, na, nb)


def _row_html(ctx, b, row):
    ids = row.get('item_ids') or []
    best = None
    for i in ids:
        item = ctx.item_by_id.get(i)
        d = abs((item or {}).get('delta') or 0.0)
        if item is not None and (best is None or d > best[0]):
            best = (d, i, item)
    if row.get('cno'):
        name, method = 'CNO(전극 단독)', '—'
    else:
        label = '–'.join(row.get('electrodes') or []) if b['type'] == 'pair' else (row.get('partner') or '–'.join(row.get('electrodes') or []))
        if best is None:
            name = esc(label)
        else:
            name = '<a class="lk" href="%s&amp;layer=%s&amp;it=%s&amp;to=explore">%s</a>' % (
                esc(_href('pair:' + row['pair_id'])), quote(row['layer'], safe=''), quote(best[1], safe=''), esc(label))
        method = ' · '.join(sorted({_item_label(ctx.item_by_id[i]) for i in ids if i in ctx.item_by_id})) or '—'
    pres = row.get('presence') or []
    if row.get('cno'):
        change = '<span class="badge">증감</span>'
    elif pres == ['new']:
        change = '<span class="badge b-new">신규</span>'
    elif pres == ['gone']:
        change = '<span class="badge b-gone">없어짐</span>'
    else:
        change = '<span class="badge">%s</span>' % ('증가' if row['net'] > 0 else '감소')
    total = row.get('pair_delta')
    whole = ' <span class="muted small">(전부)</span>' if total is not None and abs(total - row['net']) < 1e-12 else ''
    lay = ' <span class="muted small">%s</span>' % esc(row['layer']) if b['type'] == 'move' else ''
    return '<tr><td class="l">%s%s</td><td class="l">%s%s</td><td class="l wrap">%s</td><td class="l">%s</td><td>%s</td><td>%s%s</td></tr>' % (
        name, lay,
        chip(row['kind']), row['kind'], esc(method), change, pp_cell(row['net'], None),
        pp_cell(total, None) if total is not None else '<span class="muted">—</span>', whole)


def _bundle_card(ctx, b):
    rows = b.get('rows') or []
    head = '<table class="bt2"><thead><tr><th class="l">%s</th><th class="l">종류</th><th class="l">방식</th><th class="l">변화</th><th>이 카드 몫</th>' \
           '<th>쌍 전체 Δ</th></tr></thead><tbody>' % ('쌍' if b['type'] == 'pair' else '상대')
    shown = ''.join(_row_html(ctx, b, r) for r in rows[:5])
    more = ''
    if len(rows) > 5:
        more = '<details><summary class="small">그 밖 줄 %d 개 ▸</summary>%s%s</tbody></table></details>' % (
            len(rows) - 5, head, ''.join(_row_html(ctx, b, r) for r in rows[5:]))
    kinds = ' &nbsp;·&nbsp; '.join('%s%s %s <span class="muted">(%s)</span>' % (
        chip(k), k, pp_cell(b['kinds'][k]['net'], None), share_txt(b['kinds'][k]['share'])) for k in KINDS)
    amount = ', '.join(_kind_phrase(k, b) for k in KINDS)
    parts = ['<div class="bcard" id="%s"><div class="bh"><b class="bt"><a class="lk" href="#unit=%s&amp;to=s2" title="2 제품 대응 표의 이 칸 보기">%s</a></b> %s%s'
             '<div class="bv">%s</div></div>' % (esc(b['id']), esc(b['id']), esc(bundle_title(b)), _unit_badge(b),
                                                '<span class="badge b-mv">상충</span>' if b.get('conflict') else '', kinds),
             '<h4>무엇이 바뀌었나</h4><p class="bp">%s</p>' % esc(unit_what(ctx, b))]
    if b.get('layer_state_unverified'):
        parts.append('<p class="bp muted small">레이어 상태 미확인 — DL1 전극 목록이 없어 이 전극의 레이어 상태를 확인하지 못했습니다(상태 그대로로 계산).</p>')
    if b.get('layer_state_unknown'):
        parts.append('<p class="bp muted small">일부 끝점의 레이어 상태 정보가 없습니다(상태 그대로로 계산).</p>')
    for el in b.get('partners_elsewhere') or []:
        other = ctx.bundle_by_id.get(el['bundle_id'])
        if other is not None:
            parts.append('<p class="bp small">%s 와의 변화는 \'%s\' 카드에 있습니다.</p>' % (esc(el['partner']), lk_bundle(other)))
    parts.append('<h4>어떤 불량이 어떻게</h4>%s%s</tbody></table>%s' % (head, shown, more))
    parts.append('<p class="muted small" style="margin:3px 0">이 카드 몫 = 이 카드에 배정된 item 의 합입니다. 쌍 전체 Δ = 그 쌍의 모든 레이어·방식 item 의 합이며, '
                 '다른 카드에 나뉜 몫도 들어 있습니다. CNO 는 그 전극의 카드에 전부 들어갑니다.</p>')
    inc, dec = b['atoms']['increase'], b['atoms']['decrease']
    parts.append('<h4>얼마나</h4><p class="bp">%s. 늘어난 item %d 건 · 줄어든 item %d 건.%s</p>' % (
        esc(amount), inc, dec, ' <b class="warn">line 과 pixel 의 방향이 반대입니다(상충).</b>' if b.get('conflict') else ''))
    tp = ['가장 큰 상대 %s 가 이 묶음 %s 변화량의 %s입니다.' % (esc(b['top_partner'][k]['partner']), k, share_txt(b['top_partner'][k]['fraction']))
          for k in KINDS if (b.get('top_partner') or {}).get(k) and (b.get('partner_count') or 0) > 1]
    if tp:
        parts.append('<p class="bp">%s</p>' % ' '.join(tp))
    parts.append('<h4>근거</h4><p class="bp muted occ">%s</p></div>' % esc(_occ_evidence(ctx, b)))
    return ''.join(parts)


def _card_ref(b, other_kind, kind):
    return '<div class="bcard muted small">(%s 목록의 %s 카드 — %s 몫 %s)</div>' % (
        other_kind, lk_bundle(b), kind, share_txt(b['kinds'][kind]['share']))


def _rest_rows(ctx, kind, ids):
    bs = [ctx.bundle_by_id[i] for i in ids if i in ctx.bundle_by_id]

    def row(b):
        name = esc(bundle_title(b))
        return ('<tr id="rest-%s-%s"><td class="l"><a class="lk" href="#unit=%s&amp;to=s2">%s</a></td><td class="l">%s</td><td>%s <span class="muted small">%s</span></td></tr>' % (
            kind, esc(b['id']), esc(b['id']), name, _unit_badge(b), pp_cell(b['kinds'][kind]['net'], None), share_txt(b['kinds'][kind]['share'])))
    head = '<table><thead><tr><th class="l">묶음</th><th class="l">유형</th><th>%s Δ (몫)</th></tr></thead><tbody>' % kind
    shown, tail = bs[:50], bs[50:]
    out = '<div class="scrl">%s%s</tbody></table></div>' % (head, ''.join(row(b) for b in shown))
    if tail:
        out += '<details><summary class="small">%d 개 더 ▸</summary>%s%s</tbody></table></details>' % (len(tail), head, ''.join(row(b) for b in tail))
    return out


def _section3(ctx):
    if ctx.bundles is None:
        return ('<section id="s3">%s%s%s</section>' % (_hd('3', '원인 후보', '그 layout 변화 중 무엇이 vi 를 바꿨나?'),
                                                         _rerun_line('원인 후보'), _nx([('s4', '4 결과'), ('s2', '2 제품 대응')])))
    state = {'defined': False}
    sums = []
    for kind in KINDS:
        sums.extend(_kind_lines(ctx, kind, 's3', state))
    design = ctx.design or {}
    same_res = all((design.get(k) or {}).get('status') == 'available' and 'resolution' in (design[k].get('negligible') or []) for k in KINDS)
    how = ['<p style="margin:3px 0">한 layout 변화는 여러 전극 쌍의 불량을 함께 바꿉니다. 그래서 변화 하나(쌍 × 방식 × 레이어)를 \'무엇이 바뀌었나\' 기준으로 정확히 한 원인 후보(묶음)에 통째로 넣습니다. '
           '모든 원인 후보를 더하면 전체 변화와 같고, 같은 변화가 두 카드에 나오지 않습니다.</p>',
           '<p style="margin:3px 0"><a href="#s4" data-go="s4">4 결과</a>의 \'가장 많이 달라진 쌍\' 은 쌍마다 모든 레이어의 변화를 합친 값이고, 여기 카드는 같은 변화를 \'무엇이 바뀌었나\' 기준으로 다시 묶은 값입니다. '
           '쌍 하나가 두 카드 이상으로 나뉠 수 있어 1 의 쌍 기준과 다를 수 있습니다.</p>',
           '<p style="margin:3px 0">카드 이름을 누르면 2 제품 대응의 그 칸이 강조되고, 상대 쌍 이름을 누르면 4 결과의 탐색에서 그 쌍이 선택됩니다.</p>']
    if not same_res:
        how.append('<p style="margin:3px 0">원인 후보 값은 실제 vi 차이이며 해상도 차이 몫도 들어 있습니다. 해상도 몫을 뺀 값은 <a href="#s6" data-go="s6">6 해상도 차이 비교</a>에 있습니다.</p>')
    hub = ctx.bundles.get('hub_min') or 3
    how.append('<details><summary class="small">배정 규칙 ▸</summary><ol class="small">'
               '<li>그 레이어에서 한쪽 전극만 상태가 바뀌었으면(B 에만 · A 에만 · 이동) 그 전극의 묶음에 전부 넣습니다. CNO 는 항상 그 전극의 묶음에 넣습니다.</li>'
               '<li>두 전극 모두 그대로이면, 그 레이어에서 같은 방향으로 변한 상대가 %d 개 이상인 전극이 한쪽뿐일 때 그 전극의 묶음("상대 여럿과 함께 변함")에 전부 넣습니다.</li>'
               '<li>그 밖(두 전극 모두 바뀜 · 허브 없음 · 양쪽 모두 허브)은 쌍 묶음("이 쌍만 변함")에 전부 넣습니다.</li></ol></details>' % hub)
    out = ['<section id="s3">', _hd('3', '원인 후보', '그 layout 변화 중 무엇이 vi 를 바꿨나?', sums),
           '<div class="how" id="how-to-read"><b>읽는 법</b>%s</div>' % ''.join(how),
           '<div class="ex"><b>용어 예시</b><table><tr><td class="muted">묶음 (전극 · 레이어)</td><td>Data · GAT2 <span class="muted">(Data 전극이 GAT2 레이어에 새로 놓이면서 생긴 쌍들의 변화)</span></td></tr>'
           '<tr><td class="muted">묶음 (쌍 · 레이어)</td><td>Sense–Guard · SD1 <span class="muted">(이 쌍만 변한 경우)</span></td></tr>'
           '<tr><td class="muted">유형</td><td>B 에 새로 놓임 · B 에서 빠짐 · 레이어 이동 · 상대 여럿과 함께 변함 · 이 쌍만 변함</td></tr>'
           '<tr><td class="muted">해석</td><td>상대 여럿과 함께 변한 묶음은 그 전극의 형상·배치 변화로 보일 수 있습니다. 원인은 확정하지 않습니다.</td></tr>'
           '<tr><td class="muted">몫</td><td>그 종류(line 또는 pixel) 변화량(|Δ| 합)에서 묶음이 차지하는 비율</td></tr></table></div>']
    ranking = ctx.bundles['ranking']
    for kind in KINDS:
        lines = _kind_lines(ctx, kind, 's3', {'defined': True})
        rk = ranking[kind]
        if rk.get('status') != 'ok':
            out.append('<h3>%s 을 바꾼 변화</h3><p class="muted" id="cards-%s-none">%s</p>' % (kind, kind, ' '.join(lines)))
            continue
        cards = [ctx.bundle_by_id[i] for i in rk.get('cards') or [] if i in ctx.bundle_by_id]
        if not cards:
            out.append('<h3>%s 을 바꾼 변화</h3><p class="muted" id="cards-%s-none">%s</p>' % (kind, kind, esc(lines[0])))
            continue
        other = ranking['line'].get('cards') or [] if kind == 'pixel' else []
        out.append('<h3>%s 을 바꾼 변화 %d 개 <span class="muted" style="font-weight:400">(%s 몫 큰 순 · 카드 몫 합 %s)</span></h3><div class="bgrid" id="cards-%s">%s</div>' % (
            kind, len(cards), kind, share_txt(rk.get('cover')), kind,
            ''.join(_card_ref(b, 'line', kind) if b['id'] in other else _bundle_card(ctx, b) for b in cards)))
        rest = rk.get('rest') or []
        if rest:
            out.append('<details id="bd-rest-%s"><summary>그 밖 %s 묶음 %d 개 — %s 변화량의 %s ▸</summary>%s</details>' % (
                kind, kind, len(rest), kind, share_txt(rk.get('rest_share')), _rest_rows(ctx, kind, rest)))
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
    out = ['<div class="note">막대는 각 쌍이 변화량(|Δ| 합)에서 차지하는 비율입니다. 실제 vi 기준이고 line 과 pixel 은 나누어 봅니다.</div><div class="g2">']
    for kind in KINDS:
        st = ((ctx.conc.get(kind) or {}).get('actual') or {}).get('pair') or {}
        if st.get('verdict') == 'omitted':
            verdict = '판정을 생략했습니다(%s)' % ' · '.join(STAGE_OMIT.get(r, r) for r in st.get('omit_reasons') or [])
        else:
            verdict = VERDICT_TEXT.get(st.get('verdict'), '—')
        k80 = st.get('k80')
        out.append('<div><h3>%s%s 쌍 단계</h3><p class="small">%s 변화량의 80%% 는 쌍 %s 개에 있고, %s.</p>' % (
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
            rows.append('<tr><td class="l muted">그 밖 %d 개</td><td class="l">%s</td></tr>' % (rest['count'], share_txt(rest.get('share'))))
        out.append('<table>%s</table></div>' % ''.join(rows))
    out.append('</div>')
    return ''.join(out)


def _section4(ctx):
    def line(kind):
        b = ctx.doc['kinds'][kind]['item_breakdown']
        gone = None if b.get('gone') is None else -b['gone']
        extra = ', CNO %s' % pp_txt(b.get('cno')) if kind == 'pixel' else ''
        return '%s 변화는 같은 불량의 증감 %s, 새 방식 %s, 없어진 방식 %s%s 로 이루어집니다.' % (
            kind, pp_txt(b.get('same')), pp_txt(b.get('new')), pp_txt(gone), extra)
    sums = [esc(line(k)) for k in KINDS]
    layers = (((ctx.conc or {}).get('line') or {}).get('actual') or {}).get('layer') or {}
    top = [g for g in layers.get('groups') or [] if not str(g['key']).startswith('CNO@') and g['key'] != 'CRP' and g.get('share') is not None]
    if layers.get('status') == 'ok' and top:
        sums.append('레이어로는 <a class="lk" href="#layer=%s&amp;to=explore">%s</a> 에 line 변화가 가장 많이 몰려 있습니다(%s).' % (
            quote(top[0]['key'], safe=''), esc(top[0]['key']), share_txt(top[0]['share'])))
    out = ['<section id="s4">', _hd('4', '결과', '불량은 어떻게 변했나(어느 조합, 어떤 방식)?', sums),
           '<div class="g2"><div><h3>변화의 구성 — item 단위 분해 <span class="muted" style="font-weight:400">(위 = 증가, 아래 = 감소 · 합이 종류 전체 Δ)</span></h3>%s</div>' % _breakdown_table(ctx),
           '<div><h3>가장 많이 달라진 쌍 <span class="muted" style="font-weight:400">(실제 vi Δ, 종류별로 나눔 · 위 5)</span></h3>%s</div></div>' % _lists(ctx),
           '<div id="explore">%s</div>' % _explore(ctx),
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


def _design_list(ctx, kind, d, increase):
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
        chip(kind), kind, '악화(설계 몫 증가)' if increase else '개선(설계 몫 감소)', 'w' if increase else 'b', kind,
        ''.join(lis) or '<li class="muted">해당하는 쌍이 없습니다</li>')


def _section6(ctx):
    head = lambda sums=None: _hd('6', '해상도 차이 비교', '제품 크기(unit 개수) 차이를 빼면 설계 차이는?', sums)
    nxt = _nx([('sA', '부록 근거·검산'), ('s4', '4 결과로 돌아가기')])
    design = ctx.design
    if design is None:
        return '<section id="s6">%s%s%s</section>' % (head(), _rerun_line('해상도 차이 비교'), nxt)
    avail = {k: (design.get(k) or {}) for k in KINDS}
    ok = [k for k in KINDS if avail[k].get('status') == 'available']
    outside = any((avail[k].get('names') or {}).get('show_outside_conditions') for k in ok)
    if ok and len(ok) == 2 and all('resolution' in (avail[k].get('negligible') or []) for k in ok) and not outside:
        return '<section id="s6">%s%s</section>' % (head(['두 제품의 해상도가 같아 나눌 몫이 없습니다.']), nxt)
    ratios = ((ctx.rebase or {}).get('ratios') or {})
    wh = (ratios.get('line') or {}).get('r_WH')
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
        if 'design' in neg:
            tail = ('설계 차이 합은 0 에 가깝지만 쌍 사이에서 오르고 내린 곳이 있습니다.' if tops_present[k] else '설계 차이는 거의 없습니다.')
            sums.append('%s 중 해상도 차이는 <b>%s</b> 입니다. %s' % (parts[0], esc(pp_txt(0.0 if 'resolution' in neg else seg['resolution'])), tail))
        else:
            res = '해상도 차이는 <b>%s</b>' % esc(pp_txt(0.0 if 'resolution' in neg else seg['resolution']))
            out_txt = ''
            if (d.get('names') or {}).get('show_outside_conditions'):
                out_txt = ', 해상도 외 조건 차이는 <b>%s</b>' % esc(pp_txt(math.fsum([seg['crp'] or 0.0, seg['density'] or 0.0])))
            sums.append('%s 중 설계 차이는 <b>%s</b>, %s%s 입니다.' % (parts[0], esc(pp_txt(seg['design'])), res, out_txt))
    out = ['<section id="s6">', head(sums)]
    if any(avail[k].get('status') == 'unavailable' for k in KINDS):
        out.append('<div class="band"><b>환산 대기</b> — 한 종류의 환산 값이 없어 그 종류는 나누지 않았습니다. 다른 종류는 정상입니다.</div>')
    # the conditions of the two products
    prods = ctx.doc['inputs']['products']
    A, B = prods['reference'], prods['target']

    def ratio(key):
        r = (ratios.get('line') or {}).get(key)
        return '' if r is None else '%.3f' % r

    def row(label, a, b, r, same):
        return '<tr><td class="l">%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            esc(label), esc(_fmt_val(a)), esc(_fmt_val(b)), '<span class="muted">같음</span>' if same else '<span class="warn">다름</span>', r)
    same_names, diff_rows = [], []
    for label, key, rk in (('유효 해상도 규칙(비)', None, 'r_rule'), ('glass 면적(µm²)', 'glass_um2', 'r_G'), ('가중 M', 'weights', 'r_M'),
                           ('이물 밀도 표(sha256)', 'density_sha256', None), ('CRP', 'crp', None)):
        if key is None:
            same = abs((ratios.get('line') or {}).get(rk, 1.0) - 1.0) < 1e-12
            (same_names if same else diff_rows).append(label if same else row(label, '—', '—', ratio(rk), False))
            continue
        a, b = A.get(key), B.get(key)
        if key == 'density_sha256':
            a, b = {k: (v or '')[:8] for k, v in (a or {}).items()}, {k: (v or '')[:8] for k, v in (b or {}).items()}
        (same_names if a == b else diff_rows).append(label.split('(')[0] if a == b else row(label, a, b, ratio(rk) if rk else '', False))
    out.append('<h3>비교 조건</h3><table id="cond6"><tr><th class="l">항목</th><th>A</th><th>B</th><th>판정</th><th>비 B/A</th></tr>%s%s%s%s</table>' % (
        row('해상도 W×H', A.get('resolution'), B.get('resolution'), ratio('r_WH'), A.get('resolution') == B.get('resolution')),
        row('유효 해상도', A.get('effective_resolution'), B.get('effective_resolution'), '', A.get('effective_resolution') == B.get('effective_resolution')),
        row('unit 크기(µm)', A.get('unit_size_um'), B.get('unit_size_um'), '', A.get('unit_size_um') == B.get('unit_size_um')), ''.join(diff_rows)))
    if same_names:
        out.append('<p class="muted small" id="cond6-same" style="margin:3px 0">같음: %s</p>' % esc(', '.join(same_names)))
    if outside:
        out.append('<div class="band"><b>해상도 외 조건이 다름</b> — 이 분리는 그 차이도 함께 담습니다(아래 "해상도 외 조건 몫").</div>')
    # the result table
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
        rows.append('<tr><td class="l"><span class="badge">%s</span></td><td>%s <span class="muted small">(A 대비 %s)</span></td>%s%s%s<td><b>%s</b></td></tr>' % (
            k, pp_cell(d['delta'], None), esc(spct(d['delta_a_relative']) if d.get('delta_a_relative') is not None else '—'),
            cell('design', seg['design']), cell('resolution', seg['resolution']),
            ('<td>%s</td>' % pp_cell(math.fsum([seg['crp'] or 0.0, seg['density'] or 0.0]), None)) if outside else '',
            esc('0%' if 'design' in neg else (spct(rel.get('design')) if rel.get('design') is not None else '—'))))
    out.append('<h3>결과 — 실제 차이 = 설계 차이 몫 + 해상도 차이 몫%s</h3><table id="design-table"><tr>%s</tr>%s</table>' % (
        ' + 해상도 외 조건 몫' if outside else '', cols, ''.join(rows)))
    # top pairs
    if ok:
        out.append('<div class="note">해상도 몫을 뺄 때 모든 쌍의 B 값에 같은 비율(A unit 개수 ÷ B unit 개수)을 곱합니다. 그래서 빠지는 양은 B 값이 큰 쌍일수록 큽니다. '
                   '%p 순위가 바뀌고, 실제로는 악화지만 늘어난 폭이 unit 개수 증가율보다 작으면 설계로는 개선으로 보일 수 있습니다. A 대비 % 의 순서는 바뀌지 않습니다.</div>')
        lists = ''.join(_design_list(ctx, k, avail[k], inc) for k in ok for inc in (True, False))
        out.append('<h3 id="design-top">설계 차이 상위 쌍 <span class="muted" style="font-weight:400">(설계 차이 몫 기준 · 실제 Δ 순위와 달라진 쌍에 "실제 순위" 표시)</span></h3>'
                   '<p class="muted small">"A 대비 %" = 설계 차이 몫 ÷ A 값입니다. 쌍을 누르면 4 결과의 탐색에서 그 쌍이 선택됩니다.</p><div class="g2">' + lists + '</div>')
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
            k, esc(' → '.join('%s %s' % (s, vi_txt(stt[s].get('vi'))) for s in ('S0', 'S1', 'S2', 'S3', 'S4') if s in stt))))
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


def _pair_bundle(ctx, pair):
    items = []
    for item in pair['items']:
        items.append({'id': item['item_id'], 'ft': item.get('failure_type'), 'ul': item.get('upper_layer'), 'll': item.get('lower_layer'),
                      'al': item.get('analysis_layer'), 'label': _item_label(item), 'pres': item.get('presence'),
                      'a': item.get('a'), 'b': item.get('b'), 'd': item.get('delta'), 'sizes': item.get('by_size') or [],
                      'af': bool(item.get('a_filtered')), 'bf': bool(item.get('b_filtered'))})
    return {'id': pair['pair_id'], 'no': pair.get('rank'), 'e': list(pair['electrodes']), 'kind': pair['kind'], 'ck': pair['change_kind'],
            'a': pair['a'], 'b': pair['b'], 'd': pair['delta'], 'pa': bool(pair['present_a']), 'pb': bool(pair['present_b']),
            'pct_a': pair.get('pct_a'), 'pct_b': pair.get('pct_b'), 'pct_d': pair.get('pct_delta'),
            'la': list(pair.get('layers_a') or []), 'lb': list(pair.get('layers_b') or []),
            'brk': pair.get('breakdown'), 'comp': pair.get('composition'), 'items': items}


def _changes_bundle(ctx):
    ch = ctx.changes
    if ctx.maps is None:
        return None
    if not isinstance(ch, dict):
        return {'link_status': 'no_candidates'}
    rois = []
    for roi in ch.get('rois') or []:
        rois.append({'roi_key': roi.get('roi_key'), 'layers': roi.get('layers'), 'status': roi.get('status'),
                     'tags': roi.get('change_kinds'), 'tag_text': roi.get('change_kind_text'),
                     'center_xy_um': roi.get('center_xy_um'), 'window_um': roi.get('window_um'),
                     'map_status': (roi.get('map') or {}).get('status'), 'map': roi.get('map')})
    return {'link_status': ch.get('link_status'), 'coords_status': ch.get('coords_status'), 'apply_align': ch.get('apply_align'),
            'summary': ch.get('summary'), 'pairs': ch.get('pairs') or {}, 'rois': rois, 'outside_map': ch.get('outside_map') or [],
            'overlay': ctx.maps['doc'].get('overlay')}


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
            'cards_dropped': len(mdoc.get('cards_dropped_for_budget') or []), 'options': mdoc.get('options') or {},
            'dropped_cards': [{'pair_id': d.get('pair_id'), 'kind': d.get('kind'), 'stage': d.get('stage'), 'reason': d.get('reason')}
                              for d in mdoc.get('cards_dropped_for_budget') or [] if isinstance(d, dict)],
            'sha256': ctx.maps['sha'], 'report': mdoc.get('report') or {}, 'sides': _map_sides(ctx), 'layers': _map_layers(ctx),
            'shape_reason': _shape_reason(ctx), 'overlay': mdoc.get('overlay')}


def _cards_bundle(ctx):
    if ctx.maps is None:
        return []
    return [{'pair_id': c.get('pair_id'), 'kind': c.get('kind'), 'rank': c.get('rank'), 'tabs': c.get('tabs'), 'tab_ranks': c.get('tab_ranks'),
             'match': (c.get('match') or {}).get('status')}
            for c in ctx.maps['doc'].get('cards') or [] if isinstance(c, dict)]


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
        'maps': _maps_bundle(ctx), 'changes': _changes_bundle(ctx), 'cards': _cards_bundle(ctx), 'report07': ctx.report07,
        'layer_order': ctx.layer_order, 'bundles': _bundles_data(ctx)}


def _bundles_data(ctx):
    """What the script needs of the cause candidates: id, title, the (electrode, layer) cells, the pairs with their share, card flag."""
    if ctx.bundles is None:
        return []
    in_cards = {i for kind in KINDS for i in ((ctx.bundles.get('ranking') or {}).get(kind) or {}).get('cards') or []}
    out = []
    for b in ctx.bundles.get('list') or []:
        if b['type'] == 'pair':
            cells = [[e, b['layer']] for e in b.get('electrodes') or []]
        elif b['type'] == 'move':
            cells = [[b['electrode'], l] for l in (b.get('from') or []) + (b.get('to') or [])]
        else:
            cells = [[b['electrode'], b['layer']]]
        pairs = {}
        for r in b.get('rows') or []:
            if r.get('pair_id'):
                p = pairs.setdefault(r['pair_id'], [0.0, 0.0, 0])
                p[0] += r['net']
                p[1] += r['abs']
                p[2] += len(r.get('item_ids') or [])
        out.append({'id': b['id'], 'title': bundle_title(b), 'ic': b['id'] in in_cards, 'cells': cells, 'pairs': pairs})
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
