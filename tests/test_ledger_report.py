"""ledger/T5: the ledger-report command (single-file HTML of ledger.json, sections 1, 3, 4, 6, 7; 2 and 5 are placeholders).

test_ledger.py / test_ledger_integration.py cover the ledger itself. These tests build `ledger_v1` documents by hand (a
consistent builder: totals are the sums of the rows) and one from the real compare2 -> ledger chain."""
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from simcompare import ledger_report
from simcompare.ledger import CHANGE_KINDS, STATUS_LABELS

KINDS = ('line', 'pixel')
SRC = str(Path(__file__).resolve().parents[1] / 'src')


# ---- a consistent ledger_v1 builder -----------------------------------------------------------------------------------

def pct_of(value, vi_a):
    return None if value is None or not vi_a else 100.0 * value / vi_a


def spec(pair_id, electrodes, kind, a, b, present_a=True, present_b=True, change='same_pair_change', layer='L1',
         filtered=(False, False), failure='lateral_short', lower=None):
    return dict(pair_id=pair_id, electrodes=list(electrodes), kind=kind, a=a, b=b, present_a=present_a,
                present_b=present_b, change=change, layer=layer, filtered=filtered, failure=failure, lower=lower or layer)


def make_ledger(pairs, cno=(), extra_electrodes=(), status='ok', reasons=(), vi=None, sizes=(1.0, 2.0), complete=True,
                crp=None, intercept=0.01):
    """pairs: spec() list. cno: [(electrode, density_layer, a, b)]. Returns a ledger_v1 dict whose totals are the row sums."""
    vi = vi or {}
    items_sum, cno_sum, pair_rows = {}, {}, []
    for kind in KINDS:
        rows = [p for p in pairs if p['kind'] == kind]
        # like the real ledger (ledger.py): one null part makes the sum null
        items_sum[kind] = {f: (None if any(p[f] is None for p in rows) else math.fsum(p[f] for p in rows)) for f in ('a', 'b')}
        items_sum[kind]['delta'] = (None if any(p['a'] is None or p['b'] is None for p in rows)
                                    else math.fsum(p['b'] - p['a'] for p in rows))
    cno_sum = {'a': math.fsum(c[2] for c in cno), 'b': math.fsum(c[3] for c in cno)}
    cno_sum['delta'] = cno_sum['b'] - cno_sum['a']
    crp_d = crp or 0.0
    totals, kinds = {}, {}
    for kind in KINDS:
        it = items_sum[kind]
        a = None if it['a'] is None else it['a'] + intercept + (cno_sum['a'] - crp_d if kind == 'pixel' else 0.0)
        b = None if it['b'] is None else it['b'] + intercept + (cno_sum['b'] if kind == 'pixel' else 0.0)
        d = None if it['delta'] is None else it['delta'] + ((cno_sum['delta'] + crp_d) if kind == 'pixel' else 0.0)
        totals[kind] = {'items': it, 'intercept': {'a': intercept, 'b': intercept, 'delta': 0.0}, 'a': a, 'b': b,
                        'delta': d, 'vi_a': a, 'vi_b': b}
        if kind == 'pixel':
            totals[kind]['cno'] = dict(cno_sum, pct_delta=pct_of(cno_sum['delta'], a))
            totals[kind]['crp'] = {'present': crp is not None, 'a': -crp_d, 'b': 0.0, 'delta': crp_d}
        if vi.get(kind) == 'none':
            totals[kind]['a'] = totals[kind]['b'] = totals[kind]['delta'] = None
    vi_a = {k: totals[k]['vi_a'] for k in KINDS}
    for index, kind in enumerate(KINDS):
        rows = sorted((p for p in pairs if p['kind'] == kind), key=lambda p: (-(p['b'] if p['present_b'] and p['b'] is not None else (p['a'] or 0)), p['pair_id']))
        for number, p in enumerate(rows, 1):
            p['rank'] = ('L' if kind == 'line' else 'P') + str(number)
    order = {'line': 0, 'pixel': 1}
    out_pairs = []
    for p in sorted(pairs, key=lambda p: (order.get(p['kind'], 2), p.get('rank') or 'Z')):
        va = vi_a.get(p['kind'])
        d = None if p['a'] is None or p['b'] is None else p['b'] - p['a']
        item = {'item_id': json.dumps([p['pair_id'], p['failure'], p['layer'], p['lower']], ensure_ascii=False),
                'failure_type': p['failure'], 'analysis_layer': p['layer'], 'upper_layer': p['layer'], 'lower_layer': p['lower'],
                'endpoints': [{'electrode_id': e, 'actual_layer': p['layer']} for e in p['electrodes']], 'class': p['kind'],
                'presence': 'both' if p['present_a'] and p['present_b'] else ('new' if p['present_b'] else 'gone'),
                'filtered_out': all(p['filtered']) if (p['present_a'] and p['present_b']) else (p['filtered'][1] if p['present_b'] else p['filtered'][0]),
                'a_filtered': bool(p['filtered'][0]), 'b_filtered': bool(p['filtered'][1]),
                'lambda_a': 1.0, 'lambda_b': 2.0, 'a': p['a'], 'b': p['b'], 'delta': d}
        out_pairs.append({'pair_id': p['pair_id'], 'electrodes': p['electrodes'], 'kind': p['kind'], 'present_a': p['present_a'],
                          'present_b': p['present_b'], 'computable_a': p['a'] is not None, 'computable_b': p['b'] is not None,
                          'a': p['a'], 'b': p['b'], 'delta': d,
                          'layers_a': [p['layer']] if p['present_a'] else [], 'layers_b': [p['layer']] if p['present_b'] else [],
                          'items': [item], 'change_kind': p['change'], 'pct_a': pct_of(p['a'], va), 'pct_b': pct_of(p['b'], va),
                          'pct_delta': pct_of(d, va), 'rank': p.get('rank')})
    names = sorted({e for p in pairs for e in p['electrodes']} | {c[0] for c in cno} | set(extra_electrodes))
    el_rows, el_layers = [], {}
    for name in names:
        acc = {k: {'a': 0.0, 'b': 0.0} for k in ('line', 'pixel', 'cno')}
        layers = {}
        partners = set()
        for p in pairs:
            if name in p['electrodes'] and p['kind'] in KINDS:
                for f in ('a', 'b'):
                    acc[p['kind']][f] += (p[f] or 0.0) / len(p['electrodes'])
                lay = layers.setdefault(p['layer'], {k: {'a': 0.0, 'b': 0.0, 'delta': 0.0} for k in KINDS})
                lay[p['kind']]['a'] += (p['a'] or 0.0) / len(p['electrodes'])
                lay[p['kind']]['b'] += (p['b'] or 0.0) / len(p['electrodes'])
                lay[p['kind']]['delta'] = lay[p['kind']]['b'] - lay[p['kind']]['a']
                partners.update(e for e in p['electrodes'] if e != name)
        for c in cno:
            if c[0] == name:
                acc['cno']['a'] += c[2]
                acc['cno']['b'] += c[3]
                acc['pixel']['a'] += c[2]
                acc['pixel']['b'] += c[3]
                lay = layers.setdefault(c[1], {k: {'a': 0.0, 'b': 0.0, 'delta': 0.0} for k in KINDS})
                lay['pixel']['a'] += c[2]
                lay['pixel']['b'] += c[3]
                lay['pixel']['delta'] = lay['pixel']['b'] - lay['pixel']['a']
        row = {'electrode': name, 'status': 'common' if complete else 'unverifiable', 'present_a': True, 'present_b': True,
               'layers_a': sorted(layers), 'layers_b': sorted(layers), 'cno': dict(acc['cno'], delta=acc['cno']['b'] - acc['cno']['a']),
               'partners_a': sorted(partners), 'partners_b': sorted(partners), 'layers': layers}
        for kind in KINDS:
            a, b = acc[kind]['a'], acc[kind]['b']
            row[kind] = {'a': a, 'b': b, 'delta': b - a, 'pct_a': pct_of(a, vi_a[kind]), 'pct_b': pct_of(b, vi_a[kind]),
                         'pct_delta': pct_of(b - a, vi_a[kind])}
        el_rows.append(row)
    layer_rows = {'line': [], 'pixel': [], 'cno': []}
    for kind in KINDS:
        by_layer = {}
        for p in pairs:
            if p['kind'] == kind:
                by_layer.setdefault(p['layer'], []).append(p)
        for layer, rows in sorted(by_layer.items()):
            a, b = math.fsum(p['a'] or 0.0 for p in rows), math.fsum(p['b'] or 0.0 for p in rows)
            by_size = [{'diameter_um': s, 'a': a / len(sizes), 'b': b / len(sizes), 'delta': (b - a) / len(sizes)} for s in sizes]
            layer_rows[kind].append({'layer': layer, 'a': a, 'b': b, 'delta': b - a, 'by_size': by_size,
                                     'top_pairs': [{'pair_id': rows[0]['pair_id'], 'electrodes': rows[0]['electrodes'], 'delta': (rows[0]['b'] or 0) - (rows[0]['a'] or 0)}]})
    for layer in sorted({c[1] for c in cno}):
        rows = [c for c in cno if c[1] == layer]
        a, b = math.fsum(c[2] for c in rows), math.fsum(c[3] for c in rows)
        layer_rows['cno'].append({'layer': layer, 'a': a, 'b': b, 'delta': b - a,
                                  'by_size': [{'diameter_um': 1.0, 'a': a, 'b': b, 'delta': b - a}], 'top_pairs': []})
    cno_rows = [{'electrode_id': c[0], 'density_layer': c[1], 'change_kind': 'cno', 'presence': 'both', 'lambda_a': 1.0,
                 'lambda_b': 2.0, 'a': c[2], 'b': c[3], 'delta': c[3] - c[2], 'pct_a': pct_of(c[2], vi_a['pixel']),
                 'pct_b': pct_of(c[3], vi_a['pixel']), 'pct_delta': pct_of(c[3] - c[2], vi_a['pixel'])} for c in cno]
    counts = {code: 0 for code, _ in CHANGE_KINDS}
    for p in pairs:
        counts[p['change']] += 1
    counts['cno'] = len(cno)
    for kind in KINDS:
        rows = [p for p in pairs if p['kind'] == kind]
        known = [(p['b'] or 0.0) - (p['a'] or 0.0) for p in rows if p['a'] is not None and p['b'] is not None]
        kinds[kind] = {'computable': vi.get(kind) != 'none', 'vi_a': totals[kind]['vi_a'], 'vi_b': totals[kind]['vi_b'],
                       'slope': 1.0, 'slope_method': 'secant', 'lambda_a': 1.0, 'lambda_b': 2.0, 'pair_count': len(rows),
                       'change_kinds': {code: sum(1 for p in rows if p['change'] == code) for code, _ in CHANGE_KINDS},
                       'increase_sum': math.fsum(v for v in known if v > 0), 'decrease_sum': math.fsum(v for v in known if v < 0),
                       'value_pending_rows': 0}
    kinds['change_kind_counts'] = counts
    inventory = {role: {'electrodes': names, 'layers': {}, 'source': 'dl1' if complete else 'observed',
                        'status': 'complete' if complete else 'incomplete', 'sha256': 'f' * 64} for role in ('reference', 'target')}
    return {'schema_version': 'ledger_v1', 'status': status, 'reasons': list(reasons),
            'change_kinds': [{'code': c, 'label': l} for c, l in CHANGE_KINDS],
            'electrode_status': [{'code': c, 'label': l} for c, l in STATUS_LABELS],
            'inputs': {'compare2_json_sha256': 'a' * 64, 'config_sha256': 'b' * 64, 'ledger_basis': 'pair_share_v1',
                       'products': {'reference': {'model_group': 'mobile', 'pair_table_hash': 'h' * 8, 'crp': 1.0},
                                    'target': {'model_group': 'auto', 'pair_table_hash': 'h' * 8, 'crp': 1.0}},
                       'electrode_inventory': inventory},
            'totals': totals, 'kinds': kinds, 'pairs': out_pairs, 'electrodes': el_rows, 'cno': cno_rows, 'layers': layer_rows,
            'checks': {'status': 'passed' if status == 'ok' else 'failed',
                       'equations': {'eq1_line_a': {'status': 'passed' if status == 'ok' else 'failed'}}},
            'warnings': [], 'unavailable': []}


