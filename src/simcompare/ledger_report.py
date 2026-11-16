"""ledger-report: the single-file HTML report of one `ledger` run (`ledger_v1` -> `ledger_report.html`).

Input: `ledger.json` (+ the `execution.json` of the `ledger` run). Nothing is recalculated: every number is a `ledger.json`
value. The report only converts formats (% of the reference vi, %p), and adds up the values of the rows it folds into an
"other k" row (docs/plan/ledger_report/README.md sections 11-13). Sections 2 and 5 are placeholders in this stage.
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

from .compare2 import _json_text
from .numeric_schema import NumericContractError

SCHEMA_VERSION = 'ledger_report_v1'
LEDGER_SCHEMA = 'ledger_v1'
COMMAND = 'ledger-report'
KINDS = ('line', 'pixel')
HTML_NAME = 'ledger_report.html'
OWNED_NAMES = {HTML_NAME, 'report.json', 'execution.json', 'error.json'}
TMP_PREFIX = '.ledger-report-'
DEFAULT_TOP = 30
MODE_LABELS = {'lateral_short': '측면', 'vertical_short': '상하', 'lateral_open': 'CNO'}
PRESENCE_LABELS = {'both': 'A·B 모두', 'new': 'B 에만', 'gone': 'A 에만'}
REASON_LABELS = {'ledger_totals_check_failed': '합계 검산이 맞지 않습니다(ledger 의 checks 를 확인하세요)'}
NEXT_STEP = '다음 단계에서 추가됩니다'


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
    return ('+' if p > 0 else '−') + pct_txt(abs(p))


def raw_pct(v):
    """vi values are fractions; the report writes them as % (07 does the same)."""
    return '%.4f%%' % (v * 100)


def raw_pp(v):
    return ('+' if v > 0 else '−') + '%.4f%%p' % abs(v * 100)


def sub(text):
    return '<span class="sub2">%s</span>' % esc(text)


def vcell(value, pct, present=True):
    """A value cell: absent in that product = 없음; present without a value = 대기; present 0 = 0%."""
    if not present:
        return muted('없음')
    if value is None:
        return muted('대기')
    if value == 0:
        return '0%' if pct is not None else '0'
    if pct is None:
        return esc(raw_pct(value))
    return esc(pct_txt(pct)) + sub(raw_pct(value))


def dcell(delta, pct):
    if delta is None:
        return muted('대기')
    if delta == 0:
        return '0'
    cls = 's-up' if delta > 0 else 's-down'
    main = spct(pct) if pct is not None else raw_pp(delta)
    return '<span class="sg %s">%s</span>%s' % (cls, esc(main), sub(raw_pp(delta)) if pct is not None else '')


def osum(values):
    """Exact sum with the ledger rule (ledger.py): one null part makes the whole sum null (대기)."""
    values = list(values)
    return None if any(v is None for v in values) else math.fsum(values)


def rest_sums(rows, getters):
    """Sums of the rows folded into an "other" row, one per getter, with the ledger null rule, plus the number of
    rows that have a null in any of the three values (shown as "값 대기 n 줄 포함")."""
    sums = [osum(g(r) for r in rows) for g in getters]
    pending = sum(1 for r in rows if any(g(r) is None for g in getters))
    return sums[0], sums[1], sums[2], pending


def pending_note(pending):
    return '<span class="sub2">값 대기 %d 줄 포함</span>' % pending if pending else ''


def attr_num(value):
    return '' if value is None else repr(float(value))


def chip(kind):
    return '<span class="chip" style="background:var(--%s)"></span>' % kind


# ---- reading ledger.json ----------------------------------------------------------------------------------------------

def _read_ledger(ledger_dir):
    """(document, sha256, execution) or NumericContractError ledger_input_missing / ledger_schema_mismatch."""
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
    return doc, hashlib.sha256(raw).hexdigest(), execution


# ---- sections -----------------------------------------------------------------------------------------------------------

class Ctx(object):
    """Lookup tables of one report."""

    def __init__(self, doc, top_pairs, top_electrodes):
        self.doc = doc
        self.top_pairs, self.top_electrodes = top_pairs, top_electrodes
        self.maps = None                      # the loaded ledger-maps folder (T5), or None
        self.change_labels = {c['code']: c['label'] for c in doc['change_kinds']}
        self.status_labels = {c['code']: c['label'] for c in doc['electrode_status']}
        self.vi_a = {kind: doc['kinds'][kind]['vi_a'] for kind in KINDS}
        self.cno_electrodes = {row['electrode_id'] for row in doc['cno']}
        self.kind_electrodes = {kind: set() for kind in KINDS}
        for row in doc['pairs']:
            if row['kind'] in KINDS:
                self.kind_electrodes[row['kind']].update(row['electrodes'])
        self.kind_electrodes['pixel'] |= self.cno_electrodes

    def pct(self, value, kind):
        return pct_of(value, self.vi_a[kind])

    def change_badge(self, code):
        cls = {'new_pair': ' b-new', 'gone_pair': ' b-gone', 'value_pending': ' b-trade'}.get(code, '')
        return '<span class="badge%s">%s</span>' % (cls, esc(self.change_labels.get(code, code)))


def _abc_cells(ctx, kind, a, b, delta, present_a=True, present_b=True):
    return ('<td>%s</td><td>%s</td><td class="l">%s</td>' % (
        vcell(a, ctx.pct(a, kind), present_a), vcell(b, ctx.pct(b, kind), present_b), dcell(delta, ctx.pct(delta, kind))))


def _data_attrs(row, a, b, d):
    return 'data-row="%s" data-a="%s" data-b="%s" data-d="%s"' % (row, attr_num(a), attr_num(b), attr_num(d))


def _maps_band(ctx):
    maps = ctx.maps
    if maps is None or maps['doc'].get('status') != 'partial':
        return ''
    return ('<div class="band partial" id="maps-partial"><b>지도 부분 결과</b> — ledger-maps 의 값 검산이 맞지 않습니다'
            '(%s). 2절 지도의 값을 그대로 믿지 마세요.</div>' % esc(', '.join(maps['doc'].get('reasons') or ['partial'])))


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


PAIR_REASON_TEXT = {'builtin:GH': '코드 규칙: GH 포함', 'builtin:N_NODE': '코드 규칙: N_NODE 포함',
                    'builtin:VBIAS-VINT1': '코드 규칙: VBIAS-VINT1', 'pairs_csv:excluded': 'pairs.csv: excluded'}
PRODUCT_NAMES = (('reference', '기준 A'), ('target', '대상 B'))


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


def _excluded_only_electrodes(ctx):
    """Electrodes that appear in a left-out pair and keep no calculated item (no registered pair, no CNO)."""
    kept = set(ctx.cno_electrodes)
    for row in ctx.doc['pairs']:
        kept.update(row['electrodes'])
    named = set()
    for _name, electrodes, _pid, _reason, _a, _e, _layers in _pair_exclusion_rows(ctx.doc):
        named.update(electrodes)
    return named - kept


def _section1(ctx):
    doc = ctx.doc
    kinds, totals = doc['kinds'], doc['totals']
    out = ['<section id="s1"><p class="q">얼마나 달라졌나</p><h2>1. 전체 비교</h2>',
           '<p class="lead">vi 는 불량 발생 비율입니다. 양품 수율이 아닙니다. 값이 커지면 위험이 커집니다.</p>']
    # vi table
    out.append('<table id="vi"><tr><th class="l">불량 종류</th><th>vi A (= 100%)</th><th>vi B</th><th class="l">Δvi (B − A)</th>'
               '<th>증가 합</th><th>감소 합</th></tr>')
    for kind in KINDS:
        k, t = kinds[kind], totals[kind]
        vi_a, vi_b = k['vi_a'], k['vi_b']
        a_cell = ('100%' + sub(raw_pct(vi_a))) if vi_a not in (None, 0) else (raw_pct(vi_a) if vi_a == 0 else muted('대기'))
        out.append('<tr><td class="l">%s<b>%s</b></td><td>%s</td><td>%s</td><td class="l">%s</td><td>%s</td><td>%s</td></tr>' % (
            chip(kind), kind, a_cell, vcell(vi_b, ctx.pct(vi_b, kind)), dcell(t['delta'], ctx.pct(t['delta'], kind)),
            dcell(k['increase_sum'], ctx.pct(k['increase_sum'], kind)),
            dcell(k['decrease_sum'], ctx.pct(k['decrease_sum'], kind))))
    out.append('</table><p class="muted">큰 글씨 % 는 <b>A 의 전체 vi 를 100% 로 본 값</b>입니다. line 값은 vi_line A, pixel 값은 '
               'vi_pixel A 가 기준입니다. 기준이 달라서 line % 와 pixel % 는 서로 더하거나 비교하지 않습니다. 작은 글씨는 원래 값(%, %p)입니다. '
               'A 의 vi 가 0 이면 % 없이 원래 값만 보입니다.</p>')
    # tiles
    out.append('<h3>핵심 요약</h3><div class="key">%s</div>' % _tiles(ctx))
    # change kinds
    out.append('<h3>변화 종류 — 달라진 양이 어떤 종류의 변화에서 왔나</h3>%s' % _change_kind_table(ctx))
    # composition of the totals, CRP and curve-correction lines
    out.append('<h3>합계 구성 — 곡선 보정 줄과 CRP 줄</h3>%s' % _composition_table(ctx))
    # electrode correspondence
    counts = {}
    for row in doc['electrodes']:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    labels = ' · '.join('%s %d' % (esc(ctx.status_labels.get(code, code)), counts[code]) for code in
                        [c['code'] for c in doc['electrode_status']] if code in counts)
    out.append('<h3>전극 대응</h3><p>전극 %d 개: %s</p>' % (len(doc['electrodes']), labels or '없음'))
    inv = doc['inputs']['electrode_inventory']
    incomplete = [role for role in ('reference', 'target') if inv[role]['status'] != 'complete']
    if incomplete:
        out.append('<p class="muted" id="inventory-note">전극 목록이 완전하지 않아(관측 기준) 전극 추가·제거를 판정하지 않습니다. '
                   '목록에 없다는 것은 "없음"이 아니라 "확인 불가"입니다.</p>')
    excluded = _pair_exclusion_rows(doc)
    if excluded:
        distinct = len({tuple(sorted(row[1])) for row in excluded})
        out.append('<p class="muted" id="pair-excluded-note">계산에서 제외한 전극 쌍 %d개 — 7절 참고</p>' % distinct)
    out.append('</section>')
    return ''.join(out)


def _group_sums(ctx):
    """Display sums per (kind, change kind) of the ledger rows (pairs + CNO): count of rows with a value, increase, decrease, delta."""
    doc = ctx.doc
    groups = {kind: {} for kind in KINDS}
    for row in doc['pairs']:
        if row['kind'] in KINDS:
            groups[row['kind']].setdefault(row['change_kind'], []).append(row['delta'])
    for row in doc['cno']:
        groups['pixel'].setdefault('cno', []).append(row['delta'])
    result = {}
    for kind in KINDS:
        for code, values in groups[kind].items():
            # ledger null rule: one pending row makes the group sum pending
            result[(kind, code)] = {'delta': osum(values),
                                    'inc': osum(v if v > 0 else 0.0 for v in values if v is not None)
                                    if all(v is not None for v in values) else None}
    return result


def _tiles(ctx):
    doc = ctx.doc
    sums = _group_sums(ctx)

    def line(kind, body):
        return '<div class="kl">%s<span class="muted">%s</span> %s</div>' % (chip(kind), kind, body)

    t1 = ''.join(line(k, dcell(doc['totals'][k]['delta'], ctx.pct(doc['totals'][k]['delta'], k))) for k in KINDS)
    t2 = []
    for kind in KINDS:
        groups = [(v['inc'], code) for (k, code), v in sums.items() if k == kind and code != 'no_change']
        pending = any(inc is None for inc, _ in groups)
        cands = [(inc, code) for inc, code in groups if inc]
        if not cands:
            t2.append(line(kind, muted('대기') if pending else '증가 없음'))
            continue
        inc, code = max(cands, key=lambda c: (c[0], c[1]))
        note = ' ' + muted('(값 대기 포함)') if pending else ''
        t2.append(line(kind, '<a href="#s1"><b>%s</b></a> 증가 %s%s' % (esc(ctx.change_labels.get(code, code)),
                                                                     dcell(inc, ctx.pct(inc, kind)), note)))
    t3 = []
    for kind in KINDS:
        rows = _sorted_electrodes(ctx, kind)
        if not rows or rows[0][kind]['delta'] is None:
            t3.append(line(kind, muted('대기')))
            continue
        row = rows[0]
        index = rows.index(row)
        href = '#el-%s-%d' % (kind, index + 1) if index < ctx.top_electrodes else '#s3'
        t3.append(line(kind, '<a href="%s"><b>%s</b></a> %s' % (href, esc(row['electrode']),
                                                              dcell(row[kind]['delta'], row[kind]['pct_delta']))))
    t4 = []
    for kind in KINDS:
        rows = [p for p in doc['pairs'] if p['kind'] == kind]
        if not rows:
            t4.append(line(kind, '쌍 없음'))
            continue
        p = rows[0]
        t4.append(line(kind, '<a href="#pair-%s"><b>%s</b></a> %s' % (esc(p['rank']), esc('–'.join(p['electrodes'])),
                                                                     vcell(p['b'], p['pct_b'], p['present_b']))))
    return ('<div class="tile"><div class="k">① 전체 <span class="muted">(Δvi, A 의 vi 대비)</span></div>%s</div>'
            '<div class="tile"><div class="k">② 증가가 가장 큰 변화 종류 <span class="muted">(기준: 증가 합)</span></div>%s</div>'
            '<div class="tile"><div class="k">③ 가장 크게 달라진 전극 <span class="muted">(3절)</span></div>%s</div>'
            '<div class="tile"><div class="k">④ B 에서 가장 큰 쌍 <span class="muted">(4절)</span></div>%s</div>' % (
                t1, ''.join(t2), ''.join(t3), ''.join(t4)))


def _change_kind_table(ctx):
    doc = ctx.doc
    counts = doc['kinds']['change_kind_counts']
    sums = _group_sums(ctx)
    head = ('<table id="change-kinds"><tr><th class="l">변화 종류</th><th>건수</th><th>%sline 변화</th><th>%spixel 변화</th></tr>' % (
        chip('line'), chip('pixel')))
    rows = []
    total_count = 0
    for entry in doc['change_kinds']:
        code = entry['code']
        count = counts.get(code, 0)
        if code == 'value_pending' and count == 0:
            continue
        total_count += count
        cells = []
        for kind in KINDS:
            if code == 'cno' and kind == 'line':
                cells.append(muted('해당 없음'))
                continue
            entry_sum = sums.get((kind, code))
            cells.append('0' if entry_sum is None else dcell(entry_sum['delta'], ctx.pct(entry_sum['delta'], kind)))
        rows.append('<tr data-code="%s"><td class="l"><b>%s</b></td><td>%d</td><td>%s</td><td>%s</td></tr>' % (
            esc(code), esc(entry['label']), count, cells[0], cells[1]))
    crp = doc['totals']['pixel']['crp']
    rows.append('<tr><td class="l"><b>CRP 차이</b><span class="sub2">두 제품의 CRP 차이. pixel 에만 있고 전극에 나누지 않음</span></td>'
                '<td>—</td><td>%s</td><td>%s</td></tr>' % (
                    muted('해당 없음'), dcell(crp['delta'], ctx.pct(crp['delta'], 'pixel'))))
    t = doc['totals']
    rows.append('<tr class="sum"><td class="l">전체 (= 위 줄의 합)</td><td>%d</td><td>%s</td><td>%s</td></tr>' % (
        total_count, dcell(t['line']['delta'], ctx.pct(t['line']['delta'], 'line')),
        dcell(t['pixel']['delta'], ctx.pct(t['pixel']['delta'], 'pixel'))))
    return head + ''.join(rows) + ('</table><p class="muted">건수는 쌍 하나에 한 번이고 CNO 줄은 CNO 로 셉니다. 값 대기는 0 건이면 숨깁니다. '
                                   '변화 종류는 확인된 사실만 말합니다.</p>')


def _composition_table(ctx):
    doc = ctx.doc
    out = ['<table id="composition"><tr><th class="l">줄</th><th>A</th><th>B</th><th class="l">Δ</th></tr>']
    inputs = doc['inputs']['products']
    for kind in KINDS:
        t = doc['totals'][kind]
        out.append('<tr class="grp"><td colspan="4" class="l">%s<b>%s</b> <span class="muted">A 의 vi_%s = 100%%</span></td></tr>' % (
            chip(kind), kind, kind))
        lines = [('쌍 item 합', t['items'])]
        if kind == 'pixel':
            lines.append(('CNO 합', t['cno']))
        lines.append(('곡선 보정 줄 (절편, 전극에 나누지 않음)', t['intercept']))
        if kind == 'pixel':
            crp = t['crp']
            crp_label = 'CRP 줄 (두 제품의 CRP 가 같음)' if not crp['present'] else (
                'CRP 줄 (A CRP %s, B CRP %s)' % (num(inputs['reference'].get('crp')), num(inputs['target'].get('crp'))))
            lines.append((crp_label, crp))
        for label, row in lines:
            out.append('<tr><td class="l">%s</td>%s</tr>' % (esc(label), _abc_cells(ctx, kind, row['a'], row['b'], row['delta'])))
        out.append('<tr class="sum"><td class="l">열 합계 (= vi)</td>%s</tr>' % _abc_cells(ctx, kind, t['a'], t['b'], t['delta']))
    out.append('</table><p class="muted">쌍의 A 값은 "B 의 CRP 기준으로 환산한 값"입니다. CRP 줄은 A = −d, B = 0, Δ = d 입니다. '
               '열 합계는 실제 vi 와 같아야 합니다(합계식 6개, 7절 검산).</p>')
    return ''.join(out)


def _placeholder(number, title):
    return ('<section id="s%d"><h2>%d. %s</h2><p class="lead placeholder">%s.</p></section>' % (number, number, esc(title), NEXT_STEP))


# ---- section 3: electrodes ---------------------------------------------------------------------------------------------

def _sorted_electrodes(ctx, kind):
    rows = [row for row in ctx.doc['electrodes'] if row['electrode'] in ctx.kind_electrodes[kind]]

    def key(row):
        delta = row[kind]['delta']
        return (0 if delta is not None else 1, -abs(delta or 0.0), row['electrode'])

    return sorted(rows, key=key)


def _layers_text(row, side):
    layers = row['layers_' + side]
    present = row['present_' + side]
    if not present:
        return '없음'
    return ', '.join(layers) if layers else '—'


def _partners_text(row):
    a, b = ', '.join(row['partners_a']) or '없음', ', '.join(row['partners_b']) or '없음'
    return a if a == b else '%s → %s' % (a, b)


def _electrode_layer_details(ctx, row, kind):
    lines = []
    for layer, kinds in row['layers'].items():
        v = kinds[kind]
        if v['a'] == 0 and v['b'] == 0 and v['delta'] == 0:
            continue
        lines.append('<tr class="sub"><td class="l">%s</td>%s</tr>' % (esc(layer), _abc_cells(ctx, kind, v['a'], v['b'], v['delta'])))
    if not lines:
        return ''
    return ('<details><summary>레이어별 %d</summary><table><tr><th class="l">레이어</th><th>A</th><th>B</th><th class="l">Δ</th></tr>%s</table></details>'
            % (len(lines), ''.join(lines)))


def _electrode_row(ctx, row, kind, index, row_kind):
    v = row[kind]
    cells = ['<td class="l"><b>%s</b></td>' % esc(row['electrode']),
             '<td class="l"><span class="badge">%s</span></td>' % esc(ctx.status_labels.get(row['status'], row['status'])),
             '<td class="l">%s → %s</td>' % (esc(_layers_text(row, 'a')), esc(_layers_text(row, 'b'))),
             '<td>%s</td><td>%s</td><td class="l">%s</td>' % (
                 vcell(v['a'], v['pct_a'], row['present_a']), vcell(v['b'], v['pct_b'], row['present_b']),
                 dcell(v['delta'], v['pct_delta']))]
    if kind == 'pixel':
        if row['electrode'] in ctx.cno_electrodes:
            c = row['cno']
            cells.append('<td>%s → %s%s</td>' % (esc(pct_txt(ctx.pct(c['a'], kind))) if c['a'] is not None and ctx.pct(c['a'], kind) is not None else esc(num(c['a'])),
                                                 esc(pct_txt(ctx.pct(c['b'], kind))) if c['b'] is not None and ctx.pct(c['b'], kind) is not None else esc(num(c['b'])),
                                                 sub('Δ ' + (raw_pp(c['delta']) if c['delta'] else '0') if c['delta'] is not None else '대기')))
        else:
            cells.append('<td>%s</td>' % muted('해당 없음'))
    cells.append('<td class="l wrap">%s</td><td class="l wrap">%s</td>' % (esc(_partners_text(row)), _electrode_layer_details(ctx, row, kind)))
    anchor = ' id="el-%s-%d"' % (kind, index) if row_kind == 'top' else ''
    return '<tr%s %s>%s</tr>' % (anchor, _data_attrs(row_kind, v['a'], v['b'], v['delta']), ''.join(cells))


def _electrode_head(kind):
    return ('<tr><th class="l">전극</th><th class="l">상태</th><th class="l">레이어 A → B</th><th>A</th><th>B</th><th class="l">Δ</th>%s'
            '<th class="l">상대 전극 A → B</th><th class="l">레이어별</th></tr>' % ('<th>그중 CNO A → B</th>' if kind == 'pixel' else ''))


def _section3(ctx):
    doc = ctx.doc
    out = ['<section id="s3"><p class="q">어느 전극인가</p><h2>3. 전극별</h2>',
           '<p class="lead">전극 쌍의 불량은 두 전극에 절반씩 넣습니다. CNO 는 그 전극에 전부 넣습니다. 전극 이름이 곧 기능입니다. '
           'line 과 pixel 은 따로 정렬하고 더하지 않습니다. 정렬은 변화(Δ)의 절댓값이 큰 순입니다.</p>']
    n = ctx.top_electrodes
    for kind in KINDS:
        rows = _sorted_electrodes(ctx, kind)
        t = doc['totals'][kind]
        total = ({'a': t['items']['a'], 'b': t['items']['b'], 'delta': t['items']['delta']} if kind == 'line' else
                 {k: osum([t['items'][k], t['cno'][k]]) for k in ('a', 'b', 'delta')})
        shown, rest = rows[:n], rows[n:]
        cols = 9 if kind == 'pixel' else 8
        out.append('<h3 id="s3-%s">%s<b>%s</b> <span class="muted">%d/%d 전극 표시 · 기준 vi_%s A = 100%%</span></h3>' % (
            kind, chip(kind), kind, len(shown), len(rows), kind))
        out.append('<table id="el-%s" class="ledger">%s' % (kind, _electrode_head(kind)))
        out.extend(_electrode_row(ctx, row, kind, i, 'top') for i, row in enumerate(shown, 1))
        if rest:
            a, b, d, pend = rest_sums(rest, [(lambda r, f=f: r[kind][f]) for f in ('a', 'b', 'delta')])
            out.append('<tr class="rest" %s><td class="l"><b>기타 %d 전극</b>%s</td><td></td><td></td>%s%s<td colspan="%d"></td></tr>' % (
                _data_attrs('rest', a, b, d), len(rest), pending_note(pend), _abc_cells(ctx, kind, a, b, d),
                '<td></td>' if kind == 'pixel' else '', 2))
        out.append('<tr class="sum" %s><td class="l">합계</td><td colspan="2" class="l muted">%s</td>%s<td colspan="%d"></td></tr>' % (
            _data_attrs('total', total['a'], total['b'], total['delta']),
            '전극 쌍 합 + CNO' if kind == 'pixel' else '전극 쌍 합', _abc_cells(ctx, kind, total['a'], total['b'], total['delta']),
            cols - 6))
        out.append('</table>')
        if rest:
            out.append('<details><summary>기타 %d 전극 전체 보기</summary><table class="ledger">%s%s</table></details>' % (
                len(rest), _electrode_head(kind), ''.join(_electrode_row(ctx, row, kind, i, 'restitem') for i, row in enumerate(rest, 1))))
        na = [row['electrode'] for row in doc['electrodes'] if row['electrode'] not in ctx.kind_electrodes[kind]]
        if na:
            only = _excluded_only_electrodes(ctx)
            names = ', '.join(esc(name) + (' (계산 제외 쌍만 있음)' if name in only else '') for name in na)
            out.append('<details><summary>%s — 이 종류의 쌍(CNO)이 없는 전극 %d 개</summary><p class="wrap">%s</p></details>' % (
                '해당 없음', len(na), names))
    out.append('</section>')
    return ''.join(out)


# ---- section 4: pairs --------------------------------------------------------------------------------------------------

def _pair_modes(pair):
    modes = []
    for item in pair['items']:
        label = MODE_LABELS.get(item['failure_type'], item['failure_type'])
        if label not in modes:
            modes.append(label)
    return ' · '.join(modes) or '—'


def _pair_layers_text(pair):
    a = ', '.join(pair['layers_a']) if pair['present_a'] else '없음'
    b = ', '.join(pair['layers_b']) if pair['present_b'] else '없음'
    return '%s → %s' % (a or '—', b or '—')


def _item_side_cell(ctx, kind, item, side):
    """One side of an item: not there = 없음; distance filter = 거리 필터로 0; null value = 대기."""
    present = item['presence'] in (('both', 'gone') if side == 'a' else ('both', 'new'))
    if not present:
        return muted('없음')
    if item['%s_filtered' % side]:
        return muted('거리 필터로 0')
    return vcell(item[side], ctx.pct(item[side], kind))


def _item_rows(ctx, pair, kind):
    lines = []
    for item in pair['items']:
        upper, lower = item['upper_layer'], item['lower_layer']
        layer = upper if upper == lower else '%s/%s' % (upper, lower)
        ends = ' – '.join('%s@%s' % (e['electrode_id'], e['actual_layer']) for e in item['endpoints'])
        lines.append('<tr class="sub"><td class="l">%s</td><td class="l">%s</td><td class="l wrap">%s</td><td class="l">%s</td>'
                     '<td>%s</td><td>%s</td><td class="l">%s</td></tr>' % (
                         esc(MODE_LABELS.get(item['failure_type'], item['failure_type'])), esc(layer), esc(ends),
                         esc(PRESENCE_LABELS.get(item['presence'], item['presence'])),
                         _item_side_cell(ctx, kind, item, 'a'), _item_side_cell(ctx, kind, item, 'b'),
                         dcell(item['delta'], ctx.pct(item['delta'], kind))))
    return lines


def _pair_row(ctx, pair, kind, row_kind):
    name = '–'.join(pair['electrodes'])
    items = _item_rows(ctx, pair, kind)
    detail = ''
    if items:
        detail = ('<details><summary>항목 %d</summary><table><tr><th class="l">방식</th><th class="l">위/아래 레이어</th>'
                  '<th class="l">전극@레이어</th><th class="l">A·B</th><th>A</th><th>B</th><th class="l">Δ</th></tr>%s</table></details>'
                  % (len(items), ''.join(items)))
    anchor = ' id="pair-%s"' % esc(pair['rank']) if row_kind == 'top' else ''
    link = ''
    if getattr(ctx, 'maps', None) is not None:
        link = '<br><a class="maplink" href="#s2" data-map-pair="%s">지도에서 보기</a>' % esc(pair['pair_id'])
        card_link = _card_link(ctx, pair['pair_id'])
        if card_link:
            link += ' · ' + card_link
    return ('<tr%s %s><td>%s</td><td class="l"><b>%s</b>%s</td><td class="l">%s</td><td class="l">%s</td><td class="l">%s</td>'
            '<td>%s</td><td>%s</td><td class="l">%s</td><td class="l wrap">%s</td></tr>' % (
                anchor, _data_attrs(row_kind, pair['a'], pair['b'], pair['delta']), esc(pair['rank']), esc(name), link,
                ctx.change_badge(pair['change_kind']), esc(_pair_modes(pair)), esc(_pair_layers_text(pair)),
                vcell(pair['a'], pair['pct_a'], pair['present_a']), vcell(pair['b'], pair['pct_b'], pair['present_b']),
                dcell(pair['delta'], pair['pct_delta']), detail))


PAIR_HEAD = ('<tr><th>번호</th><th class="l">전극 쌍</th><th class="l">변화 종류</th><th class="l">방식</th><th class="l">레이어 A → B</th>'
             '<th>A</th><th>B</th><th class="l">Δ</th><th class="l">항목</th></tr>')


def _cno_rows(ctx, rows, row_kind):
    out = []
    for row in rows:
        a, b, d = row['a'], row['b'], row['delta']
        out.append('<tr %s><td class="l"><b>%s</b></td><td class="l">%s</td><td class="l">%s</td>%s</tr>' % (
            _data_attrs(row_kind, a, b, d), esc(row['electrode_id']), esc(row['density_layer']),
            esc(PRESENCE_LABELS.get(row['presence'], row['presence'])),
            _abc_cells(ctx, 'pixel', a, b, d, row['presence'] in ('both', 'gone'), row['presence'] in ('both', 'new'))))
    return out


def _section4(ctx):
    doc = ctx.doc
    n = ctx.top_pairs
    out = ['<section id="s4"><p class="q">어느 전극 쌍, 어느 레이어인가</p><h2>4. 전극 쌍별</h2>',
           '<p class="lead">전극 쌍 한 줄이 A 와 B 의 값을 가집니다. 번호 L# 은 line, P# 은 pixel 순위(B 값이 큰 순)입니다. '
           '항목을 펼치면 방식 · 위/아래 레이어 · 전극@레이어가 보입니다.</p>']
    for kind in KINDS:
        rows = [p for p in doc['pairs'] if p['kind'] == kind]
        shown, rest = rows[:n], rows[n:]
        t = doc['totals'][kind]['items']
        out.append('<h3 id="s4-%s">%s<b>%s</b> <span class="muted">%d/%d 쌍 표시 · 기준 vi_%s A = 100%%</span></h3>' % (
            kind, chip(kind), kind, len(shown), len(rows), kind))
        out.append('<table id="pairs-%s" class="ledger">%s' % (kind, PAIR_HEAD))
        out.extend(_pair_row(ctx, p, kind, 'top') for p in shown)
        if rest:
            a, b, d, pend = rest_sums(rest, [(lambda r, f=f: r[f]) for f in ('a', 'b', 'delta')])
            out.append('<tr class="rest" %s><td></td><td class="l"><b>기타 %d 쌍</b>%s</td><td colspan="3"></td>%s<td></td></tr>' % (
                _data_attrs('rest', a, b, d), len(rest), pending_note(pend), _abc_cells(ctx, kind, a, b, d)))
        out.append('<tr class="sum" %s><td></td><td class="l">합계</td><td class="l muted" colspan="3">전극 쌍 item 합</td>%s<td></td></tr>' % (
            _data_attrs('total', t['a'], t['b'], t['delta']), _abc_cells(ctx, kind, t['a'], t['b'], t['delta'])))
        out.append('</table>')
        if rest:
            out.append('<details><summary>기타 %d 쌍 전체 보기</summary><table class="ledger">%s%s</table></details>' % (
                len(rest), PAIR_HEAD, ''.join(_pair_row(ctx, p, kind, 'restitem') for p in rest)))
        if kind == 'pixel':
            out.append(_cno_block(ctx))
    unknown = [p for p in doc['pairs'] if p['kind'] not in KINDS]
    if unknown:
        out.append('<h3>불량 종류를 알 수 없는 쌍 %d 개</h3><p class="wrap muted">번호와 합계에 넣지 않습니다. %s</p>' % (
            len(unknown), esc(', '.join('–'.join(p['electrodes']) for p in unknown))))
    out.append('<h3>전극 쌍 × 레이어</h3>%s</section>' % _matrix(ctx))
    return ''.join(out)


def _cno_block(ctx):
    doc = ctx.doc
    rows = sorted(doc['cno'], key=lambda r: (r['delta'] is None, -abs(r['delta'] or 0.0), r['electrode_id'], r['density_layer']))
    n = ctx.top_pairs
    shown, rest = rows[:n], rows[n:]
    t = doc['totals']['pixel']['cno']
    head = '<tr><th class="l">전극</th><th class="l">density 레이어</th><th class="l">A·B</th><th>A</th><th>B</th><th class="l">Δ</th></tr>'
    out = ['<h3 id="s4-cno">%s<b>CNO</b> <span class="muted">%d/%d 줄 표시 · 전극 하나의 값 · 기준 vi_pixel A = 100%%</span></h3>' % (
        chip('pixel'), len(shown), len(rows)),
        '<table id="cno" class="ledger">%s' % head]
    out.extend(_cno_rows(ctx, shown, 'top'))
    if rest:
        a, b, d, pend = rest_sums(rest, [(lambda r, f=f: r[f]) for f in ('a', 'b', 'delta')])
        out.append('<tr class="rest" %s><td class="l"><b>기타 %d 줄</b>%s</td><td colspan="2"></td>%s</tr>' % (
            _data_attrs('rest', a, b, d), len(rest), pending_note(pend), _abc_cells(ctx, 'pixel', a, b, d)))
    out.append('<tr class="sum" %s><td class="l">CNO 합계</td><td colspan="2"></td>%s</tr></table>' % (
        _data_attrs('total', t['a'], t['b'], t['delta']), _abc_cells(ctx, 'pixel', t['a'], t['b'], t['delta'])))
    if rest:
        out.append('<details><summary>기타 %d 줄 전체 보기</summary><table class="ledger">%s%s</table></details>' % (
            len(rest), head, ''.join(_cno_rows(ctx, rest, 'restitem'))))
    return ''.join(out)


def _matrix(ctx):
    doc = ctx.doc
    out = []
    for kind in KINDS:
        layers = [row['layer'] for row in doc['layers'][kind]]
        shown = [p for p in doc['pairs'] if p['kind'] == kind][:ctx.top_pairs]
        out.append('<h3>%s<b>%s</b> <span class="muted">표시한 %d 쌍 · 칸 = 그 레이어 item 의 Δ (A 의 vi 대비). 상하 불량은 위 레이어 칸입니다</span></h3>' % (
            chip(kind), kind, len(shown)))
        if not shown or not layers:
            out.append('<p class="muted">표시할 쌍이 없습니다.</p>')
            continue
        out.append('<table class="hm"><tr><th class="l">전극 쌍</th><th class="l">변화 종류</th>%s<th>쌍 전체 Δ</th></tr>' % ''.join(
            '<th>%s</th>' % esc(layer) for layer in layers))
        for p in shown:
            cells = []
            for layer in layers:
                items = [i for i in p['items'] if i['analysis_layer'] == layer]
                if not items:
                    cells.append('<td class="muted">·</td>')
                    continue
                d = osum(i['delta'] for i in items)
                cells.append('<td>%s</td>' % dcell(d, ctx.pct(d, kind)))
            out.append('<tr><td class="l"><b>%s. %s</b></td><td class="l">%s</td>%s<td>%s</td></tr>' % (
                esc(p['rank']), esc('–'.join(p['electrodes'])), ctx.change_badge(p['change_kind']), ''.join(cells),
                dcell(p['delta'], p['pct_delta'])))
        out.append('</table>')
    return ''.join(out)


# ---- section 6: layers and sizes --------------------------------------------------------------------------------------

def _section6(ctx):
    doc = ctx.doc
    out = ['<section id="s6"><p class="q">공정 관점에서는 어떤가</p><h2>6. 레이어 · 이물 크기</h2>',
           '<p class="lead">비교는 전극으로 하고, 원인 설명은 레이어로 합니다. line 과 pixel 을 따로 봅니다. '
           'CNO 는 density 레이어 이름의 줄입니다.</p>']
    totals = doc['totals']
    for kind, label, ref_kind, total in (('line', 'line', 'line', totals['line']['items']),
                                         ('pixel', 'pixel', 'pixel', totals['pixel']['items']),
                                         ('cno', 'CNO (pixel)', 'pixel', totals['pixel']['cno'])):
        rows = doc['layers'][kind]
        out.append('<h3 id="s6-%s">%s<b>%s</b> <span class="muted">레이어 집계 · 기준 vi_%s A = 100%%</span></h3>' % (
            kind, chip(ref_kind), esc(label), ref_kind))
        if not rows:
            out.append('<p class="muted">레이어 줄이 없습니다.</p>')
            continue
        out.append('<table id="layers-%s"><tr><th class="l">레이어</th><th>A</th><th>B</th><th class="l">Δ</th><th class="l">주된 기여 (쌍, Δ)</th></tr>' % kind)
        for row in rows:
            tops = ' · '.join('%s %s' % (esc('–'.join(tp['electrodes'])), dcell(tp['delta'], ctx.pct(tp['delta'], ref_kind)))
                              for tp in row.get('top_pairs') or []) or muted('—')
            out.append('<tr %s><td class="l"><b>%s</b></td>%s<td class="l wrap">%s</td></tr>' % (
                _data_attrs('layer', row['a'], row['b'], row['delta']), esc(row['layer']),
                _abc_cells(ctx, ref_kind, row['a'], row['b'], row['delta']), tops))
        out.append('<tr class="sum" %s><td class="l">합계</td>%s<td></td></tr></table>' % (
            _data_attrs('total', total['a'], total['b'], total['delta']),
            _abc_cells(ctx, ref_kind, total['a'], total['b'], total['delta'])))
    out.append('<h3>레이어 × 이물 크기 (µm)</h3><p class="muted">칸 = 그 레이어·이물 크기의 Δ (A 의 vi 대비). 칸에 마우스를 올리면 A, B 값이 보입니다. '
               '모든 레이어에서 A, B 가 0 인 이물 크기는 열에서 뺍니다.</p>')
    for kind, label, ref_kind in (('line', 'line', 'line'), ('pixel', 'pixel', 'pixel'), ('cno', 'CNO (pixel)', 'pixel')):
        rows = doc['layers'][kind]
        sizes = sorted({s['diameter_um'] for row in rows for s in row['by_size'] if s['a'] != 0 or s['b'] != 0})
        dropped = len({s['diameter_um'] for row in rows for s in row['by_size']}) - len(sizes)
        out.append('<h3>%s<b>%s</b></h3>' % (chip(ref_kind), esc(label)))
        if not rows or not sizes:
            out.append('<p class="muted">값이 있는 이물 크기가 없습니다.</p>')
            continue
        out.append('<div class="scroll"><table class="hm"><tr><th class="l">레이어</th>%s</tr>' % ''.join(
            '<th>%s</th>' % esc(('%g' % s)) for s in sizes))
        for row in rows:
            by = {s['diameter_um']: s for s in row['by_size']}
            cells = []
            for size in sizes:
                s = by.get(size)
                if s is None:
                    cells.append('<td class="muted">·</td>')
                    continue
                title = 'A %s / B %s' % (num(s['a']), num(s['b']))
                cells.append('<td title="%s">%s</td>' % (esc(title), dcell(s['delta'], ctx.pct(s['delta'], ref_kind))))
            out.append('<tr><td class="l"><b>%s</b></td>%s</tr>' % (esc(row['layer']), ''.join(cells)))
        out.append('</table></div>')
        if dropped:
            out.append('<p class="muted">값이 모두 0 인 이물 크기 %d 개는 뺐습니다.</p>' % dropped)
    out.append('</section>')
    return ''.join(out)


# ---- section 7: reference ---------------------------------------------------------------------------------------------

RULES = (
    ('% 단위', 'A 의 전체 vi 를 100% 로 본 값입니다. line 은 vi_line A, pixel 은 vi_pixel A 가 기준입니다. A 의 vi 가 0 이면 % 를 계산하지 않고 원래 값만 보입니다.'),
    ('절반 분할', '전극 쌍의 값을 두 전극에 1/2 씩 넣습니다. CNO 는 그 전극에 전부 넣습니다.'),
    ('공유 영역 분담', '여러 쌍이 함께 덮는 영역은 덮는 쌍 수 n 으로 1/n 씩 나눕니다. 한 쌍의 item 이 같은 영역을 덮으면 그 쌍의 몫을 item 수로 다시 나눕니다.'),
    ('합계 관계', '불량 종류마다 전극 쌍 합 + CNO = 전극 합. 곡선 보정 줄과 CRP 줄을 더한 열 합계는 실제 vi 와 같습니다(합계식 6개).'),
    ('A 값 · B 값', '쌍과 전극의 A 값은 B 의 CRP 기준으로 환산한 값입니다. CRP 줄은 A = −d, B = 0, Δ = d 이고 전극에 나누지 않습니다.'),
    ('표시', '"없음" 은 그 제품에 그 전극이나 쌍이 없다는 뜻입니다. "0%" 는 있지만 값이 0 이라는 뜻입니다. "해당 없음" 은 그 불량 종류와 관계가 없다는 뜻입니다. '
           '"대기" 는 값을 계산할 수 없다는 뜻입니다. "거리 필터로 0" 은 거리 필터가 그 쪽 값을 0 으로 지웠다는 뜻입니다.'),
    ('line 과 pixel', 'line % 와 pixel % 는 기준이 달라 더하거나 한 순서로 섞지 않습니다.'))
TERMS = (
    ('불량 종류', 'line 또는 pixel'),
    ('불량 방식', '측면(같은 레이어의 두 전극), 상하(다른 레이어의 두 전극, "위/아래 레이어"로 표기), CNO(전극 접촉 열림)'),
    ('같은 쌍 증감', '같은 두 전극, 같은 레이어. 불량 값만 달라졌습니다.'),
    ('레이어 변경', '같은 두 전극. 불량이 있는 레이어나 방식, 방향이 달라졌습니다.'),
    ('새 쌍 · 사라진 쌍', '두 전극은 A 와 B 에 모두 있습니다. 둘 사이의 불량이 새로 생기거나 없어졌습니다.'),
    ('전극 추가·제거', '전극이 새로 생기거나 없어져서 함께 생기거나 없어진 불량입니다. 두 제품의 전극 목록이 모두 완전할 때만 판정합니다.'),
    ('변화 없음', '같은 두 전극, 같은 레이어. 값이 같습니다.'),
    ('값 대기', '값이 필요한 판정인데 값을 계산할 수 없는 쌍입니다.'),
    ('CRP 줄', '두 제품의 CRP 가 다를 때의 차이 한 줄입니다. pixel 에만 있습니다.'),
    ('곡선 보정 줄', '절편 한 줄입니다. 전극에 나누지 않습니다.'))


def _section7(ctx):
    doc = ctx.doc
    inputs = doc['inputs']
    out = ['<section id="s7"><p class="q">어떻게 계산했나</p><h2>7. 참고</h2><div class="grid2"><div><h3>계산 규칙</h3><table>']
    out.extend('<tr><td class="l"><b>%s</b></td><td class="l wrap">%s</td></tr>' % (esc(k), esc(v)) for k, v in RULES)
    out.append('</table></div><div><h3>용어</h3><table>')
    out.extend('<tr><td class="l"><b>%s</b></td><td class="l wrap">%s</td></tr>' % (esc(k), esc(v)) for k, v in TERMS)
    out.append('</table></div></div>')
    # products
    out.append('<h3>제품 정보</h3><table id="products"><tr><th class="l">제품</th><th class="l">model_group</th><th class="l">pair_table_hash</th><th>CRP</th></tr>')
    for role, name in (('reference', '기준 A'), ('target', '대상 B')):
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
    out.append('<h3>caa</h3><p class="muted">caa 지표는 ledger.json 에 들어 있지 않습니다. 07 보고서(compare2-report)의 caa 표시를 보세요.</p>')
    # inputs and settings
    out.append('<h3>입력과 설정</h3><table id="inputs">')
    for label, value in (('compare2.json sha256', inputs.get('compare2_json_sha256')), ('config sha256', inputs.get('config_sha256')),
                         ('ledger_basis', inputs.get('ledger_basis')), ('ledger 상태', doc.get('status'))):
        out.append('<tr><td class="l"><b>%s</b></td><td class="l wrap">%s</td></tr>' % (esc(label), esc(value)))
    for role, name in (('reference', '기준 A'), ('target', '대상 B')):
        inv = inputs['electrode_inventory'][role]
        out.append('<tr><td class="l"><b>전극 목록 %s</b></td><td class="l wrap">출처 %s · 상태 %s · %d 개 · sha256 %s</td></tr>' % (
            name, esc(inv['source']), esc(inv['status']), len(inv['electrodes']), esc(inv.get('sha256'))))
    out.append('<tr><td class="l"><b>옵션</b></td><td class="l">상위 쌍 %d · 상위 전극 %d</td></tr></table>' % (ctx.top_pairs, ctx.top_electrodes))
    # checks
    checks = doc.get('checks') or {}
    out.append('<h3>검산</h3><p>합계 검산 상태: <b>%s</b></p>' % esc(checks.get('status')))
    equations = checks.get('equations') or {}
    if equations:
        out.append('<table id="equations"><tr><th class="l">합계식</th><th class="l">상태</th></tr>%s</table>' % ''.join(
            '<tr><td class="l">%s</td><td class="l">%s</td></tr>' % (esc(name), esc(e.get('status'))) for name, e in equations.items()))
    # warnings
    warnings = doc.get('warnings') or []
    out.append('<h3>경고</h3>')
    if warnings:
        out.append('<ul id="warnings">%s</ul>' % ''.join(
            '<li><b>%s</b> %s</li>' % (esc(w.get('code')), esc(json.dumps({k: v for k, v in w.items() if k != 'code'},
                                                                          ensure_ascii=False))) for w in warnings))
    else:
        out.append('<p class="muted">경고가 없습니다.</p>')
    # unavailable
    unavailable = doc.get('unavailable') or []
    out.append('<h3>계산 불가 항목 %d 개</h3>' % len(unavailable))
    if unavailable:
        rows = ''.join('<tr><td class="l">%s</td><td class="l wrap">%s</td><td class="l">%s</td><td class="l wrap">%s</td></tr>' % (
            esc(u.get('scope')), esc(u.get('id')), esc(u.get('side', '')), esc(', '.join(u.get('reasons') or [])))
            for u in unavailable)
        out.append('<details><summary>목록 보기</summary><table id="unavailable"><tr><th class="l">범위</th><th class="l">대상</th>'
                   '<th class="l">제품</th><th class="l">사유</th></tr>%s</table></details>' % rows)
    else:
        out.append('<p class="muted">없습니다.</p>')
    out.append('</section>')
    return ''.join(out)


# ---- section 2: layout risk maps (needs --maps-dir) -------------------------------------------------------------------

MAPS_SCHEMA = 'ledger_maps_v1'
ROLE_KEYS = (('reference', 'A'), ('target', 'B'))
# the PNG names ledger-maps writes (one folder, no path separator, no ..)
MAP_FILE_NAME = re.compile(r'map_(reference|target)_\d{3}_(el|it)\.png')


def _read_maps(maps_dir, ledger_sha):
    """{'doc', 'sha', 'pngs'} of a ledger-maps folder, or NumericContractError ledger_maps_input_missing /
    ledger_maps_schema_mismatch / ledger_maps_stale (the maps were made from another ledger.json)."""
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
        raise NumericContractError('ledger_maps_schema_mismatch', f'{path}: schema_version {found!r} (필요 {MAPS_SCHEMA})')
    recorded = (doc.get('inputs') or {}).get('ledger_json_sha256')
    if recorded != ledger_sha:
        raise NumericContractError('ledger_maps_stale', f'{path}: 다른 ledger.json 으로 만든 지도입니다(지도 {recorded}, 현재 {ledger_sha}). '
                                                      'ledger-maps 를 다시 실행하세요')
    pngs = {}
    for role, _key in ROLE_KEYS:
        for entry in ((doc.get('sides') or {}).get(role) or {}).get('maps') or []:
            name = entry.get('file') if isinstance(entry, dict) else None
            if not isinstance(name, str) or not MAP_FILE_NAME.fullmatch(name):
                raise NumericContractError('ledger_maps_input_missing', f'{folder}: 지도 파일 이름 {name!r} 이 폴더 직속 map_*.png 가 아닙니다')
            png = folder / name
            if not png.is_file():
                raise NumericContractError('ledger_maps_input_missing', f'{png}: 지도 PNG 가 없습니다')
            pngs[entry['file']] = png.read_bytes()
    return {'doc': doc, 'sha': hashlib.sha256(raw).hexdigest(), 'pngs': pngs}


def _plain_val(value, pct, present=True):
    if not present:
        return '없음'
    if value is None:
        return '대기'
    if value == 0:
        return '0%'
    return pct_txt(pct) if pct is not None else raw_pct(value)


def _plain_delta(delta, pct):
    if delta is None:
        return '대기'
    if delta == 0:
        return '0'
    return spct(pct) if pct is not None else raw_pp(delta)


def _maps_top(ctx):
    """Pairs of each kind that get a number in the list and on the maps (ledger-maps --top-pairs)."""
    top = (ctx.maps['doc'].get('options') or {}).get('top_pairs')
    top = top if isinstance(top, int) and not isinstance(top, bool) and top >= 1 else ctx.top_pairs
    return {kind: [p for p in ctx.doc['pairs'] if p['kind'] == kind][:top] for kind in KINDS}


def _side_maps(ctx, role):
    return ((ctx.maps['doc'].get('sides') or {}).get(role) or {})


def _pair_map_state(ctx, pair, role):
    """What the map of one product says about a pair: 없음 / 지도 영역 없음 / 값 대기 / 표시."""
    present = pair['present_a'] if role == 'reference' else pair['present_b']
    if not present:
        return '이 제품에 없음'
    items = [i for i in _side_maps(ctx, role).get('items') or [] if i['pair_id'] == pair['pair_id']]
    if not items:
        return '지도 영역 없음'
    if all(i['status'] == 'no_map_area' for i in items):
        return '지도 영역 없음'
    if all(i['status'] == 'pending' for i in items):
        return '값 대기'
    return '표시'


def _maps_payload(ctx):
    maps = ctx.maps
    mdoc, pngs = maps['doc'], maps['pngs']
    doc = ctx.doc
    top = _maps_top(ctx)
    top_ids = {p['pair_id'] for rows in top.values() for p in rows}
    pairs = []
    for p in doc['pairs']:
        if p['kind'] not in KINDS or not p.get('rank'):
            continue
        pairs.append({'id': p['pair_id'], 'no': p['rank'], 'name': '–'.join(p['electrodes']), 'e': list(p['electrodes']),
                      'kind': p['kind'], 'ck': p['change_kind'], 'cl': ctx.change_labels.get(p['change_kind'], p['change_kind']),
                      'a': p['a'], 'b': p['b'], 'd': p['delta'], 'pa': bool(p['present_a']), 'pb': bool(p['present_b']),
                      'ta': _plain_val(p['a'], p['pct_a'], p['present_a']), 'tb': _plain_val(p['b'], p['pct_b'], p['present_b']),
                      'td': _plain_delta(p['delta'], p['pct_delta']), 'top': p['pair_id'] in top_ids})
    sides = {}
    max_pct = {kind: 0.0 for kind in KINDS}
    for role, key in ROLE_KEYS:
        side = _side_maps(ctx, role)
        items = []
        for item in side.get('items') or []:
            pct = ctx.pct(item['value'], item['kind']) if item['kind'] in KINDS else None
            if pct is not None and item['kind'] in KINDS:
                max_pct[item['kind']] = max(max_pct[item['kind']], abs(pct))
            items.append({'n': item['number'], 'p': item['pair_id'], 'k': item['kind'], 'l': ', '.join(item.get('layers') or []),
                          'pct': pct, 't': _plain_val(item['value'], pct), 's': item['status']})
        entries = []
        for entry in sorted(side.get('maps') or [], key=lambda m: m['file']):
            height, width = entry['shape_yx']
            entries.append({'file': entry['file'], 'layer': entry['layer'], 'kind': entry['kind'], 'w': width, 'h': height,
                            'px_um': entry.get('px_um'), 'numbers': entry['numbers'],
                            'png': 'data:image/png;base64,' + base64.b64encode(pngs[entry['file']]).decode('ascii')})
        shape = entries[0] if entries else None
        sides[key] = {'items': items, 'maps': entries, 'w': shape['w'] if shape else 0, 'h': shape['h'] if shape else 0,
                      'electrodes': [{'number': e['number'], 'name': e['name']} for e in side.get('electrodes') or []]}
    electrodes = {row['electrode']: [bool(row['present_a']), bool(row['present_b'])] for row in doc['electrodes']}
    demo = None
    for kind in KINDS:
        rows = _sorted_electrodes(ctx, kind)
        if rows and demo is None:
            demo = rows[0]['electrode']
    return {'pairs': pairs, 'sides': sides, 'electrodes': electrodes, 'max_pct': max_pct, 'demo_electrode': demo,
            'cards': _cards_payload(ctx)}


def _legend(mode):
    def sw(css, text):
        return '<span><i class="sw" style="%s"></i>%s</span>' % (css, esc(text))
    if mode == 'size':
        return (sw('background:var(--line)', 'line 불량') + sw('background:var(--pixel)', 'pixel 불량')
                + sw('background:var(--line);opacity:.25', '옅음 = 작음') + sw('background:var(--line)', '진함 = 큼 (같은 종류의 가장 큰 값 = 1)')
                + sw('background:var(--mid);border:1px solid var(--common)', '전극 형상'))
    return (sw('background:var(--up)', '증가') + sw('background:var(--down)', '감소') + sw('background:var(--common)', '변화 없음')
            + sw('background:var(--up);border:2px solid var(--text-primary)', '새로 생김 (B 에만)')
            + sw('background:none;border:2px dashed var(--down)', '사라짐 (A 에만)')
            + sw('background:repeating-linear-gradient(45deg,var(--up) 0 2px,transparent 2px 5px)', '레이어 변경 (빗금, 색은 증감)')
            + sw('background:var(--mid);border:1px solid var(--common)', '전극 형상'))


def _map_list(ctx):
    out = []
    head = ('<tr><th>번호</th><th class="l">전극 쌍</th><th class="l">변화 종류</th><th>A</th><th>B</th><th class="l">Δ</th>'
            '<th class="l">A 지도</th><th class="l">B 지도</th><th class="l">위치 카드</th></tr>')
    for kind, rows in _maps_top(ctx).items():
        out.append('<h3>%s<b>%s</b> <span class="muted">%d 쌍 · 기준 vi_%s A = 100%%</span></h3>' % (chip(kind), kind, len(rows), kind))
        if not rows:
            out.append('<p class="muted">표시할 쌍이 없습니다.</p>')
            continue
        out.append('<table class="ledger mlist" id="mlist-%s">%s' % (kind, head))
        for p in rows:
            out.append('<tr data-sel-p="%s" tabindex="0"><td>%s</td><td class="l"><b>%s</b></td><td class="l">%s</td><td>%s</td><td>%s</td>'
                       '<td class="l">%s</td><td class="l muted">%s</td><td class="l muted">%s</td><td class="l">%s</td></tr>' % (
                           esc(p['pair_id']), esc(p['rank']), esc('–'.join(p['electrodes'])), ctx.change_badge(p['change_kind']),
                           vcell(p['a'], p['pct_a'], p['present_a']), vcell(p['b'], p['pct_b'], p['present_b']),
                           dcell(p['delta'], p['pct_delta']), esc(_pair_map_state(ctx, p, 'reference')),
                           esc(_pair_map_state(ctx, p, 'target')), _card_link(ctx, p['pair_id'])))
        out.append('</table>')
    return ''.join(out)


def _section2(ctx):
    mdoc = ctx.maps['doc']
    layers = sorted({e['layer'] for _role, key in ROLE_KEYS for e in _side_maps(ctx, _role).get('maps') or []})
    scale = mdoc.get('scale_steps') or []
    notes = []
    for role, key in ROLE_KEYS:
        px = [e.get('px_um') for e in _side_maps(ctx, role).get('maps') or [] if e.get('px_um')]
        notes.append('%s 지도 %s µm/픽셀' % (key, '%g' % px[0] if px else '—'))
    hidden = ' · '.join('%s %d' % (key, ((_side_maps(ctx, role).get('hidden') or {}).get('item_px') or 0)) for role, key in ROLE_KEYS)
    tile = (mdoc.get('options') or {}).get('tile_um')
    diameter = (mdoc.get('report') or {}).get('diameter_um')
    out = ['<section id="s2"><p class="q">layout 의 어디인가</p><h2>2. layout 리스크 지도</h2>',
           '<p class="lead">두 지도는 위치를 맞추지 않습니다. 전극 이름으로 연결합니다. "변화" 보기는 무엇이 달라졌는지, '
           '"리스크 크기" 보기는 각 제품에서 어디가 큰지 보입니다.</p>',
           '<div class="band partial" id="mapwarn" hidden><b>지도를 표시하지 않습니다</b> — 이 브라우저가 지도 그림의 번호를 바꿔 읽었습니다'
           '(번호별 픽셀 수가 ledger_maps.json 과 다릅니다). 아래 목록만 믿고 쓰세요.</div>',
           '<div class="chips"><span class="muted">보기</span><span id="modes"><button type="button" data-mode="change" class="on">변화</button>'
           '<button type="button" data-mode="size">리스크 크기</button></span>'
           '<span class="muted" style="margin-left:14px">레이어</span><span id="mlayers"><button type="button" data-layer="" class="on">전체</button>%s</span>'
           '<span class="muted" style="margin-left:14px">전극 찾기</span><input id="esearch" list="elist" placeholder="전극 이름" '
           'aria-label="전극 이름으로 선택" autocomplete="off"><datalist id="elist">%s</datalist>'
           '<button type="button" id="mclear" style="margin-left:8px">선택 해제</button></div>' % (
               ''.join('<button type="button" data-layer="%s">%s</button>' % (esc(layer), esc(layer)) for layer in layers),
               ''.join('<option value="%s">' % esc(row['electrode']) for row in ctx.doc['electrodes'])),
           '<div class="maps"><div><h3>기준 A</h3><canvas class="map" id="mapA" width="1" height="1" role="img" aria-label="기준 A 지도"></canvas>'
           '<p class="muted mnote" id="noteA"></p></div>'
           '<div><h3>대상 B</h3><canvas class="map" id="mapB" width="1" height="1" role="img" aria-label="대상 B 지도"></canvas>'
           '<p class="muted mnote" id="noteB"></p></div></div>',
           '<div class="legend" id="legend-change">%s</div><div class="legend" id="legend-size" hidden>%s</div>' % (_legend('change'), _legend('size')),
           '<p class="muted" id="mapnote">지도 이물 크기 %s µm · 분석 타일 %s µm · %s · 지도 배율을 거칠게 한 횟수 %d. 겹친 영역은 한 번호만 보입니다'
           '(가려진 픽셀 %s). 색과 진하기는 쌍 전체의 값이고 그 위치만의 값이 아닙니다.</p>' % (
               esc('%g' % diameter) if diameter is not None else '—', esc('%g' % tile) if tile is not None else '—',
               esc(' · '.join(notes)), len(scale), esc(hidden)),
           '<div class="explain" id="explain"><span class="muted">선택한 것이 없습니다. 지도의 전극이나 불량 영역, 아래 목록의 줄을 누르면 '
           '두 지도에서 함께 강조됩니다.</span></div>',
           '<h3>리스크 위치 목록 — 불량 종류별로 B 값이 큰 순서. 번호는 4절과 같습니다.</h3>', _map_list(ctx),
           '<p class="muted">CNO 는 지도가 없어 이 목록에 없습니다. 3절 전극 장부의 "그중 CNO" 열에서 봅니다.</p>',
           '</section>']
    return ''.join(out)


# ---- section 5: location cards (needs --maps-dir) -----------------------------------------------------------------------

BAND_TEXT = {
    'coords_no_candidates': '04(candidates-change) 결과 폴더를 주지 않아 좌표를 맞추지 않았습니다. 모든 줄을 제품별 "있는 쪽 값만" 으로 보입니다.',
    'coords_not_applied': '04 가 좌표 정렬을 적용하지 않았습니다(진단 모드). 모든 줄을 제품별 "있는 쪽 값만" 으로 보입니다.',
    'coords_stale': '04 결과가 지금 입력과 다릅니다(지문이 맞지 않음). 좌표 대응을 끄고 제품별 값만 보입니다.',
    'coords_unavailable': '04 결과의 모드·정렬 자료·지문을 확인할 수 없어 좌표 대응을 끄고 제품별 값만 보입니다.',
    'coords_resolution_too_coarse': '분석 타일이 대응 허용치보다 커서 좌표 대응과 가장자리 판정을 하지 않습니다. 가장자리 줄은 비교 제외입니다.'}
REASON_TEXT = dict(BAND_TEXT, **{
    'layer_change': '레이어가 바뀐 쌍이라 같은 좌표로 비교하지 않습니다',
    'items_not_on_both_sides': '한쪽 제품에만 있는 항목이 있어 비교하지 않습니다',
    'not_comparable': '같은 좌표로 비교할 수 없는 쌍입니다',
    'new_pair': 'B 에만 있는 쌍입니다', 'gone_pair': 'A 에만 있는 쌍입니다',
    'b_conflict': '앞 줄이 차지한 B 자리와 겹쳐 대응하지 않았습니다',
    'outside_b_frame': '옮긴 자리가 B frame 밖입니다',
    'low_membership_overlap': '옮긴 자리의 불량 영역이 B 와 충분히 겹치지 않습니다',
    'offset_spread': '범위 안 정렬 이동량이 서로 달라 대응하지 않았습니다',
    'no_field_cell': '범위와 겹치는 정렬 칸이 없습니다',
    'no_row_matched': '대응된 줄이 없습니다',
    'edge_no_dl1': 'frame 가장자리 밖이고 DL1 자료가 없어 비교 제외',
    'edge_no_repeat_position': 'frame 가장자리 밖이고 반복 위치가 없어 비교 제외',
    'edge_mismatch': '가장자리 반복 동등성이 맞지 않아 비교 제외',
    'edge_unverifiable': '가장자리 반복 동등성을 확인할 수 없어 비교 제외'})
ROW_TYPE = {'linear': '구간', 'point': '위치'}
STATUS_LABEL = {'matched': '같은 좌표 비교', 'a_only': 'A 값만', 'b_only': 'B 값만', 'not_compared': '비교하지 않음'}


def _init_cards(ctx):
    ctx.pair_by_id = {p['pair_id']: p for p in ctx.doc['pairs']}
    ctx.cards = [c for c in (ctx.maps['doc'].get('cards') or []) if isinstance(c, dict) and c.get('pair_id')]
    ctx.card_of = {}
    for index, card in enumerate(ctx.cards):
        ctx.card_of.setdefault(card['pair_id'], index)
    ctx.cards_payload = None


def _card_link(ctx, pair_id):
    """'위치 보기' link of a pair that has a card; nothing for a pair without one."""
    if getattr(ctx, 'maps', None) is None or pair_id not in getattr(ctx, 'card_of', {}):
        return ''
    return '<a class="cardlink" href="#card-%s">위치 보기</a>' % esc(pair_id)


def _reason_text(ctx, text):
    if not text:
        return ''
    parts = []
    for code in str(text).split(','):
        code = code.strip()
        parts.append(REASON_TEXT.get(code) or ctx.change_labels.get(code) or code)
    return ' · '.join(parts)


def _pair_name(ctx, pair_id):
    pair = ctx.pair_by_id.get(pair_id)
    return '–'.join(pair['electrodes']) if pair else pair_id


def _card_state(ctx, card, role):
    """absent = the pair is not in that product; pending = its ledger value is null; ok."""
    pair = ctx.pair_by_id.get(card['pair_id'])
    if pair is not None and not pair['present_a' if role == 'reference' else 'present_b']:
        return 'absent'
    return 'pending' if card.get('a' if role == 'reference' else 'b') is None else 'ok'


def _card_val(ctx, card, role, value):
    state = _card_state(ctx, card, role)
    if state == 'absent':
        return muted('없음')
    if state == 'pending' or value is None:
        return muted('대기')
    return vcell(value, ctx.pct(value, card['kind']))


def _card_others(ctx, card, role, row):
    others = row.get('others')
    if row.get('edge') == 'excluded' or not isinstance(others, dict) or _card_state(ctx, card, role) != 'ok':
        return '—'
    kind = card['kind']
    lines = ['%s %s' % (esc(_pair_name(ctx, o['pair_id'])), esc(_plain_val(o['value'], ctx.pct(o['value'], kind))))
             for o in others.get('top') or []]
    if others.get('others_n'):
        lines.append('외 %d 쌍 %s' % (others['others_n'], esc(_plain_val(others.get('others_value'), ctx.pct(others.get('others_value'), kind)))))
    return '<br>'.join(lines) or '—'


def _card_total(ctx, card, role, row):
    if row.get('edge') == 'excluded':
        return muted('비교 제외')
    return _card_val(ctx, card, role, row.get('range_total'))


def _repeat_note(row):
    """repeat_used: the row's tiles (and value) include image tiles one period inside the frame; `rect` is only the original box."""
    count = row.get('image_tiles')
    return '안쪽 반복 사용 — 안쪽 반복 타일 %d개 포함' % count if isinstance(count, int) and count > 0 else '안쪽 반복 사용'


