"""ledger-report (mrep/T5): the merged report frame — data bundle, fixed bar with the global selection kept in the hash,
section 1 (summary with the item breakdown) and section 5 (evidence). Sections 2, 3 and 4 are slots (T6-T8).

test_ledger.py / test_ledger_integration.py cover the ledger itself. These tests build `ledger_v1` documents by hand (a
consistent builder: totals are the sums of the rows, and the mrep/T1 keys are filled) and one from the real compare2 -> ledger chain.
The browser checks (headless Chrome --dump-dom) are in test_ledger_report_v2.py."""
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
         filtered=(False, False), failure='lateral_short', lower=None, comp=((), ())):
    return dict(pair_id=pair_id, electrodes=list(electrodes), kind=kind, a=a, b=b, present_a=present_a,
                present_b=present_b, change=change, layer=layer, filtered=filtered, failure=failure, lower=lower or layer,
                comp=comp)


def _breakdown(p, d):
    """pairs[].breakdown of one single-item pair (ledger.py `_pair_breakdown`)."""
    if d is None:
        return {'same_delta': None, 'new_value': None, 'gone_value': None, 'n_both': None, 'n_new': None, 'n_gone': None,
                'status': 'not_evaluated'}
    both, new, gone = p['present_a'] and p['present_b'], p['present_b'] and not p['present_a'], p['present_a'] and not p['present_b']
    return {'same_delta': d if both else 0.0, 'new_value': p['b'] if new else 0.0, 'gone_value': p['a'] if gone else 0.0,
            'n_both': int(both), 'n_new': int(new), 'n_gone': int(gone), 'status': 'ok'}


def make_ledger(pairs, cno=(), extra_electrodes=(), status='ok', reasons=(), vi=None, sizes=(1.0, 2.0), complete=True,
                crp=None, intercept=0.01, crp_a=1.0, crp_b=1.0):
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
                'lambda_a': 1.0, 'lambda_b': 2.0, 'a': p['a'], 'b': p['b'], 'delta': d,
                'by_size': [{'diameter_um': s, 'density_layer': p['layer'], 'area_a': 1.5, 'area_b': 2.5, 'lambda_a': 0.01 * s,
                             'lambda_b': 0.02 * s, 'value_a': None if p['a'] is None else p['a'] / len(sizes),
                             'value_b': None if p['b'] is None else p['b'] / len(sizes)} for s in sizes]}
        out_pairs.append({'pair_id': p['pair_id'], 'electrodes': p['electrodes'], 'kind': p['kind'], 'present_a': p['present_a'],
                          'present_b': p['present_b'], 'computable_a': p['a'] is not None, 'computable_b': p['b'] is not None,
                          'a': p['a'], 'b': p['b'], 'delta': d,
                          'layers_a': [p['layer']] if p['present_a'] else [], 'layers_b': [p['layer']] if p['present_b'] else [],
                          'items': [item], 'change_kind': p['change'], 'pct_a': pct_of(p['a'], va), 'pct_b': pct_of(p['b'], va),
                          'pct_delta': pct_of(d, va), 'rank': p.get('rank'), 'breakdown': _breakdown(p, d),
                          'composition': {'added': [{'item_id': 'x', 'failure_type': 'lateral_short', 'layers': [], 'text': t} for t in p['comp'][0]],
                                          'removed': [{'item_id': 'y', 'failure_type': 'lateral_short', 'layers': [], 'text': t} for t in p['comp'][1]]}})
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
        brk = [r['breakdown'] for r in out_pairs if r['kind'] == kind]
        if any(r['delta'] is None for r in out_pairs if r['kind'] == kind):
            parts = {'same': None, 'new': None, 'gone': None, 'cno': None, 'crp': None}
        else:
            parts = {'same': math.fsum(b['same_delta'] for b in brk), 'new': math.fsum(b['new_value'] for b in brk),
                     'gone': math.fsum(b['gone_value'] for b in brk), 'cno': cno_sum['delta'] if kind == 'pixel' else 0.0,
                     'crp': crp_d if kind == 'pixel' else 0.0}
        kinds[kind] = {'computable': vi.get(kind) != 'none', 'vi_a': totals[kind]['vi_a'], 'vi_b': totals[kind]['vi_b'],
                       'slope': 1.0, 'slope_method': 'secant', 'lambda_a': 1.0, 'lambda_b': 2.0, 'pair_count': len(rows),
                       'change_kinds': {code: sum(1 for p in rows if p['change'] == code) for code, _ in CHANGE_KINDS},
                       'increase_sum': math.fsum(v for v in known if v > 0), 'decrease_sum': math.fsum(v for v in known if v < 0),
                       'value_pending_rows': 0, 'item_breakdown': parts}
    kinds['change_kind_counts'] = counts
    item_checks = {kind: {'status': 'passed', 'items': {'status': 'passed'}, 'total': {'status': 'passed'}} for kind in KINDS}
    pair_states = [r['breakdown']['status'] for r in out_pairs]
    inventory = {role: {'electrodes': names, 'layers': {}, 'source': 'dl1' if complete else 'observed',
                        'status': 'complete' if complete else 'incomplete', 'sha256': 'f' * 64} for role in ('reference', 'target')}
    return {'schema_version': 'ledger_v1', 'status': status, 'reasons': list(reasons),
            'change_kinds': [{'code': c, 'label': l} for c, l in CHANGE_KINDS],
            'electrode_status': [{'code': c, 'label': l} for c, l in STATUS_LABELS],
            'inputs': {'compare2_json_sha256': 'a' * 64, 'config_sha256': 'b' * 64, 'ledger_basis': 'pair_share_v1',
                       'products': {'reference': {'model_group': 'mobile', 'pair_table_hash': 'h' * 8, 'crp': crp_a},
                                    'target': {'model_group': 'auto', 'pair_table_hash': 'h' * 8, 'crp': crp_b}},
                       'electrode_inventory': inventory},
            'totals': totals, 'kinds': kinds, 'pairs': out_pairs, 'electrodes': el_rows, 'cno': cno_rows, 'layers': layer_rows,
            'checks': {'status': 'passed' if status == 'ok' else 'failed',
                       'equations': {'eq1_line_a': {'status': 'passed' if status == 'ok' else 'failed'}},
                       'item_breakdown': {'pair': {'status': 'passed', 'checked': pair_states.count('ok'), 'failed': 0,
                                                   'not_evaluated': pair_states.count('not_evaluated')}, 'kind': item_checks}},
            'warnings': [], 'unavailable': []}