def write_ledger(folder, doc, execution=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'ledger.json').write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    if execution is not False:
        (folder / 'execution.json').write_text(json.dumps(execution or {'command': 'ledger', 'status': doc['status'], 'exit_code': 0}),
                                               encoding='utf-8')
    return folder


def run_report(tmp_path, doc, name='out', top_pairs=30, top_electrodes=30, **kwargs):
    ledger_dir = write_ledger(tmp_path / ('ledger_' + name), doc, **kwargs)
    out = tmp_path / name
    code = ledger_report.main_ledger_report(str(ledger_dir), str(out), lambda record: None,
                                            top_pairs=top_pairs, top_electrodes=top_electrodes)
    return code, out


def html_of(out):
    return (out / 'ledger_report.html').read_text(encoding='utf-8')


def sample_doc():
    pairs = [spec('P1', ['Data', 'ELVDD'], 'line', 0.0120, 0.0150), spec('P2', ['Scan1', 'EM'], 'line', 0.0060, 0.0078, change='layer_change'),
             spec('P3', ['Anode', 'Vint'], 'pixel', 0.0200, 0.0200, change='no_change', layer='PXL'),
             spec('P4', ['Gnode', 'Anode'], 'pixel', 0.0090, 0.0050, layer='SD1', failure='vertical_short', lower='PXL'),
             spec('P5', ['Scan2', 'Vint'], 'line', 0.0, 0.0065, present_a=False, change='new_pair', layer='SD1')]
    return make_ledger(pairs, cno=[('Anode', 'PXL', 0.0050, 0.0060)])