def _row_notes(ctx, card, row):
    notes = []
    match = row.get('match') or {}
    if row['edge'] == 'excluded':
        return _reason_text(ctx, row.get('edge_reason')) or '비교 제외'
    if row['edge'] == 'repeat_used':
        notes.append(_repeat_note(row))
    if match.get('partial_matched'):
        notes.append('대응 부분 제외')
    reason = _reason_text(ctx, match.get('reason'))
    if reason:
        notes.append(reason)
    elif not notes:
        side_only = {'a_only': 'A 에만 있는 쌍', 'b_only': 'B 에만 있는 쌍'}.get((card.get('match') or {}).get('status'))
        notes.append(side_only or '대응하지 않은 줄')
    return ' · '.join(notes)


def _same_table(ctx, card, a_rows, by_id):
    kind = card['kind']
    pairs_ = []
    for arow in a_rows:
        brow = by_id.get((arow.get('match') or {}).get('counterpart'))
        if (arow.get('match') or {}).get('status') == 'matched' and brow is not None:
            pairs_.append((arow, brow))
    if not pairs_:
        return pairs_, '<p class="muted">같은 좌표로 대응된 줄이 없습니다.%s</p>' % (
            esc(' — ' + _reason_text(ctx, (card.get('match') or {}).get('reason'))) if (card.get('match') or {}).get('reason') else '')
    out = ['<table class="ledger"><tr><th class="l">줄</th><th class="l">종류</th><th>A 값</th><th>B 값</th><th class="l">Δ</th>'
           '<th class="l">다른 쌍 A</th><th class="l">다른 쌍 B</th><th>범위 합계 A</th><th>범위 합계 B</th><th class="l">표시</th></tr>']
    for arow, brow in pairs_:
        a, b = arow.get('pair'), brow.get('pair')
        pending = _card_state(ctx, card, 'reference') != 'ok' or _card_state(ctx, card, 'target') != 'ok'
        delta = None if pending or a is None or b is None else b - a
        notes = ' · '.join(_repeat_note(r) for r in (arow, brow) if r['edge'] == 'repeat_used')
        out.append('<tr><td class="l rowid">%s ↔ %s</td><td class="l">%s</td><td>%s</td><td>%s</td><td class="l">%s</td>'
                   '<td class="l wrap">%s</td><td class="l wrap">%s</td><td>%s</td><td>%s</td><td class="l">%s</td></tr>' % (
                       esc(arow['id']), esc(brow['id']), esc(ROW_TYPE.get(arow['type'], arow['type'])),
                       _card_val(ctx, card, 'reference', a), _card_val(ctx, card, 'target', b),
                       muted('대기') if delta is None else dcell(delta, ctx.pct(delta, kind)),
                       _card_others(ctx, card, 'reference', arow), _card_others(ctx, card, 'target', brow),
                       _card_total(ctx, card, 'reference', arow), _card_total(ctx, card, 'target', brow), esc(notes)))
    out.append('</table>')
    return pairs_, ''.join(out)


