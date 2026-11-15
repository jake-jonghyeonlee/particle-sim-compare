"""ledger-report: the single-file HTML report of one `ledger` run (`ledger_v1` -> `ledger_report.html`).

Input: `ledger.json` (+ the `execution.json` of the `ledger` run). Nothing is recalculated: every number is a `ledger.json`
value. The report only converts formats (% of the reference vi, %p), and adds up the values of the rows it folds into an
"other k" row (docs/plan/ledger_report/README.md sections 11-13). Sections 2 and 5 are placeholders in this stage.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
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
        cands = [(v['inc'], code) for (k, code), v in sums.items() if k == kind and code != 'no_change' and v['inc']]
        if not cands:
            t2.append(line(kind, '증가 없음'))
            continue
        inc, code = max(cands, key=lambda c: (c[0], c[1]))
        t2.append(line(kind, '<a href="#s1"><b>%s</b></a> 증가 %s' % (esc(ctx.change_labels.get(code, code)),
                                                                  dcell(inc, ctx.pct(inc, kind)))))
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
            out.append('<details><summary>%s — 이 종류의 쌍(CNO)이 없는 전극 %d 개</summary><p class="wrap">%s</p></details>' % (
                '해당 없음', len(na), esc(', '.join(na))))
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
    return ('<tr%s %s><td>%s</td><td class="l"><b>%s</b></td><td class="l">%s</td><td class="l">%s</td><td class="l">%s</td>'
            '<td>%s</td><td>%s</td><td class="l">%s</td><td class="l wrap">%s</td></tr>' % (
                anchor, _data_attrs(row_kind, pair['a'], pair['b'], pair['delta']), esc(pair['rank']), esc(name),
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


def render_html(doc, ledger_sha, top_pairs, top_electrodes):
    ctx = Ctx(doc, top_pairs, top_electrodes)
    products = doc['inputs']['products']
    embed = {'schema_version': SCHEMA_VERSION, 'ledger_json_sha256': ledger_sha, 'ledger_status': doc.get('status'),
             'options': {'top_pairs': top_pairs, 'top_electrodes': top_electrodes},
             'totals': doc['totals'],
             'shown': {'pairs': {kind: [p['pair_id'] for p in doc['pairs'] if p['kind'] == kind][:top_pairs] for kind in KINDS},
                       'electrodes': {kind: [r['electrode'] for r in _sorted_electrodes(ctx, kind)][:top_electrodes] for kind in KINDS}}}
    nav = ''.join('<a href="#s%d">%d</a>' % (i, i) for i in range(1, 8))
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


def run_ledger_report(ledger_dir, out, top_pairs=DEFAULT_TOP, top_electrodes=DEFAULT_TOP):
    """Build and publish the report; returns the execution record (exit_code 0, or 3 for a partial ledger) or raises."""
    started = time.time()
    output = Path(out)
    if not _owned_output(output):
        raise NumericContractError('ledger_report_output_not_owned', f'{output}: use a new folder or a previous ledger-report folder')
    if (isinstance(top_pairs, bool) or isinstance(top_electrodes, bool) or not isinstance(top_pairs, int)
            or not isinstance(top_electrodes, int) or top_pairs < 1 or top_electrodes < 1):
        raise NumericContractError('ledger_report_option_invalid', '--top-pairs 와 --top-electrodes 는 1 이상의 정수여야 합니다')
    doc, sha, execution = _read_ledger(ledger_dir)
    page = render_html(doc, sha, top_pairs, top_electrodes)
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
    record = {'command': COMMAND, 'status': status, 'reasons': reasons,
              'outputs': [str(output / name) for name in (HTML_NAME, 'report.json')],
              'ledger_dir': str(ledger_dir), 'options': report['options'],
              'started_at': datetime.fromtimestamp(started, timezone.utc).isoformat(),
              'finished_at': datetime.now(timezone.utc).isoformat(),
              'elapsed_seconds': round(time.time() - started, 3), 'exit_code': exit_code}
    _publish(output, [(HTML_NAME, page), ('report.json', _json_text(report)), ('execution.json', _json_text(record))])
    error = output / 'error.json'
    if error.exists():
        error.unlink()
    return record


def main_ledger_report(ledger_dir, out, print_result, top_pairs=DEFAULT_TOP, top_electrodes=DEFAULT_TOP, error_stream=None):
    """CLI body: prints the record, or leaves error.json + execution.json only and returns 2."""
    error_stream = error_stream or sys.stderr
    output = Path(out)
    try:
        record = run_ledger_report(ledger_dir, out, top_pairs=top_pairs, top_electrodes=top_electrodes)
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