def rows_between(page, start, end):
    """(row kind, a, b, d) of every data-row tag between two markers."""
    a = page.index(start)
    z = page.index(end, a + len(start)) if end else len(page)
    return [(m.group(1), m.group(2), m.group(3), m.group(4)) for m in
            re.finditer(r'data-row="(\w+)" data-a="([^"]*)" data-b="([^"]*)" data-d="([^"]*)"', page[a:z])]


def num_or_none(text):
    return None if text == '' else float(text)


# ---- tests -----------------------------------------------------------------------------------------------------------

def test_html_has_no_external_reference_and_all_files(tmp_path):
    code, out = run_report(tmp_path, sample_doc())
    assert code == 0
    page = html_of(out)
    assert sorted(p.name for p in out.iterdir()) == ['execution.json', 'ledger_report.html', 'report.json']
    for needle in ('http://', 'https://', '<link', ' src=', 'url(', '@import'):
        assert needle not in page, needle
    report = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert len(report['ledger_json_sha256']) == 64 and report['options'] == {'top_pairs': 30, 'top_electrodes': 30}
    assert report['ledger_execution']['status'] == 'ok'
    assert json.loads((out / 'execution.json').read_text(encoding='utf-8'))['exit_code'] == 0


def test_seven_section_titles_in_order_with_placeholders(tmp_path):
    code, out = run_report(tmp_path, sample_doc())
    page = html_of(out)
    titles = ['1. 전체 비교', '2. layout 리스크 지도', '3. 전극별', '4. 전극 쌍별', '5. 위치 상세', '6. 레이어 · 이물 크기', '7. 참고']
    positions = [page.index('<h2>%s</h2>' % t) for t in titles]
    assert positions == sorted(positions)
    for number in (2, 5):
        section = page[page.index('<section id="s%d">' % number):]
        section = section[:section.index('</section>')]
        assert '다음 단계에서 추가됩니다' in section and '<table' not in section
    assert page.count('다음 단계에서 추가됩니다') == 2