def write_ledger(folder, doc, execution=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'ledger.json').write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    if execution is not False:
        (folder / 'execution.json').write_text(json.dumps(execution or {'command': 'ledger', 'status': doc['status'], 'exit_code': 0}),
                                               encoding='utf-8')
    return folder


def run_report(tmp_path, doc, name='out', top_pairs=30, top_electrodes=30, **kwargs):
    ledger_dir = write_ledger(tmp_path / ('ledger_' + name), doc, **{k: v for k, v in kwargs.items() if k == 'execution'})
    out = tmp_path / name
    code = ledger_report.main_ledger_report(str(ledger_dir), str(out), lambda record: None,
                                            top_pairs=top_pairs, top_electrodes=top_electrodes,
                                            **{k: v for k, v in kwargs.items() if k != 'execution'})
    return code, out


def html_of(out):
    return (out / 'ledger_report.html').read_text(encoding='utf-8')


def bundle_of(page):
    return json.loads(re.search(r'<script type="application/json" id="report-data">(.*?)</script>', page, re.S).group(1))


def section(page, number):
    start = page.index('<section id="s%d">' % number)
    return page[start:page.index('</section>', start)]


def sample_doc():
    pairs = [spec('P1', ['Data', 'ELVDD'], 'line', 0.0120, 0.0150), spec('P2', ['Scan1', 'EM'], 'line', 0.0060, 0.0078, change='layer_change',
                                                                         comp=(['상하 GAT2–SD2'], ['측면 GAT2'])),
             spec('P3', ['Anode', 'Vint'], 'pixel', 0.0200, 0.0200, change='no_change', layer='PXL'),
             spec('P4', ['Gnode', 'Anode'], 'pixel', 0.0090, 0.0050, layer='SD1', failure='vertical_short', lower='PXL'),
             spec('P5', ['Scan2', 'Vint'], 'line', 0.0, 0.0065, present_a=False, change='new_pair', layer='SD1')]
    return make_ledger(pairs, cno=[('Anode', 'PXL', 0.0050, 0.0060)])


# ---- tests -----------------------------------------------------------------------------------------------------------