def _only_table(ctx, card, rows, skip_ids):
    rest = [r for r in rows if r['id'] not in skip_ids]
    if not rest:
        return '<p class="muted">없습니다.</p>'
    out = ['<table class="ledger"><tr><th class="l">줄</th><th class="l">제품</th><th class="l">종류</th><th>이 쌍 값</th>'
           '<th class="l">다른 쌍</th><th>범위 합계</th><th class="l">설명</th></tr>']
    for row in rest:
        role = row['side']
        value = muted('비교 제외') if row['edge'] == 'excluded' else _card_val(ctx, card, role, row.get('pair'))
        out.append('<tr><td class="l rowid">%s</td><td class="l">%s</td><td class="l">%s</td><td>%s</td><td class="l wrap">%s</td><td>%s</td>'
                   '<td class="l wrap">%s</td></tr>' % (
                       esc(row['id']), 'A' if role == 'reference' else 'B', esc(ROW_TYPE.get(row['type'], row['type'])), value,
                       _card_others(ctx, card, role, row), _card_total(ctx, card, role, row), esc(_row_notes(ctx, card, row))))
    out.append('</table>')
    return ''.join(out)


def _not_listed(card):
    listed = (card.get('match') or {}).get('not_listed') or {}
    parts = ['%s %d 곳' % (key, (listed.get(role) or {})['n']) for role, key in ROLE_KEYS if (listed.get(role) or {}).get('n')]
    if not parts:
        return ''
    return '<p class="muted not-listed">그 밖 %s — 줄 수 상한에 들지 못한 점형 자리라 표에 없습니다.</p>' % esc(' · '.join(parts))