@pytest.mark.parametrize('top', [1, 2, 1000])
def test_total_rows_equal_ledger_totals_and_top_plus_rest_equals_total(tmp_path, top):
    doc = sample_doc()
    code, out = run_report(tmp_path, doc, name='t%d' % top, top_pairs=top, top_electrodes=top)
    page = html_of(out)
    for kind, nxt in (('line', '<table id="pairs-pixel"'), ('pixel', '<table id="cno"')):
        rows = rows_between(page, '<table id="pairs-%s"' % kind, nxt)
        total = [r for r in rows if r[0] == 'total']
        assert len(total) == 1
        expect = doc['totals'][kind]['items']
        assert [float(x) for x in total[0][1:]] == [expect['a'], expect['b'], expect['delta']]
        shown = [r for r in rows if r[0] == 'top']
        rest = [r for r in rows if r[0] == 'rest']
        assert len(shown) == min(top, sum(1 for p in doc['pairs'] if p['kind'] == kind))
        for index in (1, 2, 3):
            parts = [float(r[index]) for r in shown + rest]
            assert math.fsum(parts) == pytest.approx(float(total[0][index]), abs=1e-12)
        # the "other" row holds exactly the rest of the pairs
        n_rest = sum(1 for p in doc['pairs'] if p['kind'] == kind) - len(shown)
        assert (len(rest) == 1) == (n_rest > 0)
        if n_rest:
            assert '기타 %d 쌍' % n_rest in page
    # electrode tables: top + rest = total (pair totals + CNO for pixel)
    for kind, nxt in (('line', '<table id="el-pixel"'), ('pixel', '<h2>4.')):
        rows = rows_between(page, '<table id="el-%s"' % kind, nxt)
        total = [r for r in rows if r[0] == 'total'][0]
        parts = [r for r in rows if r[0] in ('top', 'rest')]
        for index in (1, 2, 3):
            assert math.fsum(float(r[index]) for r in parts) == pytest.approx(float(total[index]), abs=1e-12)
        t = doc['totals'][kind]
        extra = t['cno']['a'] if kind == 'pixel' else 0.0
        assert float(total[1]) == pytest.approx(t['items']['a'] + extra, abs=1e-12)
    # CNO table
    cno_rows = rows_between(page, '<table id="cno"', '<h3>전극 쌍 × 레이어')
    assert [float(x) for x in [r for r in cno_rows if r[0] == 'total'][0][1:]] == [
        doc['totals']['pixel']['cno'][f] for f in ('a', 'b', 'delta')]