def test_html_has_no_external_reference_and_all_files(tmp_path):
    code, out = run_report(tmp_path, sample_doc())
    assert code == 0
    page = html_of(out)
    assert sorted(p.name for p in out.iterdir()) == ['execution.json', 'ledger_report.html', 'report.json']
    for needle in ('http://', 'https://', '<link', ' src=', 'url(', '@import'):
        assert needle not in page, needle
    report = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert report['schema_version'] == 'ledger_report_v2'
    assert len(report['ledger_json_sha256']) == 64 and report['options'] == {'top_pairs': 30, 'top_electrodes': 30}
    assert report['ledger_execution']['status'] == 'ok'
    assert report['sections'] == {'present': [1, 5], 'placeholders': [2, 3, 4]}
    assert report['html_bytes'] == len(page.encode('utf-8')) and 0 < report['data_bytes'] < report['html_bytes']
    assert report['report07'] == 'none'
    assert json.loads((out / 'execution.json').read_text(encoding='utf-8'))['exit_code'] == 0


def test_five_sections_in_order_slots_for_2_3_4_and_one_data_bundle(tmp_path):
    code, out = run_report(tmp_path, sample_doc())
    page = html_of(out)
    titles = ['① 한눈 요약', '② 제품 대응', '③ 탐색', '④ 분포', '⑤ 근거 · 검산']
    positions = [page.index('<h2>%s</h2>' % t) for t in titles]
    assert positions == sorted(positions)
    for number in (3,):                                 # sections 2 and 4 are built (mrep/T6); 3 is T7-T8
        part = section(page, number)
        assert '다음 단계에서 추가됩니다' in part and '<table' not in part
    assert '<table id="corr-table">' in section(page, 2) and '<table id="ledger-table">' in section(page, 4)
    assert page.count('id="report-data"') == 1 and page.count('<script type="application/json"') == 1
    for old in ('<section id="s6">', '<section id="s7">', 'id="pairs-line"', 'id="el-line"'):
        assert old not in page


def test_bar_has_the_global_selection_clear_and_five_links(tmp_path):
    code, out = run_report(tmp_path, sample_doc())
    page = html_of(out)
    bar = page[page.index('<div id="bar">'):page.index('</div>', page.index('<div id="bar">'))]
    assert 'id="sel-label"' in bar and 'id="sel-clear"' in bar and 'disabled' in bar and '선택 해제' in bar
    assert [m for m in re.findall(r'data-go="(s\d)"', bar)] == ['s1', 's2', 's3', 's4', 's5']


def test_partial_ledger_gets_a_band_and_exit_3(tmp_path):
    doc = make_ledger([spec('A1', ['A', 'B'], 'line', 0.001, 0.002)], status='partial', reasons=['ledger_totals_check_failed'])
    code, out = run_report(tmp_path, doc, execution={'command': 'ledger', 'status': 'partial', 'exit_code': 3})
    assert code == 3
    page = html_of(out)
    assert 'class="band partial"' in page and '부분 결과' in page and 'ledger_totals_check_failed' in page
    assert page.index('부분 결과') < page.index('<section id="s1">')
    assert json.loads((out / 'report.json').read_text(encoding='utf-8'))['status'] == 'partial'
    assert '부분 결과: <b>예</b>' in section(page, 1)
    ok_code, ok_out = run_report(tmp_path, sample_doc(), name='ok')
    assert ok_code == 0 and 'id="partial"' not in html_of(ok_out) and '부분 결과: <b>아니오</b>' in section(html_of(ok_out), 1)


def test_missing_or_bad_ledger_input_exits_2_with_error_json(tmp_path):
    out = tmp_path / 'o1'
    assert ledger_report.main_ledger_report(str(tmp_path / 'nothing'), str(out), lambda r: None) == 2
    assert json.loads((out / 'error.json').read_text(encoding='utf-8'))['code'] == 'ledger_input_missing'
    assert sorted(p.name for p in out.iterdir()) == ['error.json', 'execution.json']
    code, out2 = run_report(tmp_path, sample_doc(), name='o2', execution={'command': 'ledger', 'status': 'error', 'exit_code': 2})
    assert code == 2 and json.loads((out2 / 'error.json').read_text(encoding='utf-8'))['code'] == 'ledger_input_missing'
    assert not (out2 / 'ledger_report.html').exists()
    bad = sample_doc()
    bad['schema_version'] = 'ledger_v2'
    code, out3 = run_report(tmp_path, bad, name='o3')
    assert code == 2 and json.loads((out3 / 'error.json').read_text(encoding='utf-8'))['code'] == 'ledger_schema_mismatch'
    code, out4 = run_report(tmp_path, sample_doc(), name='o4', top_pairs=0)
    assert code == 2 and json.loads((out4 / 'error.json').read_text(encoding='utf-8'))['code'] == 'ledger_report_option_invalid'


