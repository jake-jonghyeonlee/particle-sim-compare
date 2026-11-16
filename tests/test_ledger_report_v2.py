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
                              capture_output=True, text=True, timeout=120)
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
    assert selected(dom, 'data-pair') == ['P2', 'P2']        # the row in the "most changed" and in the "riskiest" list
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