def test_rest_rows_hold_the_remaining_pairs_in_a_details_block(tmp_path):
    code, out = run_report(tmp_path, sample_doc(), top_pairs=1, top_electrodes=1)
    page = html_of(out)
    assert '기타 2 쌍 전체 보기' in page and '기타' in page
    assert page.count('data-row="restitem"') >= 4


def test_pending_row_outside_the_top_n_makes_the_other_row_and_total_pending(tmp_path):
    pairs = [spec('P1', ['A', 'B'], 'line', 0.0120, 0.0150), spec('P2', ['B', 'C'], 'line', 0.0060, 0.0078),
             spec('W1', ['W', 'X'], 'line', None, 0.0001, change='value_pending')]
    code, out = run_report(tmp_path, make_ledger(pairs), top_pairs=1, top_electrodes=1000)
    page = html_of(out)
    rows = rows_between(page, '<table id="pairs-line"', '<table id="pairs-pixel"' if '<table id="pairs-pixel"' in page else None)
    rest = [r for r in rows if r[0] == 'rest'][0]
    total = [r for r in rows if r[0] == 'total'][0]
    assert rest[1] == '' and rest[3] == '' and total[1] == '' and total[3] == ''   # A and Δ wait; B is known
    line = page[page.index('<table id="pairs-line"'):]
    rest_tr = line[line.index('<tr class="rest"'):]
    rest_tr = rest_tr[:rest_tr.index('</tr>')]
    assert '값 대기 1 줄 포함' in rest_tr and '대기' in rest_tr
    sum_tr = line[line.index('<tr class="sum"'):]
    sum_tr = sum_tr[:sum_tr.index('</tr>')]
    assert '대기' in sum_tr and '줄 포함' not in sum_tr


def test_other_row_without_pending_rows_has_no_pending_note(tmp_path):
    code, out = run_report(tmp_path, sample_doc(), top_pairs=1, top_electrodes=1)
    assert '줄 포함' not in html_of(out)