def test_ledger_without_item_breakdown_exits_2_input_outdated(tmp_path):
    for kind_to_cut in ('line', 'pixel'):
        old = sample_doc()
        del old['kinds'][kind_to_cut]['item_breakdown']
        code, out = run_report(tmp_path, old, name='old_' + kind_to_cut)
        error = json.loads((out / 'error.json').read_text(encoding='utf-8'))
        assert code == 2 and error['code'] == 'ledger_report_input_outdated' and '08 을 새 번들로 다시' in error['detail']
        assert not (out / 'ledger_report.html').exists()


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
    assert ledger_report.main_ledger_report(str(ledger_dir), str(ledger_dir), lambda r: None) == 2
    assert (ledger_dir / 'ledger.json').exists()
    (ledger_dir / 'ledger.json').write_text('{not json', encoding='utf-8')
    assert ledger_report.main_ledger_report(str(ledger_dir), str(out), lambda r: None) == 2
    assert sorted(p.name for p in out.iterdir()) == ['error.json', 'execution.json']


def test_names_with_markup_are_escaped_and_the_bundle_cannot_close_the_script(tmp_path):
    name1, name2 = '<b>x</b>', '</script><i>y'
    doc = make_ledger([spec('P1', [name1, name2], 'line', 0.001, 0.002), spec('P2', ['A&B', 'C"D'], 'line', 0.001, 0.0005)])
    code, out = run_report(tmp_path, doc)
    page = html_of(out)
    assert '<b>x</b>' not in page and '&lt;b&gt;x&lt;/b&gt;' in page and '<i>y' not in page
    block = page[page.index('id="report-data">') + len('id="report-data">'):]
    block = block[:block.index('</script>')]
    assert '</' not in block and json.loads(block)['pairs'][0]['e'] in ([name1, name2], ['A&B', 'C"D'])


def test_line_and_pixel_are_separate_and_never_added(tmp_path):
    doc = sample_doc()
    code, out = run_report(tmp_path, doc)
    page = html_of(out)
    vi_line, vi_pixel = doc['totals']['line']['a'], doc['totals']['pixel']['a']
    assert '%.4f%%' % ((vi_line + vi_pixel) * 100) not in page
    for needle in ('line + pixel', 'line+pixel', '통합 순위', '전체 순위'):
        assert needle not in page
    assert 'data-kind="line"' in section(page, 1) and 'data-kind="pixel"' in section(page, 1)


def test_rule_16_things_the_stage_leaves_out(tmp_path):
    code, out = run_report(tmp_path, sample_doc())
    page = html_of(out)
    visible = re.sub(r'<script.*?</script>', '', page, flags=re.S)
    for word in ('신뢰도', '우선순위', '되돌리', 'DR 판정', '간격', '가정 계산 표'):
        assert word not in visible.replace('가정 계산과', ''), word


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
    report = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    with capsys.disabled():
        print('\n[T5 500 pairs / 200 electrodes] html %d bytes (data %d), %.2f s' % (report['html_bytes'], report['data_bytes'], elapsed))
    assert code == 0 and elapsed < 20 and report['html_bytes'] < 8 * 1024 * 1024


def test_cli_registers_ledger_report_and_runs_end_to_end(tmp_path):
    ledger_dir = write_ledger(tmp_path / 'led', sample_doc())
    out = tmp_path / 'cli_out'
    env = {'PYTHONPATH': SRC, 'PATH': '/usr/bin:/bin'}
    proc = subprocess.run([sys.executable, '-m', 'simcompare', 'ledger-report', '--ledger-dir', str(ledger_dir), '--out', str(out),
                           '--top-pairs', '2', '--top-electrodes', '3'], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)['options'] == {'top_pairs': 2, 'top_electrodes': 3}
    assert (out / 'ledger_report.html').exists()
    missing = subprocess.run([sys.executable, '-m', 'simcompare', 'ledger-report', '--ledger-dir', str(tmp_path / 'nope'),
                              '--out', str(tmp_path / 'cli_err')], capture_output=True, text=True, env=env)
    assert missing.returncode == 2 and (tmp_path / 'cli_err' / 'error.json').exists()
    helped = subprocess.run([sys.executable, '-m', 'simcompare', 'ledger-report', '--help'], capture_output=True, text=True, env=env)
    assert '--report07-dir' in helped.stdout + helped.stderr


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
    data = bundle_of(page)
    for kind in KINDS:
        assert data['kinds'][kind]['breakdown'] == doc['kinds'][kind]['item_breakdown']
        assert data['kinds'][kind]['delta'] == doc['totals'][kind]['delta']
    assert [p['id'] for p in data['pairs']] == [p['pair_id'] for p in doc['pairs']]
    for needle in ('http://', 'https://', '<link', ' src='):
        assert needle not in page