def _totals_table(ctx, card):
    kind = card['kind']

    def cell(role, group, final=False):
        state = _card_state(ctx, card, role)
        if state == 'absent':
            return muted('없음')
        if state == 'pending':
            return muted('대기')
        value = (card.get(group) or {}).get(role)
        return muted('대기') if value is None else vcell(value, ctx.pct(value, kind))
    rows = (('범위 안 합 (줄의 이 쌍 값을 더한 값)', 'rows_sum', ''),
            ('이 쌍 관찰 영역 값 (창 ∪ 쓴 범위)', 'observed', ''),
            ('4절 쌍 값 (참고)', 'section4_ref', 'unit 한 장 기준이라 4절 값과 다릅니다'))
    out = ['<table class="ledger"><tr><th class="l">합계 줄</th><th>A</th><th>B</th><th class="l">비고</th></tr>']
    for label, group, note in rows:
        out.append('<tr class="%s"><td class="l">%s</td><td>%s</td><td>%s</td><td class="l muted">%s</td></tr>' % (
            'sum' if group == 'rows_sum' else '', esc(label), cell('reference', group), cell('target', group), esc(note)))
    out.append('</table>')
    error = card.get('rounding_error_um')
    if isinstance(error, (int, float)) and error:
        out.append('<p class="muted">좌표 반올림 오차 최대 %s µm</p>' % esc('%g' % error))
    return ''.join(out)