def test_absent_zero_not_applicable_and_pending_are_told_apart(tmp_path):
    pairs = [spec('Z1', ['Zed', 'Eff'], 'line', 0.0, 0.0, change='no_change'),
             spec('N1', ['New', 'Eff'], 'line', 0.0, 0.0030, present_a=False, change='new_pair'),
             spec('G1', ['Gone', 'Eff'], 'line', 0.0020, 0.0, present_b=False, change='gone_pair'),
             spec('W1', ['Wait', 'Eff'], 'line', None, 0.0010, change='value_pending'),
             spec('F1', ['Fil', 'Eff'], 'line', 0.0, 0.0010, filtered=(True, False)),
             spec('PX', ['Pix', 'Eff2'], 'pixel', 0.0040, 0.0050)]
    code, out = run_report(tmp_path, make_ledger(pairs, cno=[('Pix', 'PXL', 0.001, 0.002)]))
    page = html_of(out)
    line = page[page.index('<table id="pairs-line"'):page.index('<table id="pairs-pixel"')]

    def cells(name):
        # one pair row including its nested item table (up to the next pair row)
        m = re.search(r'<tr id="pair-(L\d+)"[^>]*>(?:(?!<tr id="pair-).)*?<b>%s</b>(.*?)(?=<tr id="pair-|<tr class="(?:rest|sum)")' % re.escape(name),
                      line, re.S)
        assert m, name
        return m.group(2)

    # present, value 0 (the pending W1 makes the line vi null, so there is no % base: plain 0)
    assert '<td>0</td>' in cells('Zed–Eff')
    assert '없음' in cells('New–Eff') and '0%' not in cells('New–Eff').split('없음')[0]   # absent in A
    assert '없음' in cells('Gone–Eff')                         # absent in B
    assert '대기' in cells('Wait–Eff')                         # present, value null
    assert '거리 필터로 0' in cells('Fil–Eff')                 # distance filter on the A side
    # electrodes of the line table that have no pixel pair / CNO say 해당 없음 in the pixel table
    marker = '해당 없음 — 이 종류의 쌍(CNO)이 없는 전극'
    pix = page[page.index('<table id="el-pixel"'):page.index(marker, page.index('<table id="el-pixel"'))]
    assert '해당 없음' in pix                                   # Pix has CNO; Eff2 has no CNO
    assert 'Zed' not in pix                                    # a line-only electrode is not in the pixel table
    tail = page[page.index(marker):page.index('<section id="s4">')]
    assert 'Zed' in tail                                       # ...it is listed under 해당 없음


def test_print_mode_opens_every_details(tmp_path):
    code, out = run_report(tmp_path, sample_doc(), top_pairs=1)
    page = html_of(out)
    assert 'addEventListener(\'beforeprint\'' in page and "location.hash==='#print'" in page
    assert "querySelectorAll('details')" in page and '.open=true' in page
    assert '@media print' in page and page.count('<details') >= 3


def test_names_with_markup_are_escaped_everywhere(tmp_path):
    evil = '</script><script>alert(1)</script>'
    name1, name2 = 'Q<&>한글"\'', evil
    pairs = [spec('E1', [name1, name2], 'line', 0.001, 0.002), spec('E2', [name2, 'ok'], 'pixel', 0.001, 0.003, layer=evil)]
    doc = make_ledger(pairs, cno=[(name1, evil, 0.001, 0.002)])
    code, out = run_report(tmp_path, doc)
    page = html_of(out)
    assert code == 0
    markup = page[:page.index('<script type="application/json" id="report-data">')]   # outside the JSON data block
    assert '<script>alert(1)' not in markup and '</script><script>alert' not in markup
    assert page.count('<script') == 2 and page.count('</script>') == 2   # the data block and the print script only
    assert 'Q&lt;&amp;&gt;한글' in page and '&lt;/script&gt;&lt;script&gt;alert(1)' in page
    block = page[page.index('<script type="application/json" id="report-data">') + len('<script type="application/json" id="report-data">'):]
    block = block[:block.index('</script>')]
    assert '</' not in block
    data = json.loads(block)                                  # still valid JSON ("<\/" is a valid escape)
    assert data['shown']['electrodes']['line'][0] in (name1, name2)


def test_partial_ledger_gets_a_band_and_exit_3(tmp_path):
    doc = make_ledger(sample_doc()['pairs'] and [spec('A1', ['A', 'B'], 'line', 0.001, 0.002)], status='partial',
                      reasons=['ledger_totals_check_failed'])
    code, out = run_report(tmp_path, doc, execution={'command': 'ledger', 'status': 'partial', 'exit_code': 3})
    assert code == 3
    page = html_of(out)
    assert 'class="band partial"' in page and '부분 결과' in page and 'ledger_totals_check_failed' in page
    assert page.index('부분 결과') < page.index('<section id="s1">')
    assert json.loads((out / 'report.json').read_text(encoding='utf-8'))['status'] == 'partial'
    ok_code, ok_out = run_report(tmp_path, sample_doc(), name='ok')
    assert ok_code == 0 and 'class="band partial"' not in html_of(ok_out)


