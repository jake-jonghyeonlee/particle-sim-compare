"""ledger-report (mrep/T5): section 1 (summary, item breakdown), section 5 (evidence), the data bundle and the hash state.

The real chain (synthetic 02 -> ledger -> ledger-maps with 04 -> ledger-report) is `test_real_chain_report_for_the_lead`; open its
HTML with `pytest tests/test_ledger_report_v2.py -k test_real_chain_report_for_the_lead --basetemp=/tmp/mrep_t5` and look at
/tmp/mrep_t5/test_real_chain_report_for_the0/real_report/ledger_report.html (the folder name is cut by pytest). The Chrome tests (`--dump-dom`) skip when Chrome is missing."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

from simcompare import ledger_report
from simcompare.cli import main
from test_ledger_maps_changes import candidates_run
from test_ledger_maps_cards import big  # noqa: F401  (fixture)
from test_ledger_report import bundle_of, html_of, make_ledger, run_report, sample_doc, section, spec, write_ledger
from test_ledger_report_maps import ledger_sha, ok_changes, setup, write_maps

KINDS = ('line', 'pixel')
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'


def _list_ids(page, list_id):
    block = page[page.index('id="%s"' % list_id):]
    block = block[:block.index('</ul>')]
    return re.findall(r'<li data-pair="([^"]*)"', block)


def _texts(fragment):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', fragment))


# ---- section 1 --------------------------------------------------------------------------------------------------------

def test_kpis_conflict_sentence_and_no_curve_line_in_section_1(tmp_path):
    doc = sample_doc()
    code, out = run_report(tmp_path, doc)
    s1 = section(html_of(out), 1)
    for kind in KINDS:
        part = s1[s1.index('<div class="kpi" data-kind="%s">' % kind):]
        part = part[:part.index('</div></div>')]
        k, t = doc['kinds'][kind], doc['totals'][kind]
        assert 'A %s → B %s' % (ledger_report.raw_pct(k['vi_a']), ledger_report.raw_pct(k['vi_b'])) in part
        assert ledger_report.raw_pp(t['delta']) in part
        assert 'A 대비 %s' % ledger_report.spct(100.0 * t['delta'] / k['vi_a']) in part
    assert 'id="headline"' in s1
    assert '곡선 보정' not in s1 and '절편' not in s1                   # the curve line is in section 5 only
    assert '곡선 보정' in section(html_of(out), 5)


def test_headline_says_conflict_only_when_the_kinds_move_in_opposite_directions(tmp_path):
    up_down = make_ledger([spec('A', ['E1', 'E2'], 'line', 0.001, 0.002), spec('B', ['E3', 'E4'], 'pixel', 0.002, 0.001)])
    same = make_ledger([spec('A', ['E1', 'E2'], 'line', 0.001, 0.002), spec('B', ['E3', 'E4'], 'pixel', 0.001, 0.002)])
    pending = make_ledger([spec('A', ['E1', 'E2'], 'line', None, 0.002, change='value_pending'), spec('B', ['E3', 'E4'], 'pixel', 0.001, 0.002)])
    assert '상충합니다' in section(html_of(run_report(tmp_path, up_down, name='a')[1]), 1)
    assert '방향이 같습니다' in section(html_of(run_report(tmp_path, same, name='b')[1]), 1)
    assert '방향을 말하지 않습니다' in section(html_of(run_report(tmp_path, pending, name='c')[1]), 1)


def test_item_breakdown_rows_sum_to_the_kind_delta_when_the_crp_is_the_same(tmp_path):
    doc = sample_doc()
    code, out = run_report(tmp_path, doc)
    page = html_of(out)
    s1 = section(page, 1)
    table = s1[s1.index('<table id="breakdown">'):s1.index('</table>', s1.index('<table id="breakdown">'))]
    assert [m for m in re.findall(r'<tr data-cat="(\w+)"', table)] == ['same', 'new', 'gone', 'cno']
    for kind in KINDS:
        brk = doc['kinds'][kind]['item_breakdown']
        total = doc['totals'][kind]['delta']
        assert brk['same'] + brk['new'] - brk['gone'] + brk['cno'] + brk['crp'] == pytest.approx(total, abs=1e-12)
        for cat, value in (('same', brk['same']), ('new', brk['new']), ('gone', -brk['gone'])):
            row = re.search(r'<tr data-cat="%s">(.*?)</tr>' % cat, table, re.S).group(1)
            cells = re.findall(r'<td class="l[^>]*>(.*?)</td>', row, re.S)
            cell = cells[1] if kind == 'line' else cells[2]
            assert (ledger_report.raw_pp(value) in cell) if value else ('0%p' in cell)
    cno_row = re.search(r'<tr data-cat="cno">(.*?)</tr>', table, re.S).group(1)
    assert '해당 없음' in cno_row and ledger_report.raw_pp(doc['kinds']['pixel']['item_breakdown']['cno']) in cno_row
    assert 'id="crp-note"' not in s1 and 'id="crp-line"' not in section(page, 5)
    assert '합 = 종류 전체 Δ' in table
    assert 'data-href="#cat=line:same"' in table and 'data-href="#cat=pixel:gone"' in table


def test_crp_note_only_when_the_two_crps_differ(tmp_path):
    pairs = [spec('A', ['E1', 'E2'], 'line', 0.001, 0.002), spec('B', ['E3', 'E4'], 'pixel', 0.002, 0.003)]
    differ = make_ledger(pairs, crp=0.0005, crp_a=1.0, crp_b=1.2)
    code, out = run_report(tmp_path, differ)
    page = html_of(out)
    s1 = section(page, 1)
    assert 'id="crp-note"' in s1 and 'CRP 차이' in s1 and '⑤ 참고' in s1 and ledger_report.raw_pp(0.0005) in s1
    s5 = section(page, 5)
    assert 'id="crp-line"' in s5 and 'A CRP 1' in s5 and 'B CRP 1.2' in s5
    # the breakdown rows do not add up to the delta by exactly the CRP difference
    brk = differ['kinds']['pixel']['item_breakdown']
    assert differ['totals']['pixel']['delta'] - (brk['same'] + brk['new'] - brk['gone'] + brk['cno']) == pytest.approx(0.0005, abs=1e-12)
    same_crp = make_ledger(pairs)
    assert 'id="crp-note"' not in section(html_of(run_report(tmp_path, same_crp, name='same')[1]), 1)


def test_pending_breakdown_is_shown_as_waiting_for_that_kind(tmp_path):
    doc = make_ledger([spec('A', ['E1', 'E2'], 'line', None, 0.002, change='value_pending'), spec('B', ['E3', 'E4'], 'pixel', 0.001, 0.002)])
    assert doc['kinds']['line']['item_breakdown']['same'] is None
    code, out = run_report(tmp_path, doc)
    s1 = section(html_of(out), 1)
    table = s1[s1.index('<table id="breakdown">'):s1.index('</table>', s1.index('<table id="breakdown">'))]
    for cat in ('same', 'new', 'gone'):
        row = re.search(r'<tr data-cat="%s">(.*?)</tr>' % cat, table, re.S).group(1)
        assert '대기' in re.findall(r'<td class="l[^>]*>(.*?)</td>', row, re.S)[1]
    assert ledger_report.raw_pp(doc['kinds']['pixel']['item_breakdown']['same']) in table


def test_lists_are_the_ledger_top_five_per_kind_with_composition_text(tmp_path):
    pairs = [spec('L%d' % i, ['A%d' % i, 'B%d' % i], 'line', 0.001 * (i + 1), 0.001 * (i + 1) * (1 + 0.1 * ((i * 5) % 7 - 3))) for i in range(9)]
    pairs += [spec('PX%d' % i, ['C%d' % i, 'D%d' % i], 'pixel', 0.002, 0.002 + 0.0001 * i) for i in range(7)]
    pairs.append(spec('LC', ['K', 'L'], 'line', 0.004, 0.0045, change='layer_change', comp=(['측면 GAT2', '상하 GAT2–SD1'], ['상하 SD1–PXL'])))
    doc = make_ledger(pairs)
    code, out = run_report(tmp_path, doc)
    page = html_of(out)
    for kind in KINDS:
        rows = [p for p in doc['pairs'] if p['kind'] == kind and p['pct_delta'] is not None]
        want_change = [p['pair_id'] for p in sorted(rows, key=lambda p: (-abs(p['pct_delta']), p['pair_id']))[:5]]
        want_risk = [p['pair_id'] for p in sorted(rows, key=lambda p: (-p['pct_b'], p['pair_id']))[:5]]
        assert _list_ids(page, 'top-changed-%s' % kind) == want_change
        assert _list_ids(page, 'top-risk-%s' % kind) == want_risk
    assert doc['pairs'][0]['kind'] == 'line'
    s1 = section(page, 1)
    assert s1.count('data-href="#sel=pair:') == len(_list_ids(page, 'top-changed-line') + _list_ids(page, 'top-changed-pixel')
                                                    + _list_ids(page, 'top-risk-line') + _list_ids(page, 'top-risk-pixel'))
    if 'LC' in _list_ids(page, 'top-risk-line') + _list_ids(page, 'top-changed-line'):
        assert 'item +2 / −1 (+측면 GAT2 +상하 GAT2–SD1 / −상하 SD1–PXL)' in s1


def test_change_kind_table_is_folded_zero_kinds_are_one_line_and_labels_come_from_the_ledger(tmp_path):
    doc = sample_doc()
    code, out = run_report(tmp_path, doc)
    s1 = section(html_of(out), 1)
    assert '<details id="kinds-details">' in s1 and '<details id="kinds-details" open' not in s1
    label = [c['label'] for c in doc['change_kinds'] if c['code'] == 'layer_change'][0]
    assert label == '불량 구성 변경' and ('<td class="l">%s</td>' % label) in s1
    assert 'id="zero-kinds"' in s1 and '값 대기' in s1[s1.index('id="zero-kinds"'):]
    shown = re.findall(r'<tr data-code="(\w+)"', s1)
    counts = doc['kinds']['change_kind_counts']
    assert shown == [c['code'] for c in doc['change_kinds'] if counts.get(c['code'], 0)]     # only kinds with at least one pair


def test_trust_line_counts_checks_exclusions_pending_and_partial(tmp_path):
    doc = sample_doc()
    doc['inputs']['pair_exclusions'] = {'reference': {'pairs': [{'electrodes': ['GH', 'X'], 'pair_id': 'G', 'reason': 'builtin:GH', 'cases': 2}],
                                                      'cases': []}}
    code, out = run_report(tmp_path, doc)
    s1 = section(html_of(out), 1)
    trust = s1[s1.index('id="trust"'):]
    assert '✓ 검산 통과' in trust and '제외 쌍 <b>1</b>' in trust and '대기 <b>0</b>' in trust and '부분 결과: <b>아니오</b>' in trust
    assert 'builtin:GH' not in s1 and 'id="pair-excluded"' in section(html_of(out), 5)


def test_item_breakdown_check_failed_makes_a_warning_band_and_a_failed_trust_state(tmp_path):
    doc = sample_doc()
    doc['checks']['item_breakdown']['kind']['line']['status'] = 'failed'
    code, out = run_report(tmp_path, doc)
    page = html_of(out)
    assert code == 0 and 'id="band-item-breakdown"' in page and page.index('id="band-item-breakdown"') < page.index('<section id="s1">')
    assert '✗ 검산 확인 필요' in section(page, 1) and '✗ 실패' in section(page, 5)
    assert 'id="band-item-breakdown"' not in html_of(run_report(tmp_path, sample_doc(), name='fine')[1])


def test_missing_maps_missing_04_and_partial_each_have_their_own_band(tmp_path):
    page = html_of(run_report(tmp_path, sample_doc(), name='nomaps')[1])
    assert 'id="band-no-maps"' in page and 'id="band-changes"' not in page
    doc, folder, maps = setup(tmp_path / 'nocand')
    page2 = html_of(ledger_report_run(tmp_path, folder, maps, 'nocand_out'))
    assert 'id="band-no-maps"' not in page2 and 'id="band-changes"' in page2 and 'id="tags-folded"' in section(page2, 1)
    partial = make_ledger([spec('A1', ['A', 'B'], 'line', 0.001, 0.002)], status='partial', reasons=['ledger_totals_check_failed'])
    code, out = run_report(tmp_path, partial, name='part', execution={'command': 'ledger', 'status': 'partial', 'exit_code': 3})
    assert code == 3 and 'id="partial"' in html_of(out)


def ledger_report_run(tmp_path, folder, maps, name, **kwargs):
    out = tmp_path / name
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None, maps_dir=None if maps is None else str(maps), **kwargs) == 0
    return out


# ---- section 5 --------------------------------------------------------------------------------------------------------

def test_section_5_sum_checks_are_the_ledger_totals_and_pass(tmp_path):
    doc = sample_doc()
    code, out = run_report(tmp_path, doc)
    s5 = section(html_of(out), 5)
    table = s5[s5.index('<table id="sum-check">'):s5.index('</table>', s5.index('<table id="sum-check">'))]
    assert table.count('✓ 통과') == 4 and '✗' not in table
    for kind in KINDS:
        assert ledger_report.raw_pct(doc['totals'][kind]['a']) in table and ledger_report.raw_pct(doc['totals'][kind]['b']) in table
    assert ledger_report.raw_pct(doc['totals']['line']['intercept']['a']) in table                 # the curve line
    item = s5[s5.index('<table id="item-check">'):s5.index('</table>', s5.index('<table id="item-check">'))]
    assert item.count('✓ 통과') == 2 and 'id="pair-check"' in s5
    # a wrong sum is shown as it is
    doc['totals']['line']['a'] += 0.01
    bad = section(html_of(run_report(tmp_path, doc, name='bad')[1]), 5)
    assert '✗ 실패' in bad[bad.index('<table id="sum-check">'):bad.index('</table>', bad.index('<table id="sum-check">'))]


def test_section_5_has_the_number_rules_inputs_and_warnings(tmp_path):
    doc = sample_doc()
    doc['warnings'] = [{'code': 'w_test', 'detail': 'x'}]
    doc['unavailable'] = [{'scope': 'pair', 'id': 'W1', 'side': 'reference', 'reasons': ['condition_not_computable']}]
    s5 = section(html_of(run_report(tmp_path, doc)[1]), 5)
    for word in ('없음', '0%', '대기', '해당 없음', '확인 불가'):
        assert '<td class="l"><b>%s</b></td>' % word in s5
    assert 'w_test' in s5 and '계산 불가 항목 1 개' in s5 and 'condition_not_computable' in s5 and 'id="products"' in s5
    assert 'id="cond"' in s5 and 'id="cond-all"' in s5 and '전체 보기 (%d 쌍)' % len(doc['pairs']) in s5
    assert '07 보고서 없음' in s5 and '가정 계산' in s5


def test_condition_values_travel_in_the_bundle_unchanged(tmp_path):
    doc = sample_doc()
    data = bundle_of(html_of(run_report(tmp_path, doc)[1]))
    for pair, led in zip(data['pairs'], doc['pairs']):
        assert [item['sizes'] for item in pair['items']] == [item['by_size'] for item in led['items']]
        assert pair['id'] == led['pair_id'] and pair['a'] == led['a'] and pair['b'] == led['b'] and pair['d'] == led['delta']
        assert pair['brk'] == led['breakdown'] and pair['comp'] == led['composition']


# ---- determinism and the real chain ----------------------------------------------------------------------------------------

def test_same_input_twice_gives_identical_bytes_and_no_external_url(tmp_path):
    doc = sample_doc()
    folder = write_ledger(tmp_path / 'led', doc)
    maps = write_maps(tmp_path, doc, ledger_sha(folder), changes=ok_changes())
    pages = []
    for name in ('one', 'two'):
        out = tmp_path / name
        assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None, maps_dir=str(maps)) == 0
        pages.append((out / 'ledger_report.html').read_bytes())
    assert pages[0] == pages[1]
    text = pages[0].decode('utf-8')
    for needle in ('http://', 'https://', '<link', ' src=', 'url(', '@import'):
        assert needle not in text, needle


def build_real(tmp_path, big_fixture):
    code, maps_doc = candidates_run(tmp_path, big_fixture, (3.0, 4.0))
    assert code == 0
    out = tmp_path / 'real_report'
    assert main(['ledger-report', '--ledger-dir', str(tmp_path / 'led'), '--out', str(out), '--maps-dir', str(tmp_path / 'maps')]) == 0
    return out, json.loads((tmp_path / 'led' / 'ledger.json').read_text(encoding='utf-8')), maps_doc


def test_real_chain_report_for_the_lead(tmp_path, big):
    out, led, maps_doc = build_real(tmp_path, big)
    page = html_of(out)
    data = bundle_of(page)
    assert data['schema_version'] == 'ledger_report_v2' and data['ledger']['sha256'] == ledger_sha(tmp_path / 'led')
    for kind in KINDS:
        assert data['kinds'][kind]['breakdown'] == led['kinds'][kind]['item_breakdown']
        assert data['kinds'][kind]['delta'] == led['totals'][kind]['delta']
        assert data['kinds'][kind]['vi_a'] == led['kinds'][kind]['vi_a']
    assert data['top']['changed']['line'] == [p['pair_id'] for p in sorted(
        [p for p in led['pairs'] if p['kind'] == 'line' and p['pct_delta'] is not None], key=lambda p: (-abs(p['pct_delta']), p['pair_id']))[:5]]
    assert _list_ids(page, 'top-changed-line') == data['top']['changed']['line']
    ch = maps_doc['changes']
    assert ch['link_status'] == 'ok' and data['changes']['summary'] == ch['summary'] and data['changes']['pairs'] == ch['pairs']
    s = ch['summary']
    s1 = section(page, 1)
    assert '변경 위치 <b>%d곳</b>(04) · 위치에 연결된 쌍 <b>%d</b> · 미연결 쌍 <b>%d</b>' % (s['roi_count'], s['linked_pairs'], s['unlinked_pairs']) in s1
    names = {}
    from simcompare.compare2_report import KIND_TEXT
    for tag, counts in s['tag_pair_counts'].items():
        assert ('<td class="l">%s</td><td>%d</td><td>%d</td><td>%d</td>' % (KIND_TEXT.get(tag, tag), counts['line'], counts['pixel'],
                                                                         counts['line'] + counts['pixel'])) in s1
    assert [c['pair_id'] for c in data['cards']] == [c['pair_id'] for c in maps_doc['cards']]
    report = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert report['html_bytes'] == len(page.encode('utf-8')) and report['data_bytes'] > 0
    print('\nreal chain report: %s (%d bytes, data %d)' % (out / 'ledger_report.html', report['html_bytes'], report['data_bytes']))


def test_script_stays_inside_es2017(tmp_path):
    page = html_of(run_report(tmp_path, sample_doc())[1])
    script = page[page.rindex('<script>') + 8:page.rindex('</script>')]
    stripped = re.sub(r"'(?:[^'\\]|\\.)*'", "''", script)           # string literals do not count
    for bad in ('?.', '??', '=>', 'class ', 'async ', 'await ', '**', 'BigInt', 'replaceAll', 'let ', 'const ', '`'):
        assert bad not in stripped, bad
    assert 'http' not in script and 'fetch(' not in script and 'XMLHttpRequest' not in script


# ---- Chrome ----------------------------------------------------------------------------------------------------------------

class Dom(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.elements = []                      # (tag, attrs)
        self.root = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'html':
            self.root = attrs
        self.elements.append((tag, attrs))


def dump_dom(page_path, fragment):
    if not os.path.exists(CHROME):
        pytest.skip('Google Chrome is not installed')
    profile = tempfile.mkdtemp(prefix='chrome-t5-')
    try:
        proc = subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--no-first-run', '--user-data-dir=' + profile,
                               '--virtual-time-budget=5000', '--dump-dom', Path(page_path).resolve().as_uri() + fragment],
                              capture_output=True, text=True, timeout=300)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    assert proc.returncode == 0, proc.stderr[-500:]
    dom = Dom()
    dom.feed(proc.stdout)
    return dom, proc.stdout


def selected(dom, attr):
    return sorted(a[attr] for t, a in dom.elements if attr in a and 'sel' in (a.get('class') or '').split())


def test_chrome_hash_states_draw_sections_1_and_5_without_errors(tmp_path):
    doc = sample_doc()
    folder = write_ledger(tmp_path / 'led', doc)
    maps = write_maps(tmp_path, doc, ledger_sha(folder), changes=ok_changes())
    out = tmp_path / 'chrome_out'
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None, maps_dir=str(maps)) == 0
    page = out / 'ledger_report.html'
    for fragment in ('', '#', '#print', '#sel=pair:P2'):
        dom, text = dump_dom(page, fragment)
        assert dom.root.get('data-ready') == '1' and 'data-err' not in dom.root, (fragment, dom.root)
        ids = [a.get('id') for t, a in dom.elements if a.get('id')]
        for needed in ('s1', 's5', 'breakdown', 'sum-check', 'item-check', 'cond', 'bar'):
            assert needed in ids, (fragment, needed)
    # no selection
    dom, text = dump_dom(page, '#')
    assert dom.root['data-sel'] == '' and selected(dom, 'data-pair') == [] and '위에서 쌍을 하나 고르면' in text
    # print opens every details block and draws the whole condition table
    dom, text = dump_dom(page, '#print')
    details = [a for t, a in dom.elements if t == 'details']
    assert details and all('open' in a for a in details)
    assert len([1 for t, a in dom.elements if t == 'tr' and a.get('data-pair')]) >= len(doc['pairs'])
    body = [a for t, a in dom.elements if t == 'body'][0]
    assert body.get('class') == 'print'


def test_chrome_pair_selection_marks_the_row_and_shows_only_that_pairs_conditions(tmp_path):
    doc = sample_doc()
    folder = write_ledger(tmp_path / 'led', doc)
    maps = write_maps(tmp_path, doc, ledger_sha(folder), changes=ok_changes())
    out = tmp_path / 'chrome_out'
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None, maps_dir=str(maps)) == 0
    page = out / 'ledger_report.html'
    dom, text = dump_dom(page, '#sel=pair:P2')
    assert dom.root['data-sel'] == 'pair:P2' and 'data-err' not in dom.root
    assert selected(dom, 'data-pair') == ['P2', 'P2', 'P2']  # the rows in the "most changed" list, the "riskiest" list and the pair list of section 3
    cond = [a for t, a in dom.elements if t == 'tr' and a.get('data-pair') is not None]
    # the visible condition table is the one under #cond: its rows are only P2's items x sizes
    start = text.index('id="cond-table"')
    block = text[start:text.index('</table>', start)]
    rows = re.findall(r'<tr data-pair="([^"]*)"', block)
    p2 = [p for p in doc['pairs'] if p['pair_id'] == 'P2'][0]
    assert set(rows) == {'P2'} and len(rows) == sum(len(i['by_size']) for i in p2['items'])
    assert '쌍 <b>L' in text and 'id="sel-clear"' in text
    clear = [a for t, a in dom.elements if a.get('id') == 'sel-clear'][0]
    assert 'disabled' not in clear
    # the demo flag selects the pair most changed
    dom2, _text2 = dump_dom(page, '#demo')
    demo = bundle_of(page.read_text(encoding='utf-8'))['demo_pair']
    assert dom2.root['data-sel'] == 'pair:' + demo and set(selected(dom2, 'data-pair')) == {demo}


def test_chrome_electrode_layer_and_roi_selections_mark_the_related_pairs(tmp_path):
    doc = sample_doc()
    folder = write_ledger(tmp_path / 'led', doc)
    maps = write_maps(tmp_path, doc, ledger_sha(folder), changes=ok_changes())
    out = tmp_path / 'chrome_out'
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None, maps_dir=str(maps)) == 0
    page = out / 'ledger_report.html'
    dom, _text = dump_dom(page, '#sel=electrode:Vint')
    assert set(selected(dom, 'data-pair')) == {'P3', 'P5'} and 'data-err' not in dom.root          # the two pairs with Vint
    dom, _text = dump_dom(page, '#sel=layer:SD1')
    want = {p['pair_id'] for p in doc['pairs'] if any('SD1' in (i['upper_layer'], i['lower_layer']) for i in p['items'])}
    assert set(selected(dom, 'data-pair')) == want
    dom, _text = dump_dom(page, '#sel=roi:roi:R1')
    assert set(selected(dom, 'data-pair')) == {'P1', 'P3'} and dom.root['data-sel'] == 'roi:roi:R1'
    dom, text = dump_dom(page, '#sel=pair:NOPE&tab=comp&q=abc')
    assert 'data-err' not in dom.root and selected(dom, 'data-pair') == [] and '없는 쌍' in text


def test_chrome_real_chain_page_draws_without_errors(tmp_path, big):
    out, led, maps_doc = build_real(tmp_path, big)
    first = led['pairs'][0]['pair_id']
    for fragment in ('', '#print', '#demo', '#sel=pair:' + first):
        dom, text = dump_dom(out / 'ledger_report.html', fragment)
        assert dom.root.get('data-ready') == '1' and 'data-err' not in dom.root, (fragment, dom.root)
    assert selected(dom, 'data-pair') == sorted([first] * len(selected(dom, 'data-pair')))


# ---- mrep/T6: section 2 (electrode x layer table) and section 4 (electrode ledger, layer x size) -------------------------------

def _el(doc, name):
    return next(r for r in doc['electrodes'] if r['electrode'] == name)


def corr_doc(complete=True, **kwargs):
    pairs = [spec('P1', ['Data', 'ELVDD'], 'line', 0.0120, 0.0150), spec('P2', ['Scan1', 'EM'], 'line', 0.0060, 0.0078),
             spec('P3', ['Anode', 'Vint'], 'pixel', 0.0200, 0.0210, layer='PXL'), spec('P5', ['Scan2', 'Vint'], 'line', 0.0, 0.0065,
                                                                                    present_a=False, change='new_pair', layer='SD1')]
    doc = make_ledger(pairs, cno=[('Anode', 'PXL', 0.0050, 0.0060)], extra_electrodes=('Gone', 'Fresh', 'Shift'), complete=complete, **kwargs)
    for row in doc['electrodes']:
        row['layer_state'] = {layer: 'both' for layer in row['layers_a']}
        row['layer_state_status'] = 'verified' if complete else 'unverified'
        row['moved'] = False if complete else None
    if complete:
        _el(doc, 'Gone').update(status='removed', present_b=False, layer_state={'L1': 'a_only'})
        _el(doc, 'Fresh').update(status='new', present_a=False, layer_state={'PXL': 'b_only'})
        _el(doc, 'Shift').update(layer_state={'L1': 'a_only', 'PXL': 'b_only'}, moved=True)
    else:
        _el(doc, 'Data')['layer_state'] = {'L1': 'a_only'}
    return doc


def s2_row(page, name):
    return re.search(r'<tr data-el="%s"[^>]*data-row="corr">(.*?)</tr>' % re.escape(name), section(page, 2), re.S).group(1)


def s2_cell(page, name, layer):
    return re.search(r'<td class="c ([^"]*)" data-layer="%s"[^>]*>(.*?)</td>' % re.escape(layer), s2_row(page, name), re.S).groups()


def test_section_2_cell_states_summary_and_legend(tmp_path):
    doc = corr_doc()
    page = html_of(run_report(tmp_path, doc)[1])
    s2 = section(page, 2)
    common = sum(1 for r in doc['electrodes'] if r['status'] == 'common')
    assert '공통 <b>%d</b> · 신규 <b>1</b> · 제거 <b>1</b> · 레이어 이동 <b>1</b>' % common in s2
    assert "이름이 바뀐 전극은 '제거 + 신규' 로 보입니다" in s2 and 'id="corr-legend"' in s2
    assert 'id="band-no-dl1"' not in s2 and '확인 불가' not in s2
    assert 'st-both' in s2_cell(page, 'Data', 'L1')[0] and '●' in s2_cell(page, 'Data', 'L1')[1]
    assert 'st-a' in s2_cell(page, 'Gone', 'L1')[0] and '◀' in s2_cell(page, 'Gone', 'L1')[1] and 'st-mv' not in s2_cell(page, 'Gone', 'L1')[0]
    assert 'st-b' in s2_cell(page, 'Fresh', 'PXL')[0] and '▶' in s2_cell(page, 'Fresh', 'PXL')[1]
    assert 'st-mv' in s2_cell(page, 'Shift', 'L1')[0] and '⇄<sub>A</sub>' in s2_cell(page, 'Shift', 'L1')[1]
    assert 'st-mv' in s2_cell(page, 'Shift', 'PXL')[0] and '⇄<sub>B</sub>' in s2_cell(page, 'Shift', 'PXL')[1]
    assert 'st-none' in s2_cell(page, 'Data', 'SD1')[0] and s2_cell(page, 'Data', 'SD1')[1] == ''
    # clicks select the electrode (cells and name) or the layer (column head)
    assert 'data-href="#sel=electrode:Shift"' in s2 and 'data-href="#sel=layer:PXL"' in s2


def test_section_2_without_dl1_says_unknown_once_in_a_band(tmp_path):
    doc = corr_doc(complete=False)
    page = html_of(run_report(tmp_path, doc)[1])
    s2 = section(page, 2)
    assert s2.count('id="band-no-dl1"') == 1 and 'DL1 전극 목록이 없어' in s2
    assert '확인 불가' in s2_cell(page, 'ELVDD', 'SD1')[1] and 'st-unk' in s2_cell(page, 'ELVDD', 'SD1')[0]
    assert '◀' in s2_cell(page, 'Data', 'L1')[1]
    assert '확인 불가 <b>' in s2                                  # the status of the rows is `unverifiable`


def test_electrode_name_colour_is_red_for_up_blue_for_down_with_four_steps(tmp_path):
    doc = corr_doc()
    for name, line, pixel in (('Data', 0.5, None), ('Scan1', -7.0, None), ('Scan2', 30.0, 0.0), ('EM', 0.0, 0.0), ('Anode', None, None),
                              ('Vint', 2.0, -8.0), ('ELVDD', 0.99, 0.0), ('Gone', 5.0, None), ('Fresh', -25.0, 3.0)):
        _el(doc, name)['line']['pct_delta'] = line
        _el(doc, name)['pixel']['pct_delta'] = pixel
    page = html_of(run_report(tmp_path, doc)[1])
    s2 = section(page, 2)

    def klass(name):
        return re.search(r'<tr data-el="%s"[^>]*><td class="l nm([^"]*)"' % re.escape(name), s2).group(1).strip()
    assert klass('Data') == 'hd-up1' and klass('ELVDD') == 'hd-up1'
    assert klass('Scan1') == 'hd-dn3' and klass('Scan2') == 'hd-up4' and klass('Gone') == 'hd-up3'
    assert klass('Vint') == 'hd-dn3'                     # the larger |change| of line and pixel decides: pixel -8 beats line +2
    assert klass('Fresh') == 'hd-dn4'                    # |-25| > |3|
    assert klass('EM') == '' and klass('Anode') == ''    # 0 or null: no colour
    assert 'hd-up3' in s2 and 'hd-dn3' in s2 and '진하기' in s2


def test_search_box_scroll_box_and_names_are_escaped(tmp_path):
    doc = corr_doc()
    _el(doc, 'Shift')['electrode'] = '<i>Shift&'
    page = html_of(run_report(tmp_path, doc)[1])
    s2 = section(page, 2)
    assert 'id="el-q"' in s2 and 'class="scrl"' in s2 and '<i>Shift' not in page.replace('\\u003c', '')
    assert '&lt;i&gt;Shift&amp;' in s2 and '%d / %d' % (len(doc['electrodes']), len(doc['electrodes'])) in s2


def test_section_4_ledger_total_is_the_ledger_sum_not_the_kind_vi(tmp_path):
    doc = corr_doc(crp=0.003, crp_a=1.0, crp_b=2.92)
    t = doc['totals']
    assert t['pixel']['intercept']['a'] != 0 and t['pixel']['crp']['present']
    page = html_of(run_report(tmp_path, doc)[1])
    s4 = section(page, 4)
    total = re.search(r'<tr class="sum" id="ledger-total">(.*?)</tr>', s4, re.S).group(1)
    line_a, line_b = t['line']['items']['a'], t['line']['items']['b']
    pix_a, pix_b = t['pixel']['items']['a'] + t['pixel']['cno']['a'], t['pixel']['items']['b'] + t['pixel']['cno']['b']
    assert '%s → %s' % (ledger_report.raw_pct(line_a), ledger_report.raw_pct(line_b)) in total
    assert '%s → %s' % (ledger_report.raw_pct(pix_a), ledger_report.raw_pct(pix_b)) in total
    # the whole-kind vi (intercept + curve) is another number
    assert ledger_report.raw_pct(doc['kinds']['pixel']['vi_a']) not in total and ledger_report.raw_pct(doc['kinds']['line']['vi_a']) not in total
    assert ledger_report.raw_pp(t['pixel']['items']['delta'] + t['pixel']['cno']['delta']) in total
    assert '곡선 보정' in s4 and 'CRP' in s4 and 'id="ledger-total-note"' in s4
    # one row per electrode; a pixel CNO column only for an electrode with a CNO row
    rows = re.findall(r'<tr data-el="([^"]*)"[^>]*data-row="ledger">(.*?)</tr>', s4, re.S)
    assert sorted(n for n, _ in rows) == sorted(r['electrode'] for r in doc['electrodes'])
    cno_cells = {n: body.rsplit('<td', 1)[1] for n, body in rows}
    assert '해당 없음' not in cno_cells['Anode'] and '해당 없음' in cno_cells['Data']
    assert 'data-href="#sel=electrode:Anode"' in s4


def test_section_4_layer_by_size_tables_per_kind_select_the_layer(tmp_path):
    doc = corr_doc()
    s4 = section(html_of(run_report(tmp_path, doc)[1]), 4)
    for kind in ('line', 'pixel', 'cno'):
        table = re.search(r'<table id="size-%s">(.*?)</table>' % kind, s4, re.S).group(1)
        assert [m for m in re.findall(r'<tr data-layer="([^"]*)"', table)] == [r['layer'] for r in doc['layers'][kind]]
        assert table.count('µm Δ</th>') == len(doc['layers'][kind][0]['by_size'])
    assert 'data-href="#sel=layer:PXL"' in s4


def test_chrome_section_2_and_4_hash_states(tmp_path):
    doc = corr_doc()
    folder = write_ledger(tmp_path / 'led', doc)
    out = tmp_path / 'chrome_out'
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None) == 0
    page = out / 'ledger_report.html'
    for fragment in ('', '#', '#print', '#sel=electrode:Anode', '#sel=layer:PXL', '#q=Sc'):
        dom, text = dump_dom(page, fragment)
        assert dom.root.get('data-ready') == '1' and 'data-err' not in dom.root, (fragment, dom.root)
    dom, text = dump_dom(page, '#sel=electrode:Anode')
    assert dom.root['data-sel'] == 'electrode:Anode' and selected(dom, 'data-el') == ['Anode', 'Anode']      # section 2 and section 4 rows
    start = text.index('id="partner-table"')
    block = text[start:text.index('</table>', start)]
    assert 'Vint' in block and '<td class="l">pixel</td>' in block                                         # Anode's partner Vint (P3)
    dom, text = dump_dom(page, '#sel=layer:PXL')
    marked = selected(dom, 'data-layer')
    assert marked.count('PXL') >= len(doc['electrodes']) + 1 and set(marked) == {'PXL'}                    # head + cells + section 4 row
    dom, text = dump_dom(page, '#q=Sc')
    assert '<span id="el-count" class="muted">2 / %d</span>' % len(doc['electrodes']) in text
    hidden = [a['data-el'] for t, a in dom.elements if t == 'tr' and 'hide' in (a.get('class') or '').split() and a.get('data-row') == 'corr']
    assert len(hidden) == len(doc['electrodes']) - 2 and 'Scan1' not in hidden and 'Scan2' not in hidden
    dom, text = dump_dom(page, '#sel=electrode:Shift&q=shi')
    assert selected(dom, 'data-el') == ['Shift', 'Shift'] and 'data-err' not in dom.root


def test_chrome_unverified_page_draws_without_errors(tmp_path):
    doc = corr_doc(complete=False)
    folder = write_ledger(tmp_path / 'led', doc)
    out = tmp_path / 'chrome_out'
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None) in (0, 3)
    for fragment in ('', '#print', '#sel=electrode:Data'):
        dom, text = dump_dom(out / 'ledger_report.html', fragment)
        assert dom.root.get('data-ready') == '1' and 'data-err' not in dom.root and '확인 불가' in text


# ---- maps (mrep/T7): section 3 map, section 2 layout, section 1 small maps ---------------------------------------------------

def map_changes():
    """04 changes with two ROIs: R1 has rectangles on both maps, R2 lies outside the map window (listed only)."""
    changes = ok_changes()
    changes['rois'][0]['map'] = {'status': 'ok', 'shifted': False, 'clipped': False, 'larger_than_window': False, 'shift_px': [0, 0],
                                 'reference': {'rect_px': [6, 6, 16, 14], 'rect_map': [6, 6, 16, 14], 'clipped': False},
                                 'target': {'status': 'ok', 'reason': None, 'rect_px': [7, 6, 17, 14], 'rect_map': [7, 6, 17, 14], 'clipped': False}}
    second = dict(changes['rois'][0], roi_key='roi:R2', layers=['L1'])
    second['map'] = {'status': 'outside_map', 'shifted': False, 'clipped': False, 'larger_than_window': False, 'reference': None, 'target': None}
    changes['rois'].append(second)
    changes['outside_map'] = ['roi:R2']
    return changes


def map_report(tmp_path, doc=None, name='mv', changes='default', overlay=None, **kwargs):
    doc = doc or sample_doc()
    folder = write_ledger(tmp_path / ('led_' + name), doc)
    maps = write_maps(tmp_path, doc, ledger_sha(folder), name='maps_' + name, full=True, overlay=overlay,
                      changes=map_changes() if changes == 'default' else changes, **kwargs)
    out = tmp_path / ('out_' + name)
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None, maps_dir=str(maps)) in (0, 3)
    return doc, maps, out


def test_every_map_png_is_embedded_once_and_the_bundle_carries_none(tmp_path):
    doc, maps, out = map_report(tmp_path)
    page = html_of(out)
    document = json.loads((maps / 'ledger_maps.json').read_text(encoding='utf-8'))
    files = sorted(e['file'] for side in document['sides'].values() for e in side['maps'])
    assert len(files) >= 8
    assert page.count('iVBORw0KGgo') == len(files) and page.count('id="map-png"') == 1
    block = re.search(r'<script type="application/json" id="map-png">(.*?)</script>', page, re.S).group(1)
    assert sorted(json.loads(block)) == files
    assert 'iVBORw0KGgo' not in bundle_of(page).__repr__()
    data = bundle_of(page)['maps']
    assert [m['f'] for m in data['sides']['A']['maps']] == sorted(m['f'] for m in data['sides']['A']['maps'])
    assert data['layers'] == sorted({i['analysis_layer'] for p in doc['pairs'] for i in p['items']})


def test_without_maps_dir_there_is_no_png_block_and_the_slots_say_so(tmp_path):
    page = html_of(run_report(tmp_path, sample_doc())[1])
    assert 'id="map-png"' not in page and '<canvas' not in re.sub(r'<script.*?</script>', '', page, flags=re.S)
    assert '지도 없음' in section(page, 1) and '지도 없음' in section(page, 2) and '지도 없음' in section(page, 3) and 'id="s3-panel"' in section(page, 3)


def test_views_have_canvases_and_toggles_and_the_missing_share_line(tmp_path):
    doc, maps, out = map_report(tmp_path)
    page = html_of(out)
    for number, view in ((1, 's1'), (2, 's2'), (3, 's3')):
        assert len(re.findall(r'<canvas class="mapcv" data-view="%s"' % view, section(page, number))) == 2, view
    s3 = section(page, 3)
    for key in ('shape', 'defect', 'others', 'roi', 'ov'):
        assert 'data-t="%s"' % key in s3
    assert s3.count('data-mlayer=') == len(bundle_of(page)['maps']['layers']) + 1 and 'id="m-alpha"' in s3 and 'id="m3-legend"' in s3
    assert re.search(r'id="m3-missing"[^>]*>지도에 없는 몫\(A vi 대비\): CNO [^·]+ · 곡선 보정 line [^/]+ / pixel ', s3)
    assert 'id="s3-panel"' in s3 and 'data-slot="s3-panel"' not in s3              # the 40% panel is built by T8


def test_missing_share_adds_the_crp_part_only_when_the_crps_differ(tmp_path):
    same = section(html_of(map_report(tmp_path, name='same')[2]), 3)
    assert 'CRP' not in re.search(r'id="m3-missing".*?</p>', same, re.S).group(0)
    differ = sample_doc()
    differ = make_ledger([spec('P1', ['Data', 'ELVDD'], 'line', 0.0120, 0.0150), spec('P3', ['Anode', 'Vint'], 'pixel', 0.0200, 0.0200, layer='PXL')],
                         cno=[('Anode', 'PXL', 0.0050, 0.0060)], crp=0.002)
    text = section(html_of(map_report(tmp_path, doc=differ, name='differ')[2]), 3)
    assert 'CRP' in re.search(r'id="m3-missing".*?</p>', text, re.S).group(0)


def test_without_dl1_the_shape_toggle_is_off_with_the_reason_and_section_2_shows_no_picture(tmp_path):
    doc, maps, out = map_report(tmp_path, doc=corr_doc(complete=False), el=False)
    page = html_of(out)
    s3, s2 = section(page, 3), section(page, 2)
    assert re.search(r'<button[^>]*data-t="shape"[^>]*disabled', s3) and 'id="shape-reason"' in s3 and 'DL1 이 없어' in s3
    assert 'id="s2-maps-reason"' in s2 and 'DL1 이 없어' in s2 and '<canvas' not in s2
    assert bundle_of(page)['maps']['shape_reason'].startswith('전극·레이어 형상을 그릴 수 없습니다')


def test_overlay_toggle_is_off_with_the_reason_unless_the_maps_say_available(tmp_path):
    doc, maps, out = map_report(tmp_path, name='off')
    s3 = section(html_of(out), 3)
    assert re.search(r'<button[^>]*id="ov-btn"[^>]*disabled', s3) and 'id="ov-reason"' in s3 and 'coords_no_candidates' in s3
    doc, maps, out = map_report(tmp_path, name='on', overlay={'available': True, 'offset_px': [1, 0], 'reason': None})
    s3 = section(html_of(out), 3)
    assert not re.search(r'<button[^>]*id="ov-btn"[^>]*disabled', s3) and 'id="ov-reason"' not in s3


def test_roi_outside_the_map_is_only_in_the_list_and_rois_carry_their_rectangles(tmp_path):
    doc, maps, out = map_report(tmp_path)
    page = html_of(out)
    s3 = section(page, 3)
    block = re.search(r'<ul id="outside-map">(.*?)</ul>', s3, re.S).group(1)
    assert 'roi:R2' in block and 'roi:R1' not in block and 'data-href="#sel=roi:roi:R2"' in block
    rois = {r['roi_key']: r for r in bundle_of(page)['changes']['rois']}
    assert rois['roi:R1']['map']['reference']['rect_map'] == [6, 6, 16, 14] and rois['roi:R2']['map']['status'] == 'outside_map'
    doc, maps, out = map_report(tmp_path, name='nolist', changes=ok_changes())
    assert 'id="outside-map"' not in section(html_of(out), 3)


def test_section_2_cells_choose_the_layer_and_the_electrode(tmp_path):
    doc = corr_doc()
    page = html_of(run_report(tmp_path, doc)[1])
    assert 'data-layer="L1" data-href="#sel=electrode:Data&amp;layer=L1"' in section(page, 2)


def chrome_page(tmp_path, **kwargs):
    doc, maps, out = map_report(tmp_path, **kwargs)
    return doc, out / 'ledger_report.html'


def visible_canvases(dom):
    return [a for t, a in dom.elements if t == 'canvas' and 'mapcv' in (a.get('class') or '') and 'hidden' not in a]


def test_chrome_map_hash_states_draw_with_the_self_check_passed(tmp_path):
    doc, page = chrome_page(tmp_path, overlay={'available': True, 'offset_px': [1, 0], 'reason': None})
    for fragment in ('', '#', '#demo', '#sel=pair:P1', '#sel=electrode:Anode', '#sel=layer:PXL', '#layers=shape', '#layers=none', '#layers=shape,defect,others,roi&layer=L1', '#layer=all'):
        dom, text = dump_dom(page, fragment)
        assert dom.root.get('data-ready') == '1' and 'data-err' not in dom.root, (fragment, dom.root)
        assert dom.root.get('data-map-check') == 'passed', (fragment, dom.root)
        assert int(dom.root['data-map-drawn']) == len(visible_canvases(dom)) >= 4, (fragment, dom.root)
    dom, text = dump_dom(page, '#demo')
    assert dom.root['data-map-layers'] == 'shape,defect' and dom.root['data-sel'].startswith('pair:')
    dom, text = dump_dom(page, '#layers=shape')
    assert dom.root['data-map-layers'] == 'shape'
    dom, text = dump_dom(page, '#layers=none')
    assert dom.root['data-map-layers'] == ''
    dom, text = dump_dom(page, '#layer=PXL')
    assert dom.root['data-map-layer'] == 'PXL'
    dom, text = dump_dom(page, '#sel=layer:PXL')
    assert dom.root['data-map-layer'] == 'PXL'
    dom, text = dump_dom(page, '#layer=all')
    assert dom.root['data-map-layer'] == 'all' and len([1 for t, a in dom.elements if t == 'canvas' and a.get('data-view') == 's3all']) == 2 * len(bundle_of(page.read_text(encoding='utf-8'))['maps']['layers'])


def test_chrome_change_locations_draw_only_the_rectangles_that_have_a_place(tmp_path):
    doc, page = chrome_page(tmp_path)
    dom, text = dump_dom(page, '#layers=shape,roi&layer=L1')
    assert dom.root['data-map-rois'] == '1' and 'data-err' not in dom.root
    dom, text = dump_dom(page, '#layers=shape&layer=L1')
    assert dom.root['data-map-rois'] == '0'
    dom, text = dump_dom(page, '#sel=roi:roi:R1&layers=shape,roi&layer=L1')
    assert dom.root['data-sel'] == 'roi:roi:R1' and dom.root['data-map-rois'] == '1'


def test_chrome_overlay_shows_one_picture_only_when_available(tmp_path):
    doc, page = chrome_page(tmp_path, overlay={'available': True, 'offset_px': [1, 0], 'reason': None})
    dom, text = dump_dom(page, '#ov=1&layer=L1')
    assert dom.root['data-map-ov'] == '1' and 'data-err' not in dom.root and dom.root['data-map-check'] == 'passed'
    shown = [a['data-view'] for a in visible_canvases(dom)]
    assert 'ov' in shown and 's3' not in shown
    doc, page = chrome_page(tmp_path, name='noov')
    dom, text = dump_dom(page, '#ov=1&layer=L1')
    assert dom.root['data-map-ov'] == '0' and 's3' in [a['data-view'] for a in visible_canvases(dom)]


def test_chrome_a_map_whose_pixel_counts_do_not_match_gets_a_warning_and_a_list(tmp_path):
    doc, page = chrome_page(tmp_path, tamper=True)
    dom, text = dump_dom(page, '#sel=pair:P1&layer=L1')
    assert dom.root.get('data-map-check') == 'failed' and 'data-err' not in dom.root
    assert '지도 검사 실패' in text and '번호 ' in text
    assert not [a for a in visible_canvases(dom) if a['data-view'] in ('s3', 's2', 's1')]
    assert re.search(r'<li>쌍 [^<]*Data–ELVDD[^<]*번호 \d+</li>', text)


def test_chrome_without_dl1_still_draws_the_defect_areas_and_the_page_has_no_error(tmp_path):
    doc, page = chrome_page(tmp_path, doc=corr_doc(complete=False), el=False)
    for fragment in ('#demo', '#layers=shape', '#sel=pair:P1'):
        dom, text = dump_dom(page, fragment)
        assert dom.root.get('data-ready') == '1' and 'data-err' not in dom.root and dom.root['data-map-check'] == 'passed', fragment
    dom, text = dump_dom(page, '#demo')
    assert dom.root['data-map-layers'] == 'defect'


# ---- mrep/T8: section 3 panel (two entries, pair list, card tabs, presence-split range tables, 07 links) -----------------------

def comp_doc(extra_pairs=0):
    """sample_doc whose P2 is a composition-change pair with three items: both / A only (gone) / B only (new).
    extra_pairs adds that many line pairs (to test the 20-row default and 'more')."""
    doc = sample_doc()
    if extra_pairs:
        pairs = [spec('P1', ['Data', 'ELVDD'], 'line', 0.0120, 0.0150), spec('P2', ['Scan1', 'EM'], 'line', 0.0060, 0.0078, change='layer_change',
                                                                             comp=(['상하 GAT2–SD2'], ['측면 GAT2'])),
                 spec('P3', ['Anode', 'Vint'], 'pixel', 0.0200, 0.0200, change='no_change', layer='PXL'),
                 spec('P4', ['Gnode', 'Anode'], 'pixel', 0.0090, 0.0050, layer='SD1', failure='vertical_short', lower='PXL'),
                 spec('P5', ['Scan2', 'Vint'], 'line', 0.0, 0.0065, present_a=False, change='new_pair', layer='SD1')]
        pairs += [spec('X%02d' % i, ['Xa%02d' % i, 'Xb%02d' % i], 'line', 0.001 + i * 0.0001, 0.002) for i in range(extra_pairs)]
        doc = make_ledger(pairs, cno=[('Anode', 'PXL', 0.0050, 0.0060)])
    p2 = next(p for p in doc['pairs'] if p['pair_id'] == 'P2')
    base = p2['items'][0]
    gone = dict(base, item_id='P2-gone', failure_type='vertical_short', presence='gone', a=0.0020, b=0.0, delta=-0.0020)
    new = dict(base, item_id='P2-new', failure_type='lateral_open', presence='new', a=0.0, b=0.0030, delta=0.0030)
    p2['items'] = [base, gone, new]
    p2['breakdown'] = {'same_delta': 0.0018, 'new_value': 0.0030, 'gone_value': 0.0020, 'n_both': 1, 'n_new': 1, 'n_gone': 1, 'status': 'ok'}
    return doc


def item_row(item_id, presence, value):
    return {'item_id': item_id, 'presence': presence, 'value': value}


def p2_card(doc, status='matched', rows='default', tabs=('same', 'comp', 'all')):
    ids = [i['item_id'] for i in next(p for p in doc['pairs'] if p['pair_id'] == 'P2')['items']]
    if rows == 'default':
        # one range: the item both products have and the B-only item sit on the same tiles of B1 (so the table must split by
        # `presence`, not by the row's match state)
        rows = [{'id': 'A1', 'side': 'reference', 'range_total': 0.0100, 'match': {'status': 'matched', 'counterpart': 'B1'},
                 'items': [item_row(ids[0], 'both', 0.0040), item_row(ids[1], 'a_only', 0.0020), item_row(ids[2], 'b_only', 0.0)]},
                {'id': 'B1', 'side': 'target', 'range_total': 0.0120, 'match': {'status': 'matched', 'counterpart': 'A1'},
                 'items': [item_row(ids[0], 'both', 0.0050), item_row(ids[1], 'a_only', 0.0), item_row(ids[2], 'b_only', 0.0030)]}]
    return {'kind': 'line', 'pair_id': 'P2', 'rank': 'L2', 'tabs': list(tabs), 'tab_ranks': {t: 1 for t in tabs},
            'match': {'status': status, 'reason': None if status == 'matched' else 'no_common_item'}, 'rows': rows}


def p1_card():
    return {'kind': 'line', 'pair_id': 'P1', 'rank': 'L1', 'tabs': ['same', 'all'], 'tab_ranks': {'same': 1, 'all': 1},
            'match': {'status': 'matched', 'reason': None}, 'rows': []}


def t8_report(tmp_path, name='t8', doc=None, report07=None, **kwargs):
    doc = doc or comp_doc()
    folder = write_ledger(tmp_path / ('led_' + name), doc)
    kwargs.setdefault('changes', map_changes())
    maps = write_maps(tmp_path, doc, ledger_sha(folder), name='maps_' + name, full=True, **kwargs)
    out = tmp_path / ('out_' + name)
    args = {} if report07 is None else {'report07_dir': str(report07)}
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None, maps_dir=str(maps), **args) in (0, 3)
    return doc, out


def store_block(page, pair_id):
    return re.search(r'<div data-range="%s">(.*?)</div>' % re.escape(pair_id), page, re.S).group(1)


def table_rows(block, css):
    start = block.index('class="rt %s"' % css)
    end = block.index('</table>', start)
    return re.findall(r'<tr data-range=.*?</tr>', block[start:end], re.S)


def cells_of(row):
    return [re.sub(r'<[^>]+>', '', c) for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]


def test_range_tables_split_by_presence_and_copy_the_ledger_maps_values(tmp_path):
    doc, out = t8_report(tmp_path, cards=[p2_card(comp_doc()), p1_card()])
    page = html_of(out)
    block = store_block(page, 'P2')
    both, only = table_rows(block, 'rt-both'), table_rows(block, 'rt-only')
    assert len(both) == 1 and len(only) == 2
    # same coordinates A -> B: the item both products have; its range value and the range total, as the maps file says
    c = cells_of(both[0])
    assert c[0] == 'A1 ↔ B1' and c[2] == '같은 좌표 A→B' and c[3] == '0.4000% → 0.5000%' and c[4] == '1.0000% → 1.2000%' and c[5] == '범위 · 장부'
    # one product only: the A-only item on A1 and the B-only item on B1 (the B-only item shares the tiles of the compared item)
    by_label = {cells_of(r)[1]: cells_of(r) for r in only}
    gone, new = by_label['상하 L1'], by_label['CNO L1']
    assert gone[0] == 'A1' and gone[2] == 'A 에만' and gone[3] == 'A 0.2000%' and gone[4] == 'A 1.0000%'
    assert new[0] == 'B1' and new[2] == 'B 에만' and new[3] == 'B 0.3000%' and new[4] == 'B 1.2000%'
    assert '근사 값' in block and '07 · 반복 n' in block and '<th>근사' not in block         # no approximate column (C8)
    assert page.count('data-range="P2"') == 4 and 'data-range="P1"' in page


def test_card_summary_hides_zero_parts_and_tabs_follow_the_cards_tabs(tmp_path):
    doc, out = t8_report(tmp_path, cards=[p2_card(comp_doc(), tabs=('comp', 'all')), p1_card()])
    s3 = section(html_of(out), 3)
    tabs = dict(re.findall(r'<div class="cardtab" data-tab="(\w+)"( hidden)?>', s3) and [(m[0], m[1]) for m in re.findall(r'<div class="cardtab" data-tab="(\w+)"( hidden)?>', s3)])
    assert sorted(tabs) == ['all', 'comp', 'same'] and tabs['same'] == '' and tabs['comp'] == tabs['all'] == ' hidden'
    lis = {m[0]: m[1] for m in re.findall(r'<li data-card="(\w+)"[^>]*>(.*?)</li>', s3, re.S)}
    p1 = lis['P1']
    assert '같은 불량 +0.3000%p' in p1 and '새 방식' not in p1 and '없어진 방식' not in p1           # zero parts are hidden
    comp_tab = s3[s3.index('data-tab="comp"'):s3.index('data-tab="all"')]
    assert 'data-card="P2"' in comp_tab and 'data-card="P1"' not in comp_tab
    assert '같은 불량 +0.1800%p' in comp_tab and '새 방식 +0.3000%' in comp_tab and '없어진 방식 −0.2000%' in comp_tab


def test_card_that_could_not_match_says_why_in_the_same_slot(tmp_path):
    doc, out = t8_report(tmp_path, cards=[p2_card(comp_doc(), status='not_compared', rows=[])])
    block = store_block(html_of(out), 'P2')
    assert '같은 좌표로 비교할 항목이 없습니다' in block and '같은 좌표로 비교하지 못했습니다' in block and 'no_common_item' in block
    assert table_rows(block, 'rt-both') == [] or 'colspan' in block


def test_pair_list_rows_carry_the_filter_values_and_the_unlinked_text(tmp_path):
    doc, out = t8_report(tmp_path, changes=ok_changes(), cards=[p1_card()])
    page = html_of(out)
    s3 = section(page, 3)
    rows = {m.group(1): m.group(0) for m in re.finditer(r'<tr class="plrow[^"]*" data-pair="(\w+)".*?</tr>', s3, re.S)}
    assert sorted(rows) == ['P1', 'P2', 'P3', 'P4', 'P5']
    assert 'data-cat="same new gone"' in rows['P2'] and 'data-cat="new"' in rows['P5'] and 'data-kind="pixel"' in rows['P3']
    assert 'data-tags="shifted reshaped"' in rows['P1'] and 'data-linked="1"' in rows['P1'] and '#1' in rows['P1']
    assert '변경 위치 미연결 — 04 가 고른 상위 위치에 없음' in rows['P2'] and '변경 위치 미연결' not in rows['P1']
    assert '변화 없음' in rows['P3']                                        # no_change pairs are not "unlinked"
    # composition-based text of the changed-composition pair: item +k / -m (+... / -...)
    assert 'item +1 / −1 (+상하 GAT2–SD2 / −측면 GAT2)' in rows['P2']
    assert 'id="pl-count"' in s3 and '결과 5 쌍 · 표시 5' in s3 and 'data-total="5" data-count="5" data-shown="5"' in s3
    for key in ('kind', 'ck', 'cat', 'tag'):
        assert 'data-f="%s"' % key in s3
    assert 'data-f="entry" data-v="loc"' in s3 and 'data-f="entry" data-v="pair"' in s3


def test_location_entry_lists_rois_with_their_linked_pair_counts_and_the_empty_states(tmp_path):
    doc, out = t8_report(tmp_path, changes=map_changes())
    s3 = section(html_of(out), 3)
    rows = re.findall(r'<tr data-roi="([^"]+)" data-href="([^"]+)"[^>]*>(.*?)</tr>', s3, re.S)
    assert [r[0] for r in rows] == ['roi:R1', 'roi:R2']
    assert rows[0][1] == '#sel=roi:roi:R1&amp;entry=loc' and cells_of(rows[0][2])[0] == '#1' and cells_of(rows[0][2])[2] == '2'
    assert '위치 이동' in rows[0][2] and '형상 변경' in rows[0][2] and '연결됨' in rows[0][2]
    assert cells_of(rows[1][2])[2] == '0' and '연결된 쌍 없음(미연결)' in rows[1][2] and '지도 밖' in rows[1][2]
    assert '위치별 합계는 만들지 않습니다' in s3
    # no 04 / no maps folder: the entry says so instead of an empty table
    doc, out = t8_report(tmp_path, name='no04', changes={'link_status': 'no_candidates'})
    assert 'id="roi-none"' in section(html_of(out), 3) and 'id="roi-table"' not in section(html_of(out), 3)
    page = html_of(run_report(tmp_path, comp_doc(), name='nomaps')[1])
    assert '지도 폴더(--maps-dir)가 없어 변경 위치 목록이 없습니다' in section(page, 3) and 'id="pl-table"' in section(page, 3)


def test_section_2_says_observed_basis_when_dl1_is_missing_and_section_4_hides_empty_size_columns(tmp_path):
    page = html_of(run_report(tmp_path, corr_doc(complete=False))[1])
    s2 = section(page, 2)
    assert 'id="corr-observed"' in s2 and '관찰 기준' in s2 and '관찰 기준(DL1 없음)' in s2_cell(page, 'Data', 'L1')[1] + s2_row(page, 'Data')
    assert 'id="corr-observed"' not in section(html_of(run_report(tmp_path, corr_doc(), name='ok')[1]), 2)
    # sizes 1 and 2 µm carry values; 3 µm is 0 in every row, so its column goes and the head says so
    doc = corr_doc()
    for kind in ('line', 'pixel'):
        for row in doc['layers'][kind]:
            row['by_size'].append({'diameter_um': 3.0, 'a': 0.0, 'b': 0.0, 'delta': 0.0})
    s4 = section(html_of(run_report(tmp_path, doc, name='sz')[1]), 4)
    assert 'id="size-hidden-line"' in s4 and '값 없는 크기 1개 숨김' in s4 and '3 µm Δ' not in s4 and '1 µm Δ' in s4


def chrome_t8(tmp_path, name='t8c', **kwargs):
    kwargs.setdefault('cards', [p2_card(comp_doc()), p1_card()])
    doc, out = t8_report(tmp_path, name=name, **kwargs)
    return doc, out / 'ledger_report.html'


def test_chrome_t8_hash_states_have_no_error_and_the_list_counts_follow_the_filters(tmp_path):
    doc, page = chrome_t8(tmp_path, changes=ok_changes())
    expected = {'#': (5, 5), '#kind=pixel': (2, 2), '#kind=line': (3, 3), '#cat=same': (4, 4), '#cat=new': (2, 2), '#cat=gone': (1, 1),
                '#cat=line:same': (2, 2), '#ck=layer_change': (1, 1), '#q=anode': (2, 2), '#q=nothing-like-this': (0, 0),
                '#tag=shifted': (1, 1), '#tag=reshaped': (2, 2), '#cat=tag:reshaped': (2, 2), '#cat=cno': (0, 0), '#kind=line&cat=new': (2, 2)}
    for fragment, (count, shown) in expected.items():
        dom, text = dump_dom(page, fragment)
        assert dom.root.get('data-ready') == '1' and 'data-err' not in dom.root, (fragment, dom.root)
        assert (int(dom.root['data-pl-count']), int(dom.root['data-pl-shown'])) == (count, shown), (fragment, dom.root)
        visible = [a['data-pair'] for t, a in dom.elements if t == 'tr' and 'plrow' in (a.get('class') or '') and 'hide' not in a['class'].split()]
        assert len(visible) == shown, fragment
    for fragment in ('', '#demo', '#sel=pair:P2', '#sel=pair:P1', '#sel=pair:P5', '#sel=roi:roi:R1&entry=loc', '#sel=electrode:Anode', '#sel=layer:PXL',
                     '#tab=comp', '#tab=all', '#entry=loc', '#q=Data', '#cat=new', '#more=1', '#print', '#sel=pair:P2&print'):
        dom, text = dump_dom(page, fragment)
        assert dom.root.get('data-ready') == '1' and 'data-err' not in dom.root, (fragment, dom.root)
    dom, text = dump_dom(page, '#tab=comp')
    assert dom.root['data-tab'] == 'comp'
    shown_tabs = [a['data-tab'] for t, a in dom.elements if t == 'div' and a.get('data-tab') and 'hidden' not in a]
    assert shown_tabs == ['comp']
    dom, text = dump_dom(page, '#entry=loc')
    assert dom.root['data-entry'] == 'loc'
    dom, text = dump_dom(page, '#sel=roi:roi:R1')
    assert dom.root['data-entry'] == 'loc' and 'id="roi-pairs"' in text


def test_chrome_default_shows_20_rows_more_shows_all(tmp_path):
    doc, page = chrome_t8(tmp_path, name='many', doc=comp_doc(extra_pairs=25), changes=ok_changes(), cards=[p2_card(comp_doc(extra_pairs=25))])
    dom, text = dump_dom(page, '#')
    assert dom.root['data-pl-total'] == '30' and dom.root['data-pl-count'] == '30' and dom.root['data-pl-shown'] == '20'
    assert '더 보기 (+10 쌍)' in text
    dom, text = dump_dom(page, '#more=1')
    assert dom.root['data-pl-shown'] == '30' and 'data-err' not in dom.root
    dom, text = dump_dom(page, '#q=xa0&more=1')
    assert dom.root['data-pl-count'] == dom.root['data-pl-shown'] == '10'


def test_chrome_pair_detail_has_the_equation_totals_items_ranges_and_07_links(tmp_path):
    seven = tmp_path / 'compare2_report'
    seven.mkdir()
    (seven / 'report.html').write_text('<html></html>', encoding='utf-8')
    doc, page = chrome_t8(tmp_path, name='d07', report07=seven, changes=map_changes())
    dom, text = dump_dom(page, '#sel=pair:P2')
    pd = text[text.index('id="pd"'):]
    assert 'data-err' not in dom.root and 'data-pd="P2"' in pd
    assert '쌍 Δ = 같은 불량 증감 <b>+0.1800%p</b> + 새 방식 <b>0.3000%</b> − 없어진 방식 <b>0.2000%</b>' in pd
    assert 'id="pd-total"' in pd and 'id="pd-items"' in pd and pd.count('A 에만') >= 2 and 'B 에만' in pd and '양쪽' in pd
    assert 'id="range-tables"' in pd and '같은 좌표 비교 A → B' in pd and '이 제품에만 있는 불량 방식' in pd
    assert '변경 위치 미연결 — 04 가 고른 상위 위치에 없음' in pd                      # P2 is not linked in map_changes()
    assert 'href="../compare2_report/report.html#s6"' in text and '영상: 보고서 미포함' in pd
    dom, text = dump_dom(page, '#sel=pair:P1')
    pd = text[text.index('id="pd"'):]
    assert '관련 쌍 전체값입니다. 위치별 합계는 없습니다.' in pd and '#sel=roi:roi%3AR1&amp;entry=loc' in pd
    dom, text = dump_dom(page, '#sel=roi:roi:R1')
    assert 'href="../compare2_report/report.html#roiroi:R1"' in text
    # without 07
    doc, page2 = chrome_t8(tmp_path, name='n07', changes=map_changes())
    dom, text = dump_dom(page2, '#sel=pair:P2')
    assert '07 보고서 없음' in text[text.index('id="pd-video"'):] and 'report.html#s6' not in text


def test_chrome_pair_without_a_card_or_with_a_dropped_card_says_why(tmp_path):
    dropped = [{'kind': 'line', 'pair_id': 'P1', 'rank': 'L1', 'tabs': ['all'], 'stage': 'target', 'reason': 'card_reserve_failed'}]
    doc, page = chrome_t8(tmp_path, name='nocard', cards=[p2_card(comp_doc())], dropped=dropped, status='partial')
    dom, text = dump_dom(page, '#sel=pair:P3')
    assert 'id="no-card"' in text and '위치 카드 대상이 아닙니다(상위 5)' in text and '같은 불량 증감 상위 · 구성 변경 상위 · 전체 |Δ|' in text
    dom, text = dump_dom(page, '#sel=pair:P1')
    assert 'cards_budget_reduced · 단계 target · card_reserve_failed' in text
    page_text = html_of(page.parent)
    assert 'id="cards-dropped"' in page_text and 'cards_budget_reduced' in page_text


def test_chrome_zero_valued_condition_rows_are_folded_in_section_5(tmp_path):
    doc = comp_doc()
    p = next(p for p in doc['pairs'] if p['pair_id'] == 'P5')
    for size in p['items'][0]['by_size']:
        size['value_a'] = 0.0
        size['value_b'] = 0.0
    page = run_report(tmp_path, doc)[1] / 'ledger_report.html'
    dom, text = dump_dom(page, '#sel=pair:P5')
    assert 'class="zrows" hidden' in text and '값 0 인 크기 2개 펼치기' in text and 'data-err' not in dom.root
    dom, text = dump_dom(page, '#sel=pair:P5&print')
    assert 'class="zrows" hidden' not in text and '값 0 인 크기 2개 접기' in text
    dom, text = dump_dom(page, '#sel=pair:P1')
    assert 'class="zrows"' not in text[text.index('id="cond-table"'):text.index('</table>', text.index('id="cond-table"'))]