def _card_geo(ctx, role):
    """Map pixel geometry of one product: frame µm of map pixel (0, 0), µm per map pixel, map size. None if unknown."""
    side = _side_maps(ctx, role)
    frame = side.get('frame') or {}
    maps = sorted(side.get('maps') or [], key=lambda m: m['file'])
    origin, pitch = frame.get('origin_um'), frame.get('pitch_um')
    if not maps or not origin or not pitch:
        return None
    entry = maps[0]
    px, op, shape = entry.get('px_um'), entry.get('origin_px'), entry.get('shape_yx')
    if not px or not op or not shape:
        return None
    return {'ox': origin[0] + op[0] * pitch, 'oy': origin[1] + op[1] * pitch, 'px': px, 'w': shape[1], 'h': shape[0]}


def _card_side_view(rows, geo):
    """{'vb', 'z', 'rows'} of one product's card map: the crop around the rows in map pixels, zoom, rows in crop-free map pixels."""
    if geo is None or not rows:
        return None
    boxes = []
    for row in rows:
        x0, y0, x1, y1 = row['rect']
        boxes.append((row, [(x0 - geo['ox']) / geo['px'], (y0 - geo['oy']) / geo['px'],
                            (x1 - geo['ox']) / geo['px'], (y1 - geo['oy']) / geo['px']]))
    inside = [b for r, b in boxes if b[2] > 0 and b[0] < geo['w'] and b[3] > 0 and b[1] < geo['h']]
    live = [b for r, b in boxes if r['edge'] != 'excluded' and b[2] > 0 and b[0] < geo['w'] and b[3] > 0 and b[1] < geo['h']]
    use = live or inside
    if not use:
        return None
    bx0, by0 = min(b[0] for b in use), min(b[1] for b in use)
    bx1, by1 = max(b[2] for b in use), max(b[3] for b in use)
    pad = max(10.0, 0.25 * max(bx1 - bx0, by1 - by0))
    vx0, vy0 = max(0, int(math.floor(bx0 - pad))), max(0, int(math.floor(by0 - pad)))
    vx1, vy1 = min(geo['w'], int(math.ceil(bx1 + pad))), min(geo['h'], int(math.ceil(by1 + pad)))
    if vx1 - vx0 < 2 or vy1 - vy0 < 2:
        return None
    return {'vb': [vx0, vy0, vx1, vy1], 'z': max(1, min(8, 520 // (vx1 - vx0))),
            'rows': [{'id': r['id'], 'e': r['edge'], 'n': int(r.get('image_tiles') or 0), 'b': [round(v, 2) for v in b]}
                     for r, b in boxes]}


def _cards_payload(ctx):
    if ctx.cards_payload is not None:
        return ctx.cards_payload
    out = []
    geo = {role: _card_geo(ctx, role) for role, _key in ROLE_KEYS}
    for index, card in enumerate(ctx.cards):
        sides = {}
        for role, key in ROLE_KEYS:
            view = _card_side_view([r for r in card.get('rows') or [] if r.get('side') == role], geo[role])
            if view is not None:
                sides[key] = view
        out.append({'i': index, 'p': card['pair_id'], 'sides': sides})
    ctx.cards_payload = out
    return out


def _card_html(ctx, index, card, view):
    kind, pair_id = card['kind'], card['pair_id']
    pair = ctx.pair_by_id.get(pair_id)
    name = _pair_name(ctx, pair_id)
    rank = card.get('rank') if pair is None else pair.get('rank')
    shown = {k: [p['pair_id'] for p in ctx.doc['pairs'] if p['kind'] == k][:ctx.top_pairs] for k in KINDS}
    href = '#pair-%s' % esc(rank) if pair is not None and pair_id in shown.get(kind, []) and rank else '#s4'
    rows = card.get('rows') or []
    by_id = {r['id']: r for r in rows}
    a_rows = [r for r in rows if r.get('side') == 'reference']
    status = (card.get('match') or {}).get('status')
    a, b, d = card.get('a'), card.get('b'), card.get('delta')
    state_a, state_b = _card_state(ctx, card, 'reference'), _card_state(ctx, card, 'target')
    head_a = muted('없음') if state_a == 'absent' else vcell(a, ctx.pct(a, kind))
    head_b = muted('없음') if state_b == 'absent' else vcell(b, ctx.pct(b, kind))
    maps_html = []
    for role, key in ROLE_KEYS:
        state = _card_state(ctx, card, role)
        side_view = view.get(key)
        if state == 'absent':
            body = '<p class="muted mnote">이 제품에 없음</p>'
        elif side_view is None:
            body = '<p class="muted mnote">%s</p>' % ('이 제품에 이 쌍의 줄이 없습니다' if not [r for r in rows if r.get('side') == role]
                                                      else '이 제품의 지도에 줄이 보이지 않습니다')
        else:
            body = '<canvas class="cardmap" id="cv-%d-%s" width="1" height="1" hidden role="img" aria-label="%s 카드 %s 확대 지도"></canvas>' % (
                index, key, key, esc(name))
        maps_html.append('<div><h3>%s</h3>%s</div>' % ('기준 A (확대)' if key == 'A' else '대상 B (확대)', body))
    pairs_, same = _same_table(ctx, card, a_rows, by_id)
    matched_ids = {r['id'] for pair_ in pairs_ for r in pair_}
    return ''.join([
        '<details class="card" id="card-%s" data-card-pair="%s"%s><summary class="t"><span>%s %s<b>%s</b> · %s · %s · A %s → B %s · %s</span>'
        '<span><span class="badge%s">%s</span></span></summary>' % (
            esc(pair_id), esc(pair_id), ' open' if index == 0 else '', esc(rank or ''), chip(kind), esc(name), esc(kind),
            ctx.change_badge(card.get('change_kind')), head_a, head_b, dcell(d, ctx.pct(d, kind)),
            '' if status == 'matched' else ' b-trade', esc(STATUS_LABEL.get(status, status or '—'))),
        '<p class="muted"><a href="%s">4절 장부</a> · <a href="#s2" data-map-pair="%s">2절 지도에서 보기</a> · 점선 = 줄 범위(번호는 아래 표의 줄), '
        '빗금 = 비교 제외, 진한 색 = 이 쌍, 옅은 색 = 다른 쌍</p>' % (href, esc(pair_id)),
        '<div class="maps cardmaps">%s</div>' % ''.join(maps_html),
        '<h3>같은 좌표 비교 — A 줄을 04 정렬 이동량으로 B 에 옮긴 자리의 값</h3>', same,
        '<h3>있는 쪽 값만 — 대응하지 않은 줄 (제품을 밝혀 보입니다)</h3>', _only_table(ctx, card, rows, matched_ids),
        _not_listed(card),
        '<h3>합계</h3>', _totals_table(ctx, card), '</details>'])


def _section5(ctx):
    mdoc = ctx.maps['doc']
    top_cards = (mdoc.get('options') or {}).get('top_cards')
    out = ['<section id="s5"><p class="q">그 자리 주변은 어떤가</p><h2>5. 위치 상세</h2>',
           '<p class="lead">불량 종류별로 변화(|Δ|)가 큰 순서의 쌍마다 카드가 하나 있습니다%s. 카드는 그 쌍의 줄(점선 범위)과 범위 안 다른 쌍의 값을 '
           '보입니다. 서로 다른 카드의 범위는 겹칠 수 있어 카드끼리 더하지 않습니다.</p>' % (
               ' (종류별 상위 %d 쌍)' % top_cards if isinstance(top_cards, int) else '')]
    for band in mdoc.get('bands') or []:
        code = band.get('code') if isinstance(band, dict) else str(band)
        detail = band.get('detail') if isinstance(band, dict) else None
        out.append('<div class="band" data-band="%s">%s%s</div>' % (
            esc(code), esc(BAND_TEXT.get(code) or code), ' <span class="muted">(%s)</span>' % esc(detail) if detail else ''))
    out.append('<p class="muted">5절 값은 창 면적 × 밀도 근사인 07 위치 Δvi 와 다를 수 있습니다. 표시 구분: "대기" = 값 없음(계산 대기), '
               '"없음" = 그 제품에 없음, "0%" = 값이 0 입니다.</p>')
    if not ctx.cards:
        out.append('<p class="muted">카드가 없습니다. 위치를 볼 쌍이 없거나 ledger-maps 가 카드를 만들지 않았습니다.</p>')
    payload = {c['i']: c for c in _cards_payload(ctx)}
    for index, card in enumerate(ctx.cards):
        out.append(_card_html(ctx, index, card, payload[index]['sides']))
    out.append('</section>')
    return ''.join(out)


MAPS_CSS = """
[hidden]{display:none!important}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0;align-items:center}
.chips button{font:inherit;border:1px solid var(--border);background:var(--surface-1);color:var(--text-primary);border-radius:14px;padding:2px 10px;cursor:pointer}
.chips button.on{background:var(--text-primary);color:var(--surface-1)}
input#esearch{font:inherit;border:1px solid var(--border);background:var(--surface-1);color:var(--text-primary);border-radius:14px;padding:2px 10px;width:130px}
.maps{display:grid;grid-template-columns:1fr 1fr;gap:12px}.maps h3{margin-top:0}
canvas.map{display:block;width:100%;height:auto;background:var(--surface-0);border:1px solid var(--border);border-radius:6px;cursor:pointer}
.mnote{min-height:1.5em;margin:2px 0}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--text-secondary);font-size:12px;margin:8px 0}
.legend .sw{display:inline-block;width:24px;height:10px;vertical-align:middle;margin-right:4px;box-sizing:border-box}
.explain{border-left:3px solid var(--text-secondary);background:var(--surface-0);padding:8px 12px;margin-top:8px;min-height:60px}
table.mlist tr[data-sel-p]{cursor:pointer}table.mlist tr.hl td{background:var(--sel)}
tr[tabindex]:focus-visible{outline:2px solid var(--down);outline-offset:-2px}
#mtip{position:fixed;z-index:9;pointer-events:none;background:var(--surface-1);color:var(--text-primary);border:1px solid var(--border);
border-radius:6px;padding:3px 8px;font-size:12px;max-width:420px}
details.card{border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin-top:10px;background:var(--surface-1)}
details.card>summary{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-weight:650;font-size:14px;color:var(--text-primary);
list-style:none}details.card>summary::-webkit-details-marker{display:none}
details.card>summary::before{content:"▸";margin-right:6px;color:var(--text-muted)}details.card[open]>summary::before{content:"▾"}
canvas.cardmap{display:block;width:100%;height:auto;background:var(--surface-0);border:1px solid var(--border);border-radius:6px;image-rendering:pixelated}
body[data-map-check="failed"] canvas.cardmap{display:none}
details.card>summary .sub2{display:inline;margin-left:4px}
.card .maps{margin:8px 0}.card table{margin:2px 0 6px}.card td.rowid{font-weight:650}
@media print{.chips button:not(.on),#mclear,#esearch{display:none}.maps{break-inside:avoid}details.card{break-inside:auto}}
"""

MAPS_SCRIPT = r"""
(function(){
var D=JSON.parse(document.getElementById('maps-data').textContent);
function $(id){return document.getElementById(id);}
var R=['A','B'];
var st={mode:location.hash==='#size'?'size':'change',layer:'',sel:null};
if(location.hash==='#demo'&&D.demo_electrode){st.sel={t:'e',id:D.demo_electrode};}
var P={};D.pairs.forEach(function(p){P[p.id]=p;});
var S={};
R.forEach(function(r){var s=D.sides[r];S[r]={items:{},els:{},maps:[],cv:$('map'+r),comp:null,w:s.w,h:s.h};
 s.items.forEach(function(i){S[r].items[i.n]=i;});
 s.electrodes.forEach(function(e){S[r].els[e.number]=e.name;});});
var CC={};
function rgb(name,fb){var v=getComputedStyle(document.documentElement).getPropertyValue(name).trim();
 var m=/^#([0-9a-f]{6})$/i.exec(v)||/^#([0-9a-f]{6})$/i.exec(fb);var n=parseInt(m[1],16);return [(n>>16)&255,(n>>8)&255,n&255];}
function colors(){CC={bg:rgb('--surface-0','#f4f3f0'),mid:rgb('--mid','#e4e2dc'),common:rgb('--common','#8d8c85'),up:rgb('--up','#d03b3b'),
 down:rgb('--down','#2a78d6'),line:rgb('--line','#7a4fd6'),pixel:rgb('--pixel','#eb6834'),text:rgb('--text-primary','#0b0b0b')};}
var partnerCache={};
function partners(name){if(partnerCache[name])return partnerCache[name];var o={};
 D.pairs.forEach(function(p){if(p.e.indexOf(name)>=0){p.e.forEach(function(x){if(x!==name)o[x]=1;});}});partnerCache[name]=o;return o;}
function related(p){var s=st.sel;if(!s)return true;if(s.t==='p')return p.id===s.id;return p.e.indexOf(s.id)>=0;}
function elState(name){var s=st.sel;if(!s)return 0;
 if(s.t==='e'){if(name===s.id)return 1;if(partners(s.id)[name])return 2;return 3;}
 var p=P[s.id];if(p&&p.e.indexOf(name)>=0)return 1;return 3;}
function build(r){S[r].comp=mk(r,st.layer);}
function mk(r,layer,prio){var o=S[r],w=o.w,h=o.h,item=new Uint16Array(w*h),el=new Uint16Array(w*h),bb={};
 o.maps.forEach(function(m){if(layer&&m.meta.layer!==layer)return;var isItem=m.meta.kind==='it',dst=isItem?item:el,n=m.num;
  for(var i=0;i<n.length;i++){var v=n[i];
   if(v&&dst[i]&&isItem&&prio&&o.items[v]&&o.items[v].p===prio&&!(o.items[dst[i]]&&o.items[dst[i]].p===prio)){dst[i]=v;continue;}
   if(v&&!dst[i]){dst[i]=v;
   if(isItem){var x=i%w,y=(i/w)|0,b=bb[v];if(!b){bb[v]=[x,y,x,y];}else{if(x<b[0])b[0]=x;if(x>b[2])b[2]=x;if(y<b[1])b[1]=y;if(y>b[3])b[3]=y;}}}}});
 return {item:item,el:el,bb:bb};}
function style(r,n){var it=S[r].items[n];if(!it)return null;var p=P[it.p];if(!p)return null;
 var s={rel:related(p),fill:true,hatch:false,bold:false,dash:false,col:null,a:1};
 if(st.mode==='size'){var mx=D.max_pct[it.k]||0;var q=(it.pct==null||!mx)?0:Math.min(1,Math.abs(it.pct)/mx);
  s.col=it.pct==null?CC.common:CC[it.k];s.a=.18+.82*q;}
 else{var d=p.d;s.col=(d==null||d===0)?CC.common:(d>0?CC.up:CC.down);s.a=.85;
  if(p.ck==='layer_change'){s.hatch=true;}
  else if(p.ck==='new_pair'){s.bold=true;}
  else if(p.ck==='gone_pair'){s.fill=false;s.dash=true;s.col=CC.down;}}
 if(!s.rel)s.a*=.18;return s;}
function isBorder(a,w,h,x,y,n,t){if(x<t||y<t||x+t>=w||y+t>=h)return true;var i=y*w+x;
 return a[i-t]!==n||a[i+t]!==n||a[i-t*w]!==n||a[i+t*w]!==n;}
function draw(r){var o=S[r];if(!o.comp||!o.w)return;var w=o.w,h=o.h,cv=o.cv,g=cv.getContext('2d'),img=g.createImageData(w,h),d=img.data,c=o.comp;
 var i,x,y;
 for(i=0;i<w*h;i++){d[i*4]=CC.bg[0];d[i*4+1]=CC.bg[1];d[i*4+2]=CC.bg[2];d[i*4+3]=255;}
 function put(i,col,a){var q=i*4;d[q]=d[q]*(1-a)+col[0]*a;d[q+1]=d[q+1]*(1-a)+col[1]*a;d[q+2]=d[q+2]*(1-a)+col[2]*a;}
 var ecol={};
 for(i=0;i<w*h;i++){var e=c.el[i];if(!e)continue;var name=o.els[e],s=elState(name),k=ecol[e];
  if(!k){k=ecol[e]=s===1?{c:CC.common,a:1}:s===2?{c:[(CC.mid[0]+CC.common[0])>>1,(CC.mid[1]+CC.common[1])>>1,(CC.mid[2]+CC.common[2])>>1],a:1}:{c:CC.mid,a:s===3?.35:1};}
  put(i,k.c,k.a);}
 var sty={},tb=Math.max(1,Math.round(w/400)),per=Math.max(6,Math.round(w/80));
 for(i=0;i<w*h;i++){var n=c.item[i];if(!n)continue;var z=sty[n];if(z===undefined){z=sty[n]=style(r,n);}if(!z)continue;
  x=i%w;y=(i/w)|0;
  var needB=z.bold||z.dash||(st.sel&&z.rel),bd=false;
  if(needB){bd=isBorder(c.item,w,h,x,y,n,z.bold?tb*2:tb);}
  if(z.dash){if(bd&&((x+y)%(per)<per/2)){put(i,z.col,1);}continue;}
  if(bd&&(z.bold||(st.sel&&z.rel))){put(i,CC.text,1);continue;}
  if(z.hatch){if((x+y)%per<per/2){put(i,z.col,z.a);}continue;}
  if(z.fill){put(i,z.col,z.a);}}
 g.putImageData(img,0,0);
 var fs=Math.max(10,Math.round(w/55)),lab={};
 Object.keys(o.items).forEach(function(k){var it=o.items[k],p=P[it.p],b=c.bb[it.n];if(!p||!p.top||!b)return;var u=lab[p.id];
  if(!u){lab[p.id]=[b[0],b[1],b[2],b[3]];}else{if(b[0]<u[0])u[0]=b[0];if(b[1]<u[1])u[1]=b[1];}});
 g.font='bold '+fs+'px sans-serif';g.textBaseline='bottom';g.lineWidth=Math.max(2,fs/5);
 Object.keys(lab).forEach(function(id){var p=P[id],u=lab[id];g.globalAlpha=related(p)?1:.25;
  g.strokeStyle='rgb('+CC.bg.join(',')+')';g.fillStyle='rgb('+CC.text.join(',')+')';
  var ly=Math.max(fs+1,u[1]-2);g.strokeText(p.no,u[0],ly);g.fillText(p.no,u[0],ly);});
 g.globalAlpha=1;}
function drawAll(){colors();R.forEach(function(r){if(S[r].ok){build(r);draw(r);}});}
var cardComps={};
function cardComp(r,p){var k=r+'|'+p;if(!cardComps[k]){cardComps[k]=mk(r,'',p);}return cardComps[k];}
function drawCards(){if(!D.cards||!D.cards.length)return;colors();var count=0;
 D.cards.forEach(function(c){R.forEach(function(r){var s=c.sides[r];if(!s||!S[r].ok||!S[r].w)return;var cv=$('cv-'+c.i+'-'+r);if(!cv)return;
  var v=s.vb,z=s.z,W=(v[2]-v[0])*z,H=(v[3]-v[1])*z,o=S[r],comp=cardComp(r,c.p),w=o.w,own=P[c.p],dd=own?own.d:null;
  var col=(dd==null||dd===0)?CC.common:(dd>0?CC.up:CC.down);
  cv.width=W;cv.height=H;var g=cv.getContext('2d'),img=g.createImageData(W,H),d=img.data,dx,dy;
  for(dy=0;dy<H;dy++){var sy=v[1]+((dy/z)|0);
   for(dx=0;dx<W;dx++){var i=sy*w+v[0]+((dx/z)|0),q=(dy*W+dx)*4,rr=CC.bg[0],gg=CC.bg[1],bb=CC.bg[2];
    if(comp.el[i]){rr=CC.mid[0];gg=CC.mid[1];bb=CC.mid[2];}
    var n=comp.item[i];
    if(n&&o.items[n]){var mine=o.items[n].p===c.p,cl=mine?col:CC.common,a=mine?.85:.22;
     rr=rr*(1-a)+cl[0]*a;gg=gg*(1-a)+cl[1]*a;bb=bb*(1-a)+cl[2]*a;}
    d[q]=rr;d[q+1]=gg;d[q+2]=bb;d[q+3]=255;}}
  g.putImageData(img,0,0);
  var fs=Math.max(11,Math.round(W/36)),ink='rgb('+CC.text.join(',')+')',paper='rgb('+CC.bg.join(',')+')';
  s.rows.forEach(function(rw){var b=rw.b,x=(b[0]-v[0])*z,y=(b[1]-v[1])*z,ww=(b[2]-b[0])*z,hh=(b[3]-b[1])*z,k;
   if(rw.e==='excluded'){g.save();g.beginPath();g.rect(x,y,ww,hh);g.clip();g.strokeStyle=ink;g.globalAlpha=.55;g.lineWidth=1;
    for(k=-hh;k<ww;k+=8){g.beginPath();g.moveTo(x+k,y+hh);g.lineTo(x+k+hh,y);g.stroke();}g.restore();}
   g.save();g.setLineDash([6,4]);g.lineWidth=2;g.strokeStyle=ink;g.strokeRect(x,y,ww,hh);g.restore();
   var label=rw.id+(rw.e==='excluded'?' 비교 제외':(rw.e==='repeat_used'?(rw.n>0?' 안쪽 반복 타일 '+rw.n+'개 포함':' 안쪽 반복 사용'):''));
   g.font='bold '+fs+'px sans-serif';g.textBaseline='top';g.lineWidth=Math.max(2,fs/5);g.strokeStyle=paper;g.fillStyle=ink;
   var lx=Math.max(2,Math.min(W-4,x+3)),ly=Math.max(2,Math.min(H-fs-2,y+3));g.strokeText(label,lx,ly);g.fillText(label,lx,ly);});
  cv.hidden=false;count++;});});
 document.body.setAttribute('data-card-drawn',String(count));}
function openCard(id){var e=$(id);if(e&&e.tagName==='DETAILS'){e.open=true;}}
function txt(parent,text,bold){var n=document.createElement(bold?'b':'span');n.textContent=text;parent.appendChild(n);return n;}
function note(r){var s=st.sel;if(!s)return '';var k=r==='A'?'pa':'pb';
 if(s.t==='p'){var p=P[s.id];if(!p)return '';if(!p[k])return '이 제품에 없음';
  var its=Object.keys(S[r].items).map(function(n){return S[r].items[n];}).filter(function(i){return i.p===p.id;});
  if(!its.length||its.every(function(i){return i.s==='no_map_area';}))return '지도 영역 없음';return '';}
 var e=D.electrodes[s.id];if(e&&!e[r==='A'?0:1])return '이 제품에 없음';return '';}
function explain(){var box=$('explain');box.textContent='';var s=st.sel;
 if(!s){txt(box,'선택한 것이 없습니다. 지도의 전극이나 불량 영역, 아래 목록의 줄을 누르면 두 지도에서 함께 강조됩니다.').className='muted';return;}
 if(s.t==='p'){var p=P[s.id];if(!p)return;txt(box,p.no+'. '+p.name,true);
  txt(box,' · '+p.kind+' · '+p.cl+' · A '+p.ta+' → B '+p.tb+' ('+p.td+') · ');var a=document.createElement('a');a.href='#s4';a.textContent='4절 장부';box.appendChild(a);return;}
 txt(box,s.id,true);var ps=D.pairs.filter(function(p){return p.e.indexOf(s.id)>=0;});
 txt(box,' · 전극 · 이 전극이 든 쌍 '+ps.length+' 개: '+ps.slice(0,8).map(function(p){return p.no+' '+p.name;}).join(', ')+(ps.length>8?' …':''));}
function refresh(){drawAll();explain();
 R.forEach(function(r){$('note'+r).textContent=note(r);});
 var rows=document.querySelectorAll('[data-sel-p]');
 for(var i=0;i<rows.length;i++){var on=st.sel&&st.sel.t==='p'&&rows[i].getAttribute('data-sel-p')===st.sel.id;rows[i].className=on?'hl':'';}
 var b=document.querySelectorAll('#modes button');for(i=0;i<b.length;i++){b[i].className=b[i].getAttribute('data-mode')===st.mode?'on':'';}
 b=document.querySelectorAll('#mlayers button');for(i=0;i<b.length;i++){b[i].className=b[i].getAttribute('data-layer')===st.layer?'on':'';}
 $('legend-change').hidden=st.mode!=='change';$('legend-size').hidden=st.mode!=='size';}
function select(s){if(s&&st.sel&&st.sel.t===s.t&&st.sel.id===s.id)s=null;st.sel=s;refresh();}
function at(r,ev){var cv=S[r].cv,b=cv.getBoundingClientRect();
 return [Math.floor((ev.clientX-b.left)*cv.width/b.width),Math.floor((ev.clientY-b.top)*cv.height/b.height)];}
function hit(r,ev){var o=S[r];if(!o.comp)return null;var q=at(r,ev),x=q[0],y=q[1];if(x<0||y<0||x>=o.w||y>=o.h)return null;var i=y*o.w+x;
 var n=o.comp.item[i];if(n&&o.items[n]){var it=o.items[n],p=P[it.p];if(p)return {t:'p',id:p.id,text:p.no+'. '+p.name+' · '+it.k+' · '+it.l+' · '+p.cl+' · 이 제품 '+it.t+' · 쌍 Δ '+p.td};}
 var e=o.comp.el[i];if(e&&o.els[e])return {t:'e',id:o.els[e],text:o.els[e]};return null;}
function wire(r){var cv=S[r].cv,tip=$('mtip');
 cv.addEventListener('mousemove',function(ev){var h=hit(r,ev);if(!h){tip.hidden=true;return;}tip.textContent=h.text;tip.hidden=false;
  tip.style.left=(ev.clientX+12)+'px';tip.style.top=(ev.clientY+12)+'px';});
 cv.addEventListener('mouseleave',function(){tip.hidden=true;});
 cv.addEventListener('click',function(ev){var h=hit(r,ev);select(h?{t:h.t,id:h.id}:null);});}
function check(m,num){var cnt={},i,bad=0;
 for(i=0;i<num.length;i++){var v=num[i];if(v)cnt[v]=(cnt[v]||0)+1;}
 var want=m.meta.numbers,kc=Object.keys(cnt),kw=Object.keys(want);
 if(kc.length!==kw.length)return false;
 for(i=0;i<kw.length;i++){if(cnt[kw[i]]!==want[kw[i]])return false;}return true;}
function decode(r,meta,done){var im=new Image();
 im.onload=function(){var ok=false;try{var c=document.createElement('canvas');c.width=meta.w;c.height=meta.h;var g=c.getContext('2d');g.drawImage(im,0,0);
  var d=g.getImageData(0,0,meta.w,meta.h).data,num=new Uint16Array(meta.w*meta.h),blue=0;
  for(var i=0;i<num.length;i++){num[i]=d[i*4]|(d[i*4+1]<<8);if(d[i*4+2]!==0||d[i*4+3]!==255)blue++;}
  var m={meta:meta,num:num};S[r].maps.push(m);ok=blue===0&&check(m,num);}catch(e){ok=false;}done(ok);};
 im.onerror=function(){done(false);};im.src=meta.png;}
function start(){colors();var total=0,left=0,good=true;
 R.forEach(function(r){total+=D.sides[r].maps.length;});left=total;
 function fin(){var failed=!good;
  R.forEach(function(r){S[r].maps.sort(function(a,b){return a.meta.file<b.meta.file?-1:1;});S[r].ok=!failed&&S[r].w>0;
   S[r].cv.hidden=failed||S[r].w===0;if(S[r].w>0){S[r].cv.width=S[r].w;S[r].cv.height=S[r].h;}});
  $('mapwarn').hidden=!failed;document.body.setAttribute('data-map-check',failed?'failed':'passed');
  try{refresh();drawCards();document.body.setAttribute('data-map-drawn','1');}
  catch(e){$('mapwarn').hidden=false;document.body.setAttribute('data-map-check','failed');document.body.setAttribute('data-map-drawn','0');}}
 if(!total){good=true;fin();return;}
 R.forEach(function(r){D.sides[r].maps.forEach(function(meta){decode(r,meta,function(ok){if(!ok)good=false;left--;if(left===0)fin();});});});}
document.addEventListener('click',function(ev){var t=ev.target;
 var cl=t.closest?t.closest('a[href^="#card-"]'):null;if(cl){openCard(cl.getAttribute('href').slice(1));return;}
 if(t.getAttribute&&t.getAttribute('data-mode')){st.mode=t.getAttribute('data-mode');refresh();return;}
 if(t.getAttribute&&t.getAttribute('data-layer')!==null&&t.parentNode&&t.parentNode.id==='mlayers'){st.layer=t.getAttribute('data-layer');refresh();return;}
 if(t.id==='mclear'){st.sel=null;refresh();return;}
 var a=t.closest?t.closest('[data-map-pair]'):null;if(a){st.sel={t:'p',id:a.getAttribute('data-map-pair')};refresh();return;}
 var row=t.closest?t.closest('[data-sel-p]'):null;if(row){select({t:'p',id:row.getAttribute('data-sel-p')});}});
document.addEventListener('keydown',function(ev){if(ev.key==='Enter'){var row=ev.target.closest?ev.target.closest('[data-sel-p]'):null;if(row)select({t:'p',id:row.getAttribute('data-sel-p')});}});
$('esearch').addEventListener('change',function(){var v=this.value.trim();if(D.electrodes[v]){st.sel={t:'e',id:v};refresh();}});
R.forEach(wire);
window.addEventListener('beforeprint',function(){var ds=document.querySelectorAll('details.card');for(var i=0;i<ds.length;i++){ds[i].open=true;}drawAll();drawCards();});
window.addEventListener('hashchange',function(){if(location.hash.indexOf('#card-')===0){openCard(location.hash.slice(1));}});
if(location.hash.indexOf('#card-')===0){openCard(location.hash.slice(1));}
if(location.hash==='#print'){window.addEventListener('load',function(){drawAll();drawCards();});}
start();
})();
"""


# ---- document ---------------------------------------------------------------------------------------------------------

CSS = """
:root{color-scheme:light;--surface-0:#f4f3f0;--surface-1:#fcfcfb;--border:#e2e0da;--grid:#ebe9e4;
--text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#7a7972;
--line:#7a4fd6;--pixel:#eb6834;--up:#d03b3b;--down:#2a78d6;--mid:#e4e2dc;--common:#8d8c85;--sel:#fff3d6;
--badge-new:#fde7e7;--badge-gone:#e3eefc;--badge-both:#ecebe6;--trade:#f3ead0}
@media screen and (prefers-color-scheme:dark){:root{color-scheme:dark;--surface-0:#121211;--surface-1:#1a1a19;--border:#33332f;
--grid:#2a2a27;--text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#94938a;--line:#9a78ea;--pixel:#d95926;
--up:#e66767;--down:#3987e5;--mid:#3a3a36;--common:#77766f;--sel:#3a3018;--badge-new:#4a2323;--badge-gone:#1d2f4a;
--badge-both:#2c2c29;--trade:#2e2a1c}}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:44px}
body{margin:0;background:var(--surface-0);color:var(--text-primary);font:13px/1.5 -apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}
main{max-width:1240px;margin:0 auto;padding:16px 20px 60px}
header{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap}
h1{font-size:20px;margin:0}h2{font-size:16px;margin:0 0 2px}h3{font-size:13px;margin:12px 0 4px;color:var(--text-secondary)}
section{background:var(--surface-1);border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-top:14px}
.q{color:var(--text-secondary);font-weight:600;margin:0 0 2px}.muted,.lead{color:var(--text-muted)}.lead{margin:0 0 8px}
#bar{position:sticky;top:0;z-index:5;background:var(--surface-1);border:1px solid var(--border);border-radius:0 0 8px 8px;padding:5px 12px;display:flex;gap:14px;flex-wrap:wrap}
a{color:var(--down)}
.band{border:1px solid var(--border);border-left:4px solid var(--pixel);background:var(--trade);border-radius:6px;padding:8px 12px;margin:12px 0}
.band ul{margin:4px 0 0 18px;padding:0}
table{border-collapse:collapse;width:100%}
th,td{padding:4px 8px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;vertical-align:top}
th{font-weight:600;color:var(--text-secondary);font-size:12px;border-bottom:1px solid var(--border)}
.l{text-align:left}.wrap{white-space:normal}
tr.sub td{color:var(--text-secondary);font-size:12px;background:var(--surface-0)}
tr.sum td{font-weight:650;border-top:1px solid var(--border)}tr.rest td{font-style:italic}
tr.grp td{background:var(--surface-0);border-top:1px solid var(--border)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.badge{display:inline-block;padding:0 6px;border-radius:9px;font-size:11px;background:var(--badge-both);color:var(--text-secondary)}
.b-new{background:var(--badge-new)}.b-gone{background:var(--badge-gone)}.b-trade{background:var(--trade)}
.sub2{display:block;font-size:11px;color:var(--text-muted);font-weight:400}
.chip{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
.sg{font-weight:600}.s-up::before{content:"▲ ";color:var(--up);font-size:10px}.s-down::before{content:"▼ ";color:var(--down);font-size:10px}
.key{display:grid;grid-template-columns:1fr 1.25fr 1fr 1.35fr;gap:10px;margin:10px 0}
.tile{display:block;border:1px solid var(--border);border-radius:8px;padding:8px 10px;background:var(--surface-0)}
.tile .k{color:var(--text-muted);font-size:12px}.kl{font-size:14px;margin-top:2px}
.hm td{text-align:center;min-width:70px}.scroll{overflow-x:auto}
details{margin:4px 0}summary{cursor:pointer;color:var(--text-secondary)}
details table{margin-top:4px}
.placeholder{font-size:14px}
@media print{*{-webkit-print-color-adjust:exact;print-color-adjust:exact}
#bar{position:static}section{break-inside:auto}thead{display:table-header-group}tr{break-inside:avoid}
body{font-size:11px}main{max-width:none;padding:0}.scroll{overflow:visible}}
"""

SCRIPT = ("(function(){function openAll(){var d=document.querySelectorAll('details');for(var i=0;i<d.length;i++){d[i].open=true;}}"
          "window.addEventListener('beforeprint',openAll);if(location.hash==='#print'){openAll();}})();")


def _json_embed(value):
    """JSON for a <script type="application/json"> block: no `<` is left, so `</script>` (or `<!--`) can never appear."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True).replace('<', '\\u003c')


def render_html(doc, ledger_sha, top_pairs, top_electrodes, maps=None):
    ctx = Ctx(doc, top_pairs, top_electrodes)
    ctx.maps = maps
    if maps is not None:
        _init_cards(ctx)
    products = doc['inputs']['products']
    embed = {'schema_version': SCHEMA_VERSION, 'ledger_json_sha256': ledger_sha, 'ledger_status': doc.get('status'),
             'options': {'top_pairs': top_pairs, 'top_electrodes': top_electrodes},
             'totals': doc['totals'],
             'shown': {'pairs': {kind: [p['pair_id'] for p in doc['pairs'] if p['kind'] == kind][:top_pairs] for kind in KINDS},
                       'electrodes': {kind: [r['electrode'] for r in _sorted_electrodes(ctx, kind)][:top_electrodes] for kind in KINDS}}}
    nav = ''.join('<a href="#s%d">%d</a>' % (i, i) for i in range(1, 8))
    if maps is not None:
        return _render_with_maps(ctx, embed, nav, products, top_pairs, top_electrodes)
    body = [
        '<!doctype html>\n<html lang="ko"><head><meta charset="utf-8"><title>설계 변경 영향 보고서 (장부)</title><style>%s</style></head><body><main>' % CSS,
        '<header><div><h1>설계 변경 영향 보고서 — 장부</h1><div class="muted">기준 A: %s · 대상 B: %s · ledger %s</div></div>'
        '<div class="muted">상위 쌍 %d · 상위 전극 %d</div></header>' % (
            esc(products['reference'].get('model_group')), esc(products['target'].get('model_group')), esc(doc.get('status')),
            top_pairs, top_electrodes),
        _head_band(ctx), '<div id="bar">%s</div>' % nav,
        _section1(ctx), _placeholder(2, 'layout 리스크 지도'), _section3(ctx), _section4(ctx), _placeholder(5, '위치 상세'),
        _section6(ctx), _section7(ctx),
        '</main><script type="application/json" id="report-data">%s</script><script>%s</script></body></html>\n' % (_json_embed(embed), SCRIPT)]
    return ''.join(body)


def _render_with_maps(ctx, embed, nav, products, top_pairs, top_electrodes):
    """The same page with section 2 filled from the ledger-maps folder (sections 1, 3, 4, 6, 7 as without maps, plus
    the 'show on map' and 'location' links in section 4) and section 5 filled with the location cards."""
    doc = ctx.doc
    body = [
        '<!doctype html>\n<html lang="ko"><head><meta charset="utf-8"><title>설계 변경 영향 보고서 (장부)</title><style>%s%s</style></head><body><main>' % (CSS, MAPS_CSS),
        '<header><div><h1>설계 변경 영향 보고서 — 장부</h1><div class="muted">기준 A: %s · 대상 B: %s · ledger %s</div></div>'
        '<div class="muted">상위 쌍 %d · 상위 전극 %d</div></header>' % (
            esc(products['reference'].get('model_group')), esc(products['target'].get('model_group')), esc(doc.get('status')),
            top_pairs, top_electrodes),
        _head_band(ctx), _maps_band(ctx), '<div id="bar">%s</div>' % nav,
        _section1(ctx), _section2(ctx), _section3(ctx), _section4(ctx), _section5(ctx),
        _section6(ctx), _section7(ctx),
        '<div id="mtip" hidden></div></main><script type="application/json" id="report-data">%s</script>'
        '<script type="application/json" id="maps-data">%s</script><script>%s</script><script>%s</script></body></html>\n' % (
            _json_embed(embed), _json_embed(_maps_payload(ctx)), SCRIPT, MAPS_SCRIPT)]
    return ''.join(body)


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


def run_ledger_report(ledger_dir, out, top_pairs=DEFAULT_TOP, top_electrodes=DEFAULT_TOP, maps_dir=None):
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
    page = render_html(doc, sha, top_pairs, top_electrodes) if maps is None else render_html(doc, sha, top_pairs, top_electrodes, maps=maps)
    status = 'partial' if doc.get('status') == 'partial' else 'ok'
    reasons = list(doc.get('reasons') or []) if status == 'partial' else []
    exit_code = 0 if status == 'ok' else 3
    report = {'command': COMMAND, 'schema_version': SCHEMA_VERSION, 'status': status, 'reasons': reasons,
              'ledger_json_sha256': sha, 'ledger_status': doc.get('status'),
              'ledger_execution': None if execution is None else {'status': execution.get('status'),
                                                                    'exit_code': execution.get('exit_code')},
              'options': {'top_pairs': top_pairs, 'top_electrodes': top_electrodes},
              'sections': {'present': [1, 3, 4, 6, 7], 'placeholders': [2, 5]},
              'html_bytes': len(page.encode('utf-8'))}
    if maps is not None:
        report['ledger_maps_json_sha256'] = maps['sha']
        report['ledger_maps_status'] = maps['doc'].get('status')
        report['sections'] = {'present': [1, 2, 3, 4, 5, 6, 7], 'placeholders': []}
    record = {'command': COMMAND, 'status': status, 'reasons': reasons,
              'outputs': [str(output / name) for name in (HTML_NAME, 'report.json')],
              'ledger_dir': str(ledger_dir), 'options': report['options'],
              'started_at': datetime.fromtimestamp(started, timezone.utc).isoformat(),
              'finished_at': datetime.now(timezone.utc).isoformat(),
              'elapsed_seconds': round(time.time() - started, 3), 'exit_code': exit_code}
    if maps is not None:
        record['maps_dir'] = str(maps_dir)
    _publish(output, [(HTML_NAME, page), ('report.json', _json_text(report)), ('execution.json', _json_text(record))])
    error = output / 'error.json'
    if error.exists():
        error.unlink()
    return record


def main_ledger_report(ledger_dir, out, print_result, top_pairs=DEFAULT_TOP, top_electrodes=DEFAULT_TOP, error_stream=None,
                       maps_dir=None):
    """CLI body: prints the record, or leaves error.json + execution.json only and returns 2."""
    error_stream = error_stream or sys.stderr
    output = Path(out)
    try:
        record = run_ledger_report(ledger_dir, out, top_pairs=top_pairs, top_electrodes=top_electrodes, maps_dir=maps_dir)
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