def test_missing_or_bad_ledger_input_exits_2_with_error_json(tmp_path):
    out = tmp_path / 'o1'
    assert ledger_report.main_ledger_report(str(tmp_path / 'nothing'), str(out), lambda r: None) == 2
    assert json.loads((out / 'error.json').read_text(encoding='utf-8'))['code'] == 'ledger_input_missing'
    assert sorted(p.name for p in out.iterdir()) == ['error.json', 'execution.json']
    # ledger.json exists, but the ledger run ended with an error
    code, out2 = run_report(tmp_path, sample_doc(), name='o2', execution={'command': 'ledger', 'status': 'error', 'exit_code': 2})
    assert code == 2 and json.loads((out2 / 'error.json').read_text(encoding='utf-8'))['code'] == 'ledger_input_missing'
    assert not (out2 / 'ledger_report.html').exists()
    # other schema version
    bad = sample_doc()
    bad['schema_version'] = 'ledger_v2'
    code, out3 = run_report(tmp_path, bad, name='o3')
    assert code == 2 and json.loads((out3 / 'error.json').read_text(encoding='utf-8'))['code'] == 'ledger_schema_mismatch'
    # bad options
    code, out4 = run_report(tmp_path, sample_doc(), name='o4', top_pairs=0)
    assert code == 2 and json.loads((out4 / 'error.json').read_text(encoding='utf-8'))['code'] == 'ledger_report_option_invalid'


def test_output_folder_convention(tmp_path):
    doc = sample_doc()
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'mine.txt').write_text('keep', encoding='utf-8')
    ledger_dir = write_ledger(tmp_path / 'led', doc)
    assert ledger_report.main_ledger_report(str(ledger_dir), str(foreign), lambda r: None) == 2
    assert [p.name for p in foreign.iterdir()] == ['mine.txt']          # not owned: nothing written, nothing removed
    out = tmp_path / 'again'
    assert ledger_report.main_ledger_report(str(ledger_dir), str(out), lambda r: None) == 0
    assert ledger_report.main_ledger_report(str(ledger_dir), str(out), lambda r: None) == 0   # own folder: overwrite
    assert sorted(p.name for p in out.iterdir()) == ['execution.json', 'ledger_report.html', 'report.json']
    # the ledger folder itself is not a valid output (it holds ledger.json)
    assert ledger_report.main_ledger_report(str(ledger_dir), str(ledger_dir), lambda r: None) == 2
    assert (ledger_dir / 'ledger.json').exists()
    # a failure inside an owned folder removes the old report and leaves error.json + execution.json
    (ledger_dir / 'ledger.json').write_text('{not json', encoding='utf-8')
    assert ledger_report.main_ledger_report(str(ledger_dir), str(out), lambda r: None) == 2
    assert sorted(p.name for p in out.iterdir()) == ['error.json', 'execution.json']


def test_line_and_pixel_are_separate_tables_and_never_added_or_merged(tmp_path):
    doc = sample_doc()
    code, out = run_report(tmp_path, doc)
    page = html_of(out)
    s4 = page[page.index('<section id="s4">'):page.index('<section id="s5">')]
    line_part = s4[s4.index('id="pairs-line"'):s4.index('id="pairs-pixel"')]
    pixel_part = s4[s4.index('id="pairs-pixel"'):s4.index('id="cno"')]
    assert all('id="pair-P' not in line_part for _ in (0,)) and 'id="pair-L' not in pixel_part
    assert re.findall(r'id="pair-(L\d+)"', line_part) == ['L1', 'L2', 'L3']
    assert re.findall(r'id="pair-(P\d+)"', pixel_part) == ['P1', 'P2']
    # no row or total adds the two kinds: the 1-section totals are the per-kind values, never their sum
    vi_line, vi_pixel = doc['totals']['line']['a'], doc['totals']['pixel']['a']
    assert '%.4f%%' % ((vi_line + vi_pixel) * 100) not in page
    for needle in ('line + pixel', 'line+pixel', '통합 순위', '전체 순위'):
        assert needle not in page


def test_unavailable_dropped_columns_and_pending_row_hidden_when_zero(tmp_path):
    doc = sample_doc()
    code, out = run_report(tmp_path, doc)
    page = html_of(out)
    table = page[page.index('<table id="change-kinds">'):page.index('</table>', page.index('<table id="change-kinds">'))]
    assert '값 대기' not in table and 'CRP 차이' in table
    pairs = [spec('W1', ['Wait', 'Eff'], 'line', None, 0.0010, change='value_pending')]
    doc2 = make_ledger(pairs)
    doc2['unavailable'] = [{'scope': 'pair', 'id': 'W1', 'side': 'reference', 'reasons': ['condition_not_computable']}]
    code, out2 = run_report(tmp_path, doc2, name='pend')
    page2 = html_of(out2)
    assert '값 대기' in page2 and 'condition_not_computable' in page2 and '계산 불가 항목 1 개' in page2


def test_rule_16_things_the_stage_leaves_out(tmp_path):
    code, out = run_report(tmp_path, sample_doc())
    page = html_of(out)
    for word in ('방향', '분포', '신뢰도', '우선순위', '되돌리', 'DR 판정', '간격'):
        assert word not in page.replace('방향이', ''), word


def test_synthetic_500_pairs_200_electrodes_size_and_time(tmp_path, capsys):
    n_elec = 200
    pairs = []
    for i in range(500):
        kind = 'line' if i % 2 == 0 else 'pixel'
        layer = 'L%d' % (i % 5)
        a = (i % 17 + 1) * 1e-4
        b = a * (1.3 if i % 3 else 0.8)
        pairs.append(spec('PAIR%03d' % i, ['E%03d' % (i % n_elec), 'E%03d' % ((i * 7 + 1) % n_elec)], kind, a, b, layer=layer))
    doc = make_ledger(pairs, cno=[('E%03d' % i, 'L%d' % (i % 5), 1e-4, 2e-4) for i in range(50)], extra_electrodes=['E%03d' % i for i in range(n_elec)])
    assert len(doc['pairs']) == 500 and len(doc['electrodes']) == n_elec
    started = time.time()
    code, out = run_report(tmp_path, doc)
    elapsed = time.time() - started
    size = (out / 'ledger_report.html').stat().st_size
    with capsys.disabled():
        print('\n[T5 500 pairs / 200 electrodes] html %d bytes, %.2f s' % (size, elapsed))
    assert code == 0 and elapsed < 20 and size < 12 * 1024 * 1024


def test_cli_registers_ledger_report_and_runs_end_to_end(tmp_path):
    ledger_dir = write_ledger(tmp_path / 'led', sample_doc())
    out = tmp_path / 'cli_out'
    proc = subprocess.run([sys.executable, '-m', 'simcompare', 'ledger-report', '--ledger-dir', str(ledger_dir), '--out', str(out),
                           '--top-pairs', '2', '--top-electrodes', '3'], capture_output=True, text=True,
                          env={'PYTHONPATH': SRC, 'PATH': '/usr/bin:/bin'})
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)['options'] == {'top_pairs': 2, 'top_electrodes': 3}
    assert (out / 'ledger_report.html').exists()
    missing = subprocess.run([sys.executable, '-m', 'simcompare', 'ledger-report', '--ledger-dir', str(tmp_path / 'nope'),
                              '--out', str(tmp_path / 'cli_err')], capture_output=True, text=True,
                             env={'PYTHONPATH': SRC, 'PATH': '/usr/bin:/bin'})
    assert missing.returncode == 2 and (tmp_path / 'cli_err' / 'error.json').exists()


def test_real_compare2_ledger_chain_feeds_the_report(tmp_path):
    from simcompare import ledger
    from test_compare2 import CNO_CSV, CNO_NAME, run, two_sides
    from test_ledger_integration import CNO_B
    ref, tgt = two_sides()
    code, c2 = run(tmp_path, ref, tgt, ref_group='mobile', tgt_group='auto', ref_cno={CNO_NAME: CNO_CSV}, tgt_cno={CNO_NAME: CNO_B})
    assert code == 0
    led_out = tmp_path / 'real_ledger'
    assert ledger.main_ledger(str(c2), str(tmp_path / 'compare2.yaml'), str(led_out), lambda r: None) == 0
    out = tmp_path / 'real_report'
    assert ledger_report.main_ledger_report(str(led_out), str(out), lambda r: None, top_pairs=1, top_electrodes=1) == 0
    page = html_of(out)
    doc = json.loads((led_out / 'ledger.json').read_text(encoding='utf-8'))
    for kind in KINDS:
        rows = rows_between(page, '<table id="pairs-%s"' % kind, '<table id="pairs-pixel"' if kind == 'line' else '<table id="cno"')
        total = [r for r in rows if r[0] == 'total'][0]
        assert float(total[1]) == doc['totals'][kind]['items']['a']
        assert math.fsum(float(r[2]) for r in rows if r[0] in ('top', 'rest')) == pytest.approx(float(total[2]), abs=1e-12)
    for needle in ('http://', 'https://', '<link', ' src='):
        assert needle not in page
