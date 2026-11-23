"""ledger-report (merged report): the five sections, the data bundle and the hash state — sections 1 and 5 (T5), 2 and 4 (T6), the
maps (T7), the section 3 panel (T8) and the report fixes (T11).

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
from test_ledger_report import bundle_of, html_of, make_ledger, new_section, run_report, sample_doc, section, spec, write_ledger
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
    assert 'id="opinion"' in s1                                          # rnarr/T7: the headline is the overall opinion of 1
    assert '곡선 보정' not in s1 and '절편' not in s1                   # the curve line is in the appendix only
    assert '곡선 보정' in section(html_of(out), 5)


def test_opinion_says_the_direction_of_each_kind_and_waits_for_a_pending_kind(tmp_path):
    up_down = make_ledger([spec('A', ['E1', 'E2'], 'line', 0.001, 0.002), spec('B', ['E3', 'E4'], 'pixel', 0.002, 0.001)])
    pending = make_ledger([spec('A', ['E1', 'E2'], 'line', None, 0.002, change='value_pending'), spec('B', ['E3', 'E4'], 'pixel', 0.001, 0.002)])
    s1 = new_section(html_of(run_report(tmp_path, up_down, name='a')[1]), 's1')
    assert 'line vi 는' in s1 and '증가했습니다' in s1 and 'pixel vi 는' in s1 and '감소했습니다' in s1     # rnarr/T12 (M1): 높아졌습니다/낮아졌습니다 -> 증가/감소
    s1 = new_section(html_of(run_report(tmp_path, pending, name='c')[1]), 's1')
    assert 'line vi 는 값이 대기라 비교하지 않습니다' in s1


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
    assert 'data-href' not in table and '#cat=' not in table and '걸러집니다' not in s1       # rexp/T2: the rows are no longer filter links
    assert 'data-go="s5"' in cno_row and 'data-href' not in cno_row                      # CNO is no pair: it points to the electrode ledger (5)


def test_crp_note_only_when_the_two_crps_differ(tmp_path):
    pairs = [spec('A', ['E1', 'E2'], 'line', 0.001, 0.002), spec('B', ['E3', 'E4'], 'pixel', 0.002, 0.003)]
    differ = make_ledger(pairs, crp=0.0005, crp_a=1.0, crp_b=1.2)
    code, out = run_report(tmp_path, differ)
    page = html_of(out)
    s1 = section(page, 1)
    assert 'id="crp-note"' in s1 and 'CRP 차이' in s1 and '부록 참고' in s1 and ledger_report.raw_pp(0.0005) in s1
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
    for kind in KINDS:        # rnarr/T7: 4 lists per kind pair of direction (worse = vi up, better = vi down), by |change| of the pair total
        rows = [p for p in doc['pairs'] if p['kind'] == kind and p['delta'] is not None]
        want_w = [p['pair_id'] for p in sorted((p for p in rows if p['delta'] > 0), key=lambda p: (-abs(p['delta']), p['pair_id']))[:5]]
        want_b = [p['pair_id'] for p in sorted((p for p in rows if p['delta'] < 0), key=lambda p: (-abs(p['delta']), p['pair_id']))[:5]]
        assert _list_ids(page, 'w-%s' % kind) == want_w
        assert _list_ids(page, 'b-%s' % kind) == want_b
    assert doc['pairs'][0]['kind'] == 'line'
    s1 = section(page, 1)
    assert s1.count('data-href="#sel=pair:') == sum(len(_list_ids(page, '%s-%s' % (d, k))) for d in 'wb' for k in KINDS)
    assert 'top-risk' not in page and 'top-changed' not in page


def test_the_change_kind_table_is_gone_from_the_summary(tmp_path):
    code, out = run_report(tmp_path, sample_doc())
    page = html_of(out)
    assert 'kinds-details' not in page and 'id="change-kinds"' not in page and 'id="zero-kinds"' not in page


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


def test_missing_maps_and_partial_each_have_their_own_band_and_the_missing_04_is_not_told(tmp_path):
    page = html_of(run_report(tmp_path, sample_doc(), name='nomaps')[1])
    assert 'id="band-no-maps"' in page and 'id="band-changes"' not in page and '04 결과 없음' not in page
    doc, folder, maps = setup(tmp_path / 'nocand')
    page2 = html_of(ledger_report_run(tmp_path, folder, maps, 'nocand_out'))
    assert 'id="band-no-maps"' not in page2 and 'id="band-changes"' not in page2 and '04 결과 없음' not in page2 and 'id="tags-folded"' not in page2
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
    assert 'id="cond-link"' in s5 and 'id="cond-all"' in s5 and '전체 보기 (%d 쌍)' % len(doc['pairs']) in s5     # rtrim/T2: the selected-pair table moved to section 3
    assert 'id="cond"' not in s5 and 'id="cond-table"' not in html_of(run_report(tmp_path, doc, name='nocond')[1]).split('<script')[0]
    assert '07 보고서 없음' not in s5 and 'id="link07"' not in s5 and 'id="cond-link"' in s5       # rexp/T2: no 07 -> no sentence about it


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
    assert maps_doc['changes']['link_status'] == 'ok' and 'changes' not in data and 'cards' not in data        # rexp/T2: the 04 locations and the cards are not in the report
    s3 = section(page, 3)
    assert 'id="locations"' not in s3 and 'id="tags"' not in s3 and 'id="roi-table"' not in s3 and 'id="pf-table"' in s3
    assert page.count('<tr class="plrow') == len(led['pairs'])                       # the pair finder: one row per pair
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
        for needed in ('s1', 's5', 'breakdown', 'sum-check', 'item-check', 'cond-all', 'checks-details', 'bar'):
            assert needed in ids, (fragment, needed)
    # no selection: the condition table (now in the section 3 pair detail) is not drawn
    dom, text = dump_dom(page, '#')
    assert dom.root['data-sel'] == '' and selected(dom, 'data-pair') == [] and 'id="cond-table"' not in text
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
    assert selected(dom, 'data-pair') == ['P2', 'P2']  # the row in the worse/better top list and the pair list of section 4 (rnarr/T7: 4 lists by direction)
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


def test_chrome_electrode_and_layer_selections_mark_the_related_pairs_and_a_roi_selection_is_ignored(tmp_path):
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
    assert selected(dom, 'data-pair') == [] and dom.root['data-sel'] == '' and 'data-err' not in dom.root     # rexp/T2: the 04 locations are gone
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
    return re.search(r'<td class="c ([^"]*)" data-cell="1" data-layer="%s"[^>]*>(.*?)</td>' % re.escape(layer), s2_row(page, name), re.S).groups()


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


def test_epres_complete_list_with_an_unverifiable_electrode_and_a_value_without_folder(tmp_path):
    """epres/T1 D6: the band names the DL1 folder (not a missing list); a value on a side without the folder is shown, marked."""
    doc = corr_doc()
    _el(doc, 'Vint').update(status='unverifiable', present_a=False, present_b=False, layer_state={}, layer_state_status='unverified',
                            moved=None)
    _el(doc, 'Scan2').update(present_a=False)
    _el(doc, 'Scan2')['line']['a'] = 0.0042
    page = html_of(run_report(tmp_path, doc)[1])
    s2 = section(page, 2)
    assert s2.count('id="band-no-dl1"') == 1 and 'DL1 전극 폴더에 없는 전극 1 개는' in s2
    assert 'DL1 전극 목록이 없어' not in s2 and 'id="corr-observed"' not in s2
    assert page.count('(DL1 폴더 없음)') >= 1


def test_rexp_cno_only_row_has_no_folder_mark_no_band_count_and_no_move(tmp_path):
    """rexp/T1 G1: a CNO-only electrode shows its observed layers, a CNO badge and the one-line note; it is not counted in the
    "DL1 전극 폴더에 없는" band and not as a layer move."""
    doc = corr_doc()
    _el(doc, 'Shift').update(cno_only=True, status='common', present_a=True, present_b=True, layer_state={'L1': 'a_only', 'PXL': 'b_only'},
                             layer_state_status='unverified', moved=None)
    _el(doc, 'Vint').update(status='unverifiable', present_a=False, present_b=False, layer_state={}, layer_state_status='unverified',
                            moved=None)
    page = html_of(run_report(tmp_path, doc)[1])
    s2 = section(page, 2)
    assert 'CNO 전용 1 개 — 레이어 칸은 CNO 가 관측된 층이며, 비어 있어도 없다는 뜻은 아닙니다.' in s2
    assert s2.count('id="band-no-dl1"') == 1 and 'DL1 전극 폴더에 없는 전극 1 개는' in s2     # Vint only
    assert '레이어 이동 <b>0</b>' in s2 and '⇄' not in s2_row(page, 'Shift')
    assert '(DL1 폴더 없음)' not in s2_row(page, 'Shift') and 'CNO</span>' in s2_row(page, 'Shift')
    assert 'CNO 관측 층' in s2_cell(page, 'Shift', 'L1')[1] or 'CNO 관측 층' in s2_row(page, 'Shift')


def test_rexp_without_cno_only_rows_the_note_is_absent(tmp_path):
    page = html_of(run_report(tmp_path, corr_doc())[1])
    assert 'id="note-cno-only"' not in section(page, 2)


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
    for fragment in ('', '#', '#print', '#sel=electrode:Anode', '#sel=layer:PXL', '#eq=Sc'):
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
    dom, text = dump_dom(page, '#eq=Sc')
    assert '<span id="el-count" class="muted">2 / %d</span>' % len(doc['electrodes']) in text
    hidden = [a['data-el'] for t, a in dom.elements if t == 'tr' and 'hide' in (a.get('class') or '').split() and a.get('data-row') == 'corr']
    assert len(hidden) == len(doc['electrodes']) - 2 and 'Scan1' not in hidden and 'Scan2' not in hidden
    dom, text = dump_dom(page, '#sel=electrode:Shift&eq=shi')
    assert selected(dom, 'data-el') == ['Shift', 'Shift'] and 'data-err' not in dom.root
    # R3:4 — the section 2 search (#eq=) does not hide section 4 ledger rows, and the pair search (#q=) does not touch section 2
    hidden4 = [a for t, a in dom.elements if t == 'tr' and a.get('data-row') == 'ledger' and 'hide' in (a.get('class') or '').split()]
    assert hidden4 == []
    dom, text = dump_dom(page, '#q=Sc')
    assert '<span id="el-count" class="muted">%d / %d</span>' % (len(doc['electrodes']), len(doc['electrodes'])) in text


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
    s3 = new_section(page, 's3')
    assert '지도 없음' in section(page, 2) and 'id="band-no-maps"' in page and 'id="s3-detail"' in s3 and 'id="m3-missing"' not in s3


def test_views_have_canvases_and_no_toggles_and_the_missing_share_line(tmp_path):
    doc, maps, out = map_report(tmp_path)
    page = html_of(out)
    assert len(re.findall(r'<canvas class="mapcv" data-view="s2"', section(page, 2))) == 2
    assert '<canvas' not in section(page, 1) and 'id="s1-maps"' not in page and 'data-view="s1"' not in page       # rtrim/T2: no small maps in section 1
    s3 = new_section(page, 's3')
    assert '<canvas' not in s3 and 'data-view="s3"' not in page and 'data-view="s3all"' not in page and 'data-view="ov"' not in page   # rexp/T2: the panel draws its own canvases
    for gone in ('data-t=', 'data-mlayer=', 'id="m-alpha"', 'id="m3-legend"', 'id="ov-btn"', 'id="m3-view"', 'id="m3-all"', '겹쳐 보기', '레이어별로 모두'):
        assert gone not in page, gone
    assert re.search(r'id="m3-missing"[^>]*>지도에 없는 몫\(A vi 대비\): CNO [^·]+ · 곡선 보정 line [^/]+ / pixel ', s3)
    assert 'id="s3-detail"' in s3 and 'id="s3-panel"' not in page


def test_missing_share_adds_the_crp_part_only_when_the_crps_differ(tmp_path):
    same = new_section(html_of(map_report(tmp_path, name='same')[2]), 's3')
    assert 'CRP' not in re.search(r'id="m3-missing".*?</p>', same, re.S).group(0)
    differ = sample_doc()
    differ = make_ledger([spec('P1', ['Data', 'ELVDD'], 'line', 0.0120, 0.0150), spec('P3', ['Anode', 'Vint'], 'pixel', 0.0200, 0.0200, layer='PXL')],
                         cno=[('Anode', 'PXL', 0.0050, 0.0060)], crp=0.002)
    text = new_section(html_of(map_report(tmp_path, doc=differ, name='differ')[2]), 's3')
    assert 'CRP' in re.search(r'id="m3-missing".*?</p>', text, re.S).group(0)


def test_without_dl1_section_2_shows_no_picture_and_the_bundle_says_why(tmp_path):
    doc, maps, out = map_report(tmp_path, doc=corr_doc(complete=False), el=False)
    page = html_of(out)
    s2 = section(page, 2)
    assert 'id="s2-maps-reason"' in s2 and 'DL1 이 없어' in s2 and '<canvas' not in s2
    assert bundle_of(page)['maps']['shape_reason'].startswith('전극·레이어 형상을 그릴 수 없습니다')


def test_the_04_block_and_the_overlay_of_ledger_maps_never_reach_the_page_or_the_bundle(tmp_path):
    for name, overlay in (('off', None), ('on', {'available': True, 'offset_px': [1, 0], 'reason': None})):
        doc, maps, out = map_report(tmp_path, name=name, overlay=overlay)
        page = html_of(out)
        for gone in ('id="ov-btn"', 'id="ov-reason"', 'id="outside-map"', 'data-roi=', 'roi:R1', 'roi:R2', '변경 위치'):
            assert gone not in page, gone
        assert 'changes' not in bundle_of(page) and 'overlay' not in bundle_of(page)['maps']


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
    for fragment in ('', '#', '#demo', '#sel=pair:P1', '#sel=electrode:Anode', '#sel=layer:PXL', '#layer=L1', '#layers=none&ov=1&sel=roi:roi:R1'):
        dom, text = dump_dom(page, fragment)
        assert dom.root.get('data-ready') == '1' and 'data-err' not in dom.root, (fragment, dom.root)
        assert dom.root.get('data-map-check') == 'passed', (fragment, dom.root)
        assert int(dom.root['data-map-drawn']) == len([a for a in visible_canvases(dom) if a['data-view'] == 's2']) == 2, (fragment, dom.root)   # the section 2 map only
    dom, text = dump_dom(page, '#layer=PXL')
    assert dom.root['data-map-layer'] == 'PXL'
    dom, text = dump_dom(page, '#sel=layer:PXL')
    assert dom.root['data-map-layer'] == 'PXL'


def test_chrome_a_map_whose_pixel_counts_do_not_match_gets_a_warning_and_a_list(tmp_path):
    doc, page = chrome_page(tmp_path, tamper=True)
    dom, text = dump_dom(page, '#sel=pair:P1&layer=L1')
    assert dom.root.get('data-map-check') == 'failed' and 'data-err' not in dom.root
    assert '지도 검사 실패' in text and '번호 ' in text
    assert not [a for a in visible_canvases(dom) if a['data-view'] in ('s3', 's2', 's1')]
    assert re.search(r'<li>쌍 [^<]*Data–ELVDD[^<]*번호 \d+</li>', text)


def test_chrome_without_dl1_the_detail_panel_still_draws_the_pair_areas_and_the_page_has_no_error(tmp_path):
    doc, page = chrome_page(tmp_path, doc=corr_doc(complete=False), el=False)
    for fragment in ('#demo', '#sel=pair:P1'):
        dom, text = dump_dom(page, fragment)
        assert dom.root.get('data-ready') == '1' and 'data-err' not in dom.root and dom.root['data-map-check'] == 'passed', fragment
    dom, text = dump_dom(page, '#sel=pair:P1')
    assert dom.root['data-dp-drawn'] == '2' and int(dom.root['data-dp-pixels'].split(',')[0]) > 0


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


def test_the_pair_finder_has_one_row_per_pair_with_the_name_the_kind_the_candidate_and_the_delta(tmp_path):      # (d)
    doc, out = t8_report(tmp_path)
    page = html_of(out)
    s4 = new_section(page, 's4')
    finder = s4[s4.index('id="pair-find"'):]
    rows = {m.group(1): m.group(0) for m in re.finditer(r'<tr class="plrow[^"]*" data-pair="(\w+)".*?</tr>', finder, re.S)}
    assert sorted(rows) == sorted(p['pair_id'] for p in doc['pairs']) == ['P1', 'P2', 'P3', 'P4', 'P5']       # one row per pair
    assert 'data-total="5" data-count="5" data-shown="5"' in finder and '결과 5 쌍 · 표시 5' in finder
    for pid, row in rows.items():
        assert 'data-href="#sel=pair:%s"' % pid in row and 'data-scroll="explore"' in row and 'data-name="' in row
        cells = cells_of(row)
        assert len(cells) == 4                                                  # 쌍 · 종류 · 원인 후보 · Δ
    assert 'item +1 / −1 (+상하 GAT2–SD2 / −측면 GAT2)' in rows['P2'] and cells_of(rows['P3'])[1] == 'pixel'
    for gone in ('id="pl-filters"', 'id="pl-more-filters"', 'data-f="kind"', 'data-f="ck"', 'data-f="cat"', 'data-f="tag"', 'data-f="sort"', 'id="pl-q"'):
        assert gone not in page, gone
    assert 'id="pf-q"' in finder and 'id="pf-more"' in finder


def test_the_pair_finder_names_the_cause_candidate_of_each_pair(tmp_path):
    doc = comp_doc()
    p0, p1 = doc['pairs'][0], doc['pairs'][3]
    from test_ledger_report_narrative import finish, mk
    one = mk(doc, 0, 'pair', {'line': -0.01}, layer=p0['items'][0]['analysis_layer'])
    finish(doc, [one])
    page = ledger_report.render_html(doc, 'a' * 64, 30, 30)[0]
    finder = new_section(page, 's4')
    finder = finder[finder.index('id="pair-find"'):]
    row0 = re.search(r'<tr class="plrow[^"]*" data-pair="%s".*?</tr>' % p0['pair_id'], finder, re.S).group(0)
    assert ledger_report.bundle_title(one) in row0.replace('&amp;', '&')
    assert cells_of(re.search(r'<tr class="plrow[^"]*" data-pair="%s".*?</tr>' % p1['pair_id'], finder, re.S).group(0))[2] == '—'    # no candidate: a dash


def test_the_detail_panel_data_has_the_pairs_of_every_candidate_and_the_items_of_every_pair(tmp_path):         # (a)
    from test_ledger_report_narrative import _two_layer_page
    doc, b, p0, p1, l0, l1, page = _two_layer_page(tmp_path)
    data = bundle_of(page.read_text(encoding='utf-8'))
    unit = next(u for u in data['bundles'] if u['id'] == b['id'])
    assert set(unit['pairs']) == {p0['pair_id'], p1['pair_id']} and unit['cno'] == 1               # ② the pairs of the candidate (+ the CNO count)
    assert unit['ty'] == '전극 삭제' and set(unit['nk']) == {'line', 'pixel'} and unit['nk']['line'] == pytest.approx(b['kinds']['line']['net'])   # ① head
    by_id = {p['id']: p for p in data['pairs']}
    for pid, (net, absolute, n_items, positions) in unit['pairs'].items():
        items = by_id[pid]['items']
        assert items and all(0 <= n < len(items) for n in positions) and n_items == len(positions)
        for item in items:                                                        # ③ every item carries the label, presence, A, B, delta and the region counts slot
            assert {'label', 'pres', 'a', 'b', 'd', 'oc', 'af', 'bf'} <= set(item)
            assert item['oc'] is None or len(item['oc']) == 2


def test_the_item_region_counts_come_from_the_occurrence_of_ledger_maps(tmp_path):
    doc = comp_doc()
    page = ledger_report.render_html(doc, 'a' * 64, 30, 30)[0]
    assert all(item['oc'] is None for p in bundle_of(page)['pairs'] for item in p['items'])         # no maps: a dash in the panel
    folder = write_ledger(tmp_path / 'led', doc)
    maps = write_maps(tmp_path, doc, ledger_sha(folder), full=True)
    saved = json.loads((maps / 'ledger_maps.json').read_text(encoding='utf-8'))
    first = doc['pairs'][0]['items'][0]['item_id']
    suppressed = doc['pairs'][1]['items'][0]['item_id']
    saved['occurrence'] = {'diameter_um': 5.0, 'items': [
        {'item_id': first, 'reference': {'n': 4}, 'target': {'n': 7}, 'compare': {'kind': 'delta'}},
        {'item_id': suppressed, 'reference': {'n': 1}, 'target': {'n': 1}, 'compare': {'kind': 'suppressed', 'reason': 'pending'}}]}
    (maps / 'ledger_maps.json').write_text(json.dumps(saved, sort_keys=True), encoding='utf-8')
    out = tmp_path / 'oc_out'
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None, maps_dir=str(maps)) == 0
    items = {i['id']: i for p in bundle_of(html_of(out))['pairs'] for i in p['items']}
    assert items[first]['oc'] == [4, 7] and items[suppressed]['oc'] is None and items[doc['pairs'][2]['items'][0]['item_id']]['oc'] is None


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
    assert 'id="size-hidden-line"' in s4 and 'Δ 가 모두 0 인 크기 1개 숨김' in s4 and '3 µm Δ' not in s4 and '1 µm Δ' in s4


def chrome_t8(tmp_path, name='t8c', **kwargs):
    doc, out = t8_report(tmp_path, name=name, **kwargs)
    return doc, out / 'ledger_report.html'


def test_the_script_handles_the_pair_and_unit_selections_of_the_detail_panel(tmp_path):       # (b)
    page = html_of(run_report(tmp_path, comp_doc())[1])
    script = page[page.rindex('<script>') + 8:page.rindex('</script>')]
    for needle in ("sel.t==='pair'", "sel.t==='unit'", 'st.kv.cand', "'cand'", 'function drawDetail', 'function detailOf', 'function paintDetail',
                   '#sel=pair:', "target(id)", "'s3-detail'", 'data-dp'):
        assert needle in script, needle
    for gone in ('drawRois', 'toggleLayers', 'ovB', 'drawPanel', 'roiDetail', 'rangeBlock', 'm-alpha', 'data-mlayer'):
        assert gone not in script, gone


def test_chrome_detail_panel_hash_states_have_no_error_and_the_finder_counts_follow_the_search(tmp_path):
    doc, page = chrome_t8(tmp_path)
    expected = {'#': (5, 5), '#q=anode': (2, 2), '#q=nothing-like-this': (0, 0), '#q=SCAN': (2, 2), '#kind=pixel': (5, 5)}       # a removed filter key is ignored
    for fragment, (count, shown) in expected.items():
        dom, text = dump_dom(page, fragment)
        assert dom.root.get('data-ready') == '1' and 'data-err' not in dom.root, (fragment, dom.root)
        assert (int(dom.root['data-pf-count']), int(dom.root['data-pf-shown'])) == (count, shown), (fragment, dom.root)
        visible = [a['data-pair'] for t, a in dom.elements if t == 'tr' and 'plrow' in (a.get('class') or '') and 'hide' not in a['class'].split()]
        assert len(visible) == shown, fragment
    for fragment in ('', '#demo', '#sel=pair:P2', '#sel=pair:P1', '#sel=pair:P5', '#sel=electrode:Anode', '#sel=layer:PXL', '#q=Data', '#more=1', '#print',
                     '#sel=pair:P2&print', '#sel=roi:roi:R1&entry=loc', '#tab=comp&cat=new&ck=layer_change&ov=1&layers=none&to=explore'):
        dom, text = dump_dom(page, fragment)
        assert dom.root.get('data-ready') == '1' and 'data-err' not in dom.root, (fragment, dom.root)
    dom, text = dump_dom(page, '#')
    assert 'id="dp-empty"' in text and dom.root['data-dp-pair'] == '' and dom.root['data-dp-unit'] == ''       # the empty prompt


def test_chrome_default_shows_20_rows_more_shows_all(tmp_path):
    doc, page = chrome_t8(tmp_path, name='many', doc=comp_doc(extra_pairs=25))
    dom, text = dump_dom(page, '#')
    assert dom.root['data-pf-total'] == '30' and dom.root['data-pf-count'] == '30' and dom.root['data-pf-shown'] == '20'
    assert '더 보기 (+10 쌍)' in text
    assert len(re.findall(r'<tr class="plrow', text)) == 30                       # (d) one row per pair, 20 shown
    dom, text = dump_dom(page, '#more=1')
    assert dom.root['data-pf-shown'] == '30' and 'data-err' not in dom.root
    dom, text = dump_dom(page, '#q=xa0&more=1')
    assert dom.root['data-pf-count'] == dom.root['data-pf-shown'] == '10'


def test_chrome_pair_detail_has_the_equation_the_item_table_and_the_places(tmp_path):
    doc, page = chrome_t8(tmp_path, name='dp')
    dom, text = dump_dom(page, '#sel=pair:P2')
    assert 'data-err' not in dom.root and dom.root['data-dp-pair'] == 'P2'
    panel = text[text.index('id="s3-detail"'):]
    assert '쌍 Δ = 같은 불량 증감 <b>+0.1800%p</b> + 새 방식 <b>0.3000%</b> − 없어진 방식 <b>0.2000%</b>' in panel
    rows = re.findall(r'<tr(?: class="[^"]*")?><td class="l">([^<]*)</td>.*?</tr>', panel[panel.index('id="dp-items"'):panel.index('</table>', panel.index('id="dp-items"'))], re.S)
    assert len(rows) == 3                                                           # ③ the three items of P2
    items = panel[panel.index('id="dp-items"'):panel.index('</table>', panel.index('id="dp-items"'))]
    assert items.count('없어짐') == 1 and items.count('새로 생김') == 1 and items.count('증감') == 1
    assert '연결 영역 A → B' in items and 'id="cond-detail"' in panel and 'id="dp-layer"' in panel and 'id="dp-zoom"' in panel
    assert 'id="dcv-A"' in panel and 'id="dcv-B"' in panel                         # ④ A | B side by side
    assert dom.root['data-dp-drawn'] == '2' and dom.root['data-dp-zoom'] == '1' and dom.root['data-map-check'] == 'passed'
    assert 'id="ov-btn"' not in text and '겹쳐 보기' not in text


def test_chrome_zoom_follows_the_pixels_of_the_pair_and_the_button_shows_the_whole_map(tmp_path):
    doc, page = chrome_t8(tmp_path, name='zoom')
    dom, text = dump_dom(page, '#sel=pair:P1')
    x0, y0, x1, y1 = [int(v) for v in dom.root['data-dp-box'].split(',')]
    assert 0 <= x0 < x1 <= 40 and 0 <= y0 < y1 <= 40 and (x1 - x0, y1 - y0) != (40, 40)       # the crop is the box of the pair pixels + a margin
    assert int(dom.root['data-dp-pixels'].split(',')[0]) > 0
    script = ("setTimeout(function(){document.getElementById('dp-zoom').click();setTimeout(function(){},200);},400);")
    dom2, text2 = run_in_page(page, '#sel=pair:P1', script, tag='rexp_zoom')
    assert dom2.root['data-dp-zoom'] == '0' and dom2.root['data-dp-box'] == '0,0,40,40' and 'data-err' not in dom2.root       # [전체 보기]


def test_chrome_a_pair_without_pixels_on_the_layer_says_so(tmp_path):
    doc, page = chrome_t8(tmp_path, name='nopx', doc=corr_doc(complete=False), el=False)
    dom, text = dump_dom(page, '#sel=pair:P5')
    assert 'data-err' not in dom.root and dom.root['data-dp-pair'] == 'P5'
    assert '이 레이어에 영역 없음' in text or int(dom.root['data-dp-pixels'].split(',')[1]) > 0     # P5 exists in B only: A has no region on the layer


def test_without_maps_the_places_say_no_map_and_the_table_shows_dashes(tmp_path):
    page = run_report(tmp_path, comp_doc(), name='nomaps2')[1] / 'ledger_report.html'
    dom, text = dump_dom(page, '#sel=pair:P2')
    assert 'data-err' not in dom.root and dom.root['data-dp-pair'] == 'P2'
    assert 'id="dp-nomap"' in text and '지도 없음' in text[text.index('id="dp-nomap"'):][:120]
    assert 'id="dp-items"' in text and 'id="dcv-A"' not in text


def test_chrome_zero_valued_condition_rows_are_folded_in_the_pair_detail(tmp_path):
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


# ---- mrep/T11: report fixes (R3:1 - R3:10) ----------------------------------------------------------------------------------

def run_in_page(page_path, fragment, script, tag='t11'):
    """Chrome --dump-dom of a copy of the page with `script` appended (it runs after the report script, so it can change the hash,
    click and read the attributes the report sets)."""
    page = Path(page_path)
    copy = page.parent / ('%s_copy.html' % tag)
    text = page.read_text(encoding='utf-8')
    copy.write_text(text.replace('</body>', '<script>' + script + '</script></body>'), encoding='utf-8')
    return dump_dom(copy, fragment)


def test_chrome_hash_cannot_inject_attributes_or_markup(tmp_path):
    """R3:1 — the hash is untrusted: `sel=<anything>:<id>` with a kind that is not pair/electrode/layer/roi selects nothing, and the
    id never becomes an attribute NAME (it only fills quoted, escaped values)."""
    doc, page = chrome_t8(tmp_path, changes=ok_changes())
    bad = ('onfocus', 'onerror', 'onclick', 'onload', 'autofocus', 'data-inj')
    fragments = ['#sel=x%20autofocus%20tabindex%3D0%20onfocus%3Dwindow.name%3D1%20data-inj%3D1%20y%3Az',
                 '#sel=pair:%22%20onfocus%3D%22x%20data-inj%3D%221',
                 '#sel=layer:%22%3E%3Cimg%20src%3Dx%20onerror%3Dwindow.name%3D1%3E',
                 '#sel=electrode:%3Cb%20data-inj%3D1%3E%3Cimg%20src%3Dx%20onerror%3D1%3E',
                 '#sel=roi:%22%20autofocus%20onfocus%3D%22x', '#sel=__proto__:x', '#sel=constructor:x', '#sel=pair:constructor',
                 '#sel=pair:__proto__', '#sel=electrode:__proto__&entry=pair&cat=%22%3E%3Cb%3E&tag=%22%20onfocus%3D1&layer=%22%20onfocus%3D1']
    for fragment in fragments:
        dom, text = dump_dom(page, fragment)
        assert 'data-err' not in dom.root, (fragment, dom.root)
        assert dom.root.get('data-ready') == '1', fragment
        for tag, attrs in dom.elements:
            for name in attrs:
                assert name not in bad, (fragment, tag, name)
        assert not [a for t, a in dom.elements if t == 'img' and a.get('src') == 'x'], fragment
    dom, text = dump_dom(page, fragments[0])
    assert dom.root['data-sel'] == ''                                                         # a non-whitelisted kind selects nothing
    dom, text = dump_dom(page, '#sel=layer:PXL')
    assert dom.root['data-sel'] == 'layer:PXL' and dom.root['data-dp-pair'] == '' and 'id="dp-empty"' in text      # a layer / electrode selection has no detail panel
    dom, text = dump_dom(page, '#sel=electrode:Anode')
    assert dom.root['data-sel'] == 'electrode:Anode' and dom.root['data-dp-pair'] == '' and 'id="dp-empty"' in text


def test_chrome_07_link_with_a_scheme_is_never_an_anchor(tmp_path):
    doc, page = chrome_t8(tmp_path, changes=map_changes())
    text = page.read_text(encoding='utf-8')
    hacked = text.replace('"report07": {"href": null, "status": "none"}', '"report07": {"href": "javascript:alert(1)//", "status": "linked"}')
    if hacked == text:
        hacked = re.sub(r'"report07":\s*\{[^}]*\}', '"report07": {"href": "javascript:alert(1)//", "status": "linked"}', text)
    assert hacked != text
    bad = page.parent / 'hacked.html'
    bad.write_text(hacked, encoding='utf-8')
    dom, out = dump_dom(bad, '#sel=pair:P2')
    assert 'data-err' not in dom.root and not [a for t, a in dom.elements if t == 'a' and (a.get('href') or '').startswith('javascript')]
    assert '07 보고서 없음' not in out


def absent_doc():
    pairs = [spec('Z1', ['Zed', 'Eff'], 'line', 0.0, 0.0, change='no_change'),
             spec('N1', ['New', 'Eff'], 'line', 0.0, 0.0030, present_a=False, change='new_pair'),
             spec('G1', ['Gone', 'Eff'], 'line', 0.0020, 0.0, present_b=False, change='gone_pair'),
             spec('W1', ['Wait', 'Eff'], 'line', None, 0.0010, change='value_pending'),
             spec('F1', ['Fil', 'Eff'], 'line', 0.0, 0.0010, filtered=(True, False)),
             spec('PX', ['Pix', 'Eff2'], 'pixel', 0.0040, 0.0050)]
    doc = make_ledger(pairs, cno=[('Pix', 'PXL', 0.001, 0.002)])
    for row in doc['electrodes']:
        if row['electrode'] == 'New':
            row['present_a'] = False
        if row['electrode'] == 'Gone':
            row['present_b'] = False
    return doc


def ledger_cells(page, name):
    m = re.search(r'<tr data-el="%s"[^>]*data-row="ledger">(.*?)</tr>' % re.escape(name), page, re.S)
    assert m, name
    return [re.sub(r'<[^>]+>', '', c) for c in re.findall(r'<td[^>]*>(.*?)</td>', m.group(1), re.S)]


def test_section_4_ledger_tells_absent_not_applicable_and_zero_apart(tmp_path):
    """R3:2 — 없음 (not in that product), 해당 없음 (no pair of that kind), 0% (there, value 0), 대기 (there, no value)."""
    page = html_of(run_report(tmp_path, absent_doc())[1])
    zed = ledger_cells(page, 'Zed')                       # line-only electrode
    assert zed[1] != '해당 없음' and zed[3] == '해당 없음' and zed[4] == '해당 없음' and zed[5] == '해당 없음'      # no pixel pair and no CNO
    new = ledger_cells(page, 'New')                       # not in A
    assert new[1].startswith('없음 → ') and '없음' not in new[1].split('→')[1]
    gone = ledger_cells(page, 'Gone')                     # not in B
    assert gone[1].endswith('→ 없음') and not gone[1].startswith('없음')
    pix = ledger_cells(page, 'Pix')                       # has a pixel pair and CNO, and no line pair
    assert pix[1] == '해당 없음' and pix[2] == '해당 없음' and pix[3] != '해당 없음' and '→' in pix[5]
    eff2 = ledger_cells(page, 'Eff2')                     # pixel pair, no CNO
    assert eff2[3] != '해당 없음' and eff2[5] == '해당 없음'


def test_chrome_partner_list_and_pair_detail_tell_absent_pending_zero_and_distance_filter_apart(tmp_path):
    """R3:2 — section 2 partner list (없음), section 3 pair detail (없음 / 대기 / 거리 필터로 0)."""
    out = run_report(tmp_path, absent_doc())[1]
    page = out / 'ledger_report.html'
    dom, text = dump_dom(page, '#sel=electrode:Eff')
    assert 'data-err' not in dom.root
    block = text[text.index('id="partner-table"'):]
    block = block[:block.index('</table>')]
    row = {}
    for m in re.finditer(r'<tr data-pair="([^"]+)"[^>]*>(.*?)</tr>', block, re.S):
        row[m.group(1)] = re.sub(r'<[^>]+>', '', m.group(2).replace('</td>', '|'))
    ab = {pid: text.split('|')[3] for pid, text in row.items()}                              # the "A → B" cell (the 4th cell)
    assert ab['N1'].startswith('없음 → ') and ab['G1'].endswith('→ 없음')
    assert ab['W1'].startswith('대기 → ') and '없음' not in ab['W1'] and ab['Z1'] == '0% → 0%'
    def item_table(pair_id):
        dom, text = dump_dom(page, '#sel=pair:%s' % pair_id)
        assert 'data-err' not in dom.root and dom.root['data-dp-pair'] == pair_id
        table = text[text.index('id="dp-items"'):]
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', table[:table.index('</table>')]))
    assert item_table('F1').count('거리 필터로 0') == 1                                          # the A side only
    assert '없음' in item_table('N1')
    assert '대기' in item_table('W1')
    z1 = item_table('Z1')
    assert '0%' in z1 and '거리 필터로 0' not in z1
    data = bundle_of(page.read_text(encoding='utf-8'))
    assert [p for p in data['pairs'] if p['id'] == 'F1'][0]['items'][0]['af'] is True


def test_chrome_the_section_2_map_redraws_only_for_map_state(tmp_path):
    """R3:3 — the pair search and more do not redraw the section 2 map; a selection does; the detail panel (a pair choice) does not either."""
    doc, page = chrome_t8(tmp_path, changes=ok_changes())
    script = ("var R=document.documentElement,g=function(){return Number(R.getAttribute('data-map-gen'));},o={};"
              "setTimeout(function(){o.g0=g();location.hash='#q=an&more=1';setTimeout(function(){o.g1=g();"
              "location.hash='#q=an&more=1&sel=electrode:Anode';setTimeout(function(){o.g2=g();"
              "R.setAttribute('data-gens',[o.g0,o.g1,o.g2].join(','));},200);},200);},200);")
    dom, text = run_in_page(page, '#', script)
    g0, g1, g2 = [int(x) for x in dom.root['data-gens'].split(',')]
    assert 'data-err' not in dom.root and g0 >= 1
    assert g1 == g0                      # search / more are not map states
    assert g2 == g1 + 1                  # a selection is a map state


def test_chrome_search_keys_are_separate_eq_for_section_2_and_q_for_section_3(tmp_path):
    """R3:4"""
    doc, page = chrome_t8(tmp_path, changes=ok_changes())
    dom, text = dump_dom(page, '#eq=Sc&q=anode')
    assert dom.root['data-pf-count'] == '2'                                                    # the pair search is q
    m = re.search(r'id="el-count" class="muted">(\d+) / (\d+)<', text)
    assert m.group(1) != m.group(2)                                                            # the section 2 search is eq
    hidden4 = [a for t, a in dom.elements if t == 'tr' and a.get('data-row') == 'ledger' and 'hide' in (a.get('class') or '').split()]
    assert hidden4 == []                                                                       # section 4 is not filtered by the section 2 search
    dom, text = dump_dom(page, '#q=Sc')
    m = re.search(r'id="el-count" class="muted">(\d+) / (\d+)<', text)
    assert m.group(1) == m.group(2)                                                           # the pair search does not touch section 2


def test_chrome_a_top_pair_of_section_1_opens_the_detail_panel_and_cno_points_to_the_electrode_ledger(tmp_path):
    """R3:5 (rexp/T2) — the rows of the breakdown are plain numbers; a pair of the top lists selects it (#sel=pair:) and the panel follows."""
    doc, page = chrome_t8(tmp_path, changes=ok_changes())
    script = ("var R=document.documentElement,o={};"
              "setTimeout(function(){document.querySelector('#w-line li[data-pair]').click();setTimeout(function(){o.h1=location.hash;"
              "o.pair=R.getAttribute('data-dp-pair');R.setAttribute('data-nav',JSON.stringify(o));},300);},300);")
    dom, text = run_in_page(page, '#', script)
    assert 'data-err' not in dom.root
    nav = json.loads(dom.root['data-nav'])
    first = re.search(r'id="w-line".*?<li data-pair="([^"]+)"', text, re.S).group(1)
    assert nav['h1'] == '#sel=pair%3A' + first and nav['pair'] == first                      # the hash carries the pair; the panel shows it
    dom, text = dump_dom(page, '#cat=pixel:cno')
    assert 'data-err' not in dom.root and dom.root['data-dp-pair'] == ''                       # the removed filter hash does nothing
    s1 = section(page.read_text(encoding='utf-8'), 1)
    assert re.search(r'<tr data-cat="cno">.*?data-go="s5"', s1, re.S)
    assert 'data-scroll="explore"' in s1


def test_chrome_a_new_selection_drops_the_candidate_and_item_of_the_old_one_and_keeps_the_search(tmp_path):
    """R4:3 (rexp/T2) — a pair of the finder is chosen after #q=…&sel=pair:…&it=…: q stays, it and cand go."""
    doc, page = chrome_t8(tmp_path, changes=ok_changes())
    item = doc['pairs'][0]['items'][0]['item_id']
    script = ("var R=document.documentElement,o={};"
              "setTimeout(function(){document.querySelector('#pf-table tr[data-pair=\"P3\"]').click();setTimeout(function(){o.h=location.hash;"
              "o.pair=R.getAttribute('data-dp-pair');R.setAttribute('data-nav',JSON.stringify(o));},300);},300);")
    dom, text = run_in_page(page, '#q=an&more=1&sel=pair:P1&it=%s' % item, script)
    assert 'data-err' not in dom.root
    nav = json.loads(dom.root['data-nav'])
    assert 'sel=pair%3AP3' in nav['h'] and 'q=an' in nav['h'] and 'it=' not in nav['h'] and 'cand=' not in nav['h'] and nav['pair'] == 'P3'


def test_report07_link_survives_a_path_without_a_relative_form(tmp_path, monkeypatch):
    """R3:6 — Windows: os.path.relpath raises ValueError for another drive; the report is made, without the link."""
    seven = tmp_path / 'compare2_report'
    seven.mkdir()
    (seven / 'report.html').write_text('<html></html>', encoding='utf-8')

    def boom(*args, **kwargs):
        raise ValueError("path is on mount 'C:', start on mount 'D:'")
    monkeypatch.setattr(ledger_report.os.path, 'relpath', boom)
    code, out = run_report(tmp_path, sample_doc(), report07_dir=str(seven))
    assert code == 0
    page = html_of(out)
    assert bundle_of(page)['report07']['status'] == 'missing' and bundle_of(page)['report07']['href'] is None
    assert json.loads((out / 'report.json').read_text(encoding='utf-8'))['report07'] == 'missing'
    assert '다른 드라이브라 상대 링크를 만들 수 없습니다' in page and '07 폴더에서 직접 여세요' in page          # R4:1 — the reason is shown


def test_report07_href_is_percent_encoded_and_a_scheme_path_gets_no_link(tmp_path, monkeypatch):
    """R4:2"""
    seven = tmp_path / 'r #1 100%'
    seven.mkdir()
    (seven / 'report.html').write_text('<html></html>', encoding='utf-8')
    code, out = run_report(tmp_path, sample_doc(), report07_dir=str(seven))
    assert code == 0
    page = html_of(out)
    href = bundle_of(page)['report07']['href']
    assert href == '../r%20%231%20100%25/report.html' and '#' not in href and ' ' not in href
    assert 'href="../r%20%231%20100%25/report.html"' in page
    assert (out / href.replace('%20', ' ').replace('%23', '#').replace('%25', '%')).resolve().is_file()
    monkeypatch.setattr(ledger_report.os.path, 'relpath', lambda *a, **k: 'javascript:x/report.html')
    code, out = run_report(tmp_path, sample_doc(), report07_dir=str(seven))
    page = html_of(out)
    assert bundle_of(page)['report07']['status'] == 'missing' and 'href="javascript' not in page and 'id="link07-a"' not in page


def test_old_wording_and_dead_code_are_gone():
    """R3:7"""
    assert not hasattr(ledger_report, 'NEXT_STEP') and not hasattr(ledger_report, '_slot')
    from simcompare import cli
    text = Path(cli.__file__).read_text(encoding='utf-8')
    line = text[text.index('"ledger-report", add_help=False'):]
    line = line[:line.index('lrep.add_argument("--maps-dir"')]
    assert '다음 단계' not in line and line.count('현재 표시 개수에 쓰이지 않음') == 2 and '호환용' in line
    assert '다음 단계' not in Path(ledger_report.__file__).read_text(encoding='utf-8')


def test_crp_difference_without_a_value_is_waiting_not_zero(tmp_path):
    """R3:9"""
    doc = make_ledger([spec('A1', ['A', 'B'], 'pixel', 0.001, 0.002)], crp=0.0005, crp_a=1.0, crp_b=1.2)
    doc['totals']['pixel']['crp']['delta'] = None
    page = html_of(run_report(tmp_path, doc)[1])
    note = re.search(r'id="crp-note"[^>]*>(.*?)</p>', page, re.S).group(1)
    assert 'CRP 차이 대기' in note and '0.0000%p' not in note
    line = re.search(r'id="crp-line">(.*?)</p>', page, re.S).group(1)
    assert '차이 대기' in line and '0.0000%p' not in line




# ---- mscale/T1: A and B maps at the same physical scale, scale bar, unit window outline -----------------------------------------

def scaled_report(tmp_path, name='sc', pitch_a=3.0, pitch_b=2.5, whole=False, source='config', with_scale=True):
    """A full-map fixture (every picture is 40 x 40 px) whose ledger_maps.json carries pitch / region / window: A = 40 * pitch_a um wide."""
    doc = comp_doc()
    folder = write_ledger(tmp_path / ('led_' + name), doc)
    maps = write_maps(tmp_path, doc, ledger_sha(folder), name='maps_' + name, full=True, changes=map_changes())
    mdoc = json.loads((maps / 'ledger_maps.json').read_text(encoding='utf-8'))
    if with_scale:
        mdoc['window'] = {}
        for role, pitch in (('reference', pitch_a), ('target', pitch_b)):
            mdoc['sides'][role]['frame']['pitch_um'] = pitch
            mdoc['sides'][role]['region'] = {'origin_px': [0, 0], 'size_px': [40, 40], 'block_px': 1}
            size = [40, 40] if whole else [20, 20]
            origin = [0, 0] if whole else [10, 10]
            mdoc['window'][role] = {'origin_px': origin, 'size_px': size, 'unit_um': [size[0] * pitch, size[1] * pitch], 'unit_source': source}
        mdoc['options']['range_um'] = 10.0
    (maps / 'ledger_maps.json').write_text(json.dumps(mdoc, sort_keys=True), encoding='utf-8')
    out = tmp_path / ('out_' + name)
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None, maps_dir=str(maps)) in (0, 3)
    return out / 'ledger_report.html'


SCALE_PROBE = ("setTimeout(function(){var R=document.documentElement,o=[],c=document.querySelectorAll('canvas.mapcv'),i;"
               "for(i=0;i<c.length;i++){if(c[i].hidden)continue;var b=c[i].getBoundingClientRect(),p=c[i].parentNode.parentNode;"
               "o.push([c[i].getAttribute('data-view'),c[i].getAttribute('data-role'),b.width,c[i].getAttribute('data-phys-w-um')||'',"
               "p.getAttribute('data-scale-ratio')||'',c[i].getAttribute('data-win')||''].join('|'));}"
               "R.setAttribute('data-probe',o.join(';'));},400);")


def probe(page, fragment):
    dom, text = run_in_page(page, fragment, SCALE_PROBE, tag='scp')
    assert 'data-err' not in dom.root, (fragment, dom.root)
    rows = [r.split('|') for r in dom.root.get('data-probe', '').split(';') if r]
    assert rows, fragment
    return dom, text, rows


def pair_widths(rows):
    out = {}
    for view, role, width, phys, ratio, win in rows:
        out.setdefault(view, []).append((role, float(width), phys, ratio))
    return out


def test_chrome_maps_share_one_physical_scale_in_section_2(tmp_path):
    page = scaled_report(tmp_path)
    dom, text, rows = probe(page, '#')
    widths = pair_widths(rows)
    assert set(widths) == {'s2'}                                                               # rexp/T2: the section 3 map is the detail panel (own canvases)
    for fragment in ('#', '#print', '#layer=L1'):
        dom, text, rows = probe(page, fragment)
        for view, items in pair_widths(rows).items():
            if view == 'ov':
                continue
            by_role = {}
            for role, width, phys, ratio in items:
                by_role.setdefault(role, []).append((width, phys, ratio))
            assert set(by_role) == {'A', 'B'}, (fragment, view)
            for (wa, pa, ra), (wb, pb, rb) in zip(by_role['A'], by_role['B']):
                assert (pa, pb) == ('120', '100') and ra == rb == '0.833', (fragment, view, pa, pb, ra, rb)
                assert abs(wb / wa - 100.0 / 120.0) < 0.02, (fragment, view, wa, wb)


def test_scale_bar_caption_and_window_outline(tmp_path):
    page = scaled_report(tmp_path, source='derived')
    dom, text, rows = probe(page, '#')
    caps = re.findall(r'<span class="scap">(\d[^<]*)</span>', text)
    # R1:4 (rtrim/T2): "derived" is said once, "(frame 에서 유도)"; R1:2: the region size is region.size_px x pitch
    assert any('영역 120 × 120 µm · unit 창 60 µm(frame 에서 유도) + 여백 10 µm' in c for c in caps), caps[:3]
    assert any('영역 100 × 100 µm · unit 창 50 µm(frame 에서 유도)' in c for c in caps)
    assert not any('(유도)' in c or 'frame 에서 유도)' in c and c.count('유도') > 1 for c in caps)
    assert any(c.startswith('20 µm · ') for c in caps)                                      # about a fifth of 120 um
    assert all(r[5] == '1' for r in rows if r[0] == 's2')                                   # the unit window is outlined on A and B
    cfg = scaled_report(tmp_path, name='cfg', source='config')
    dom, text, rows = probe(cfg, '#')
    caps = re.findall(r'<span class="scap">(\d[^<]*)</span>', text)
    assert caps and any('unit 창 60 µm(설정)' in c for c in caps) and not any('frame 에서 유도' in c for c in caps)


def test_the_unit_window_outline_is_drawn_on_the_section_2_map_and_the_04_note_is_gone(tmp_path):
    page = scaled_report(tmp_path, name='rnote', source='derived')
    dom, text, rows = probe(page, '#')
    assert all(r[5] == '1' for r in rows if r[0] == 's2')                                   # the outline is still drawn
    text = Path(page).read_text(encoding='utf-8')
    assert 'loc-note' not in text and 'roi-ref' not in text and 'id="m3-legend"' not in text
    assert "g.fillText('unit'" in text and '#ffd21f' in text


def test_window_equal_to_the_frame_draws_no_outline_and_says_so(tmp_path):
    page = scaled_report(tmp_path, whole=True)
    dom, text, rows = probe(page, '#')
    assert all(r[5] == '0' for r in rows)
    caps = re.findall(r'<span class="scap">(\d[^<]*)</span>', text)
    assert caps and all('창 = frame 전체' in c and 'unit 창' not in c for c in caps)


def test_without_scale_data_the_maps_are_drawn_as_before(tmp_path):
    page = scaled_report(tmp_path, with_scale=False)
    dom, text = run_in_page(page, '#', SCALE_PROBE, tag='scn')
    rows = [r.split('|') for r in dom.root.get('data-probe', '').split(';') if r]
    assert rows and all(r[3] == '' and r[5] == '0' for r in rows) and not re.search(r'<span class="scap">\d', text)
    assert dom.root.get('data-map-check') == 'passed' and 'data-err' not in dom.root


def test_chrome_map_click_coordinates_survive_the_resize(tmp_path):
    page = scaled_report(tmp_path)
    script = ("setTimeout(function(){var R=document.documentElement,cv=document.querySelector('canvas.mapcv[data-view=\"s2\"][data-role=\"B\"]'),"
              "b=cv.getBoundingClientRect(),x=b.left+b.width*0.5,y=b.top+b.height*0.25,"
              "q=[Math.floor((x-b.left)*cv.width/b.width),Math.floor((y-b.top)*cv.height/b.height)];"
              "R.setAttribute('data-pick',q.join(','));R.setAttribute('data-cvw',cv.width+','+cv.height);"
              "R.setAttribute('data-box',b.width.toFixed(1)+','+b.height.toFixed(1));},400);")
    dom, text = run_in_page(page, '#', script, tag='scc')
    assert dom.root['data-pick'] == '20,10' and dom.root['data-cvw'] == '40,40'            # the canvas pixels are unchanged
    w, h = [float(x) for x in dom.root['data-box'].split(',')]
    assert abs(w - h) < 1.0                                                                 # the aspect ratio is kept


def test_scaled_report_is_byte_identical_for_the_same_input(tmp_path):
    one = scaled_report(tmp_path, name='same').read_bytes()
    two_dir = tmp_path / 'again'
    two_dir.mkdir()
    two = scaled_report(two_dir, name='same').read_bytes()
    assert one == two
    side = bundle_of(one.decode('utf-8'))['maps']['sides']['A']['sc']
    assert side['p'] == 3.0 and side['wo'] == [10, 10] and side['whole'] is False and side['us'] == 'config'


# ---- rtrim/T2: merged report trim (small maps, tags, condition table, folds, change-kind counts, B sort) -------------------------

def verified_doc(doc=None):
    """sample_doc whose electrode rows say the layer correspondence is verified and unchanged (nothing new / removed / moved)."""
    doc = doc or sample_doc()
    for row in doc['electrodes']:
        row['status'] = 'common'
        row['layer_state'] = {layer: 'both' for layer in row['layers_a']}
        row['layer_state_status'] = 'verified'
        row['moved'] = False
    return doc


def test_rtrim_section_1_has_no_small_maps_no_tag_table_and_the_new_wording(tmp_path):
    doc, maps, out = map_report(tmp_path, changes=ok_changes())
    page = html_of(out)
    s1 = section(page, 1)
    assert '<canvas' not in s1 and 'id="s1-maps"' not in page and 'data-view="s1"' not in page and '작은 A·B 지도' not in page
    assert 'id="tags"' not in page                                                          # rexp/T2: no tag table anywhere
    assert '변화의 구성' in s1 and '무엇 때문인가' not in page and '때문' not in page.split('<script')[0]
    assert 'id="w-line"' in s1 and 'data-href="#sel=pair:' in s1                              # the ranked list rows still open the exploration
    assert 'id="change-kinds"' not in page and 'id="kinds-details"' not in page              # the change-kind table is gone (G0)
    assert 'topPairs()' in page and "view==='s1'" not in page and 'o.tops' not in page         # the script has no small-map drawing or click code left
    nomaps = html_of(run_report(tmp_path, sample_doc(), name='nomaps')[1])
    assert 'id="tags-folded"' not in nomaps


def plrow_order(dom):
    return [a['data-pair'] for t, a in dom.elements if t == 'tr' and 'plrow' in (a.get('class') or '').split() and 'hide' not in (a.get('class') or '').split()]


def test_the_pair_finder_has_one_fixed_order_and_no_filters_or_b_sort(tmp_path):
    """rexp/T2 — the change-kind buttons, the B column and the B sort of the old pair list are gone; the finder keeps the default order."""
    doc = sample_doc()
    page = run_report(tmp_path, doc)[1] / 'ledger_report.html'
    text = page.read_text(encoding='utf-8')
    assert '<th>B 값</th>' not in text and 'data-f="sort"' not in text and 'data-f="ck"' not in text and 'data-brank' not in text
    default = [p['pair_id'] for p in sorted(doc['pairs'], key=ledger_report._pair_sort_key)]
    for fragment in ('#', '#sort=b', '#sort=x', '#sort=%22%20onfocus%3D1', '#sort=b&kind=pixel'):
        dom, dom_text = dump_dom(page, fragment)
        assert 'data-err' not in dom.root and plrow_order(dom) == default, fragment


def test_rtrim_check_details_are_folded_unless_a_check_is_not_passed(tmp_path):
    doc = sample_doc()
    s5 = section(html_of(run_report(tmp_path, doc, name='fine')[1]), 5)
    assert '<details id="checks-details">' in s5 and '모두 통과' in s5
    assert s5.index('<details id="checks-details"') < s5.index('<table id="sum-check">') < s5.index('<table id="item-check">') < s5.index('</details>')
    bad = sample_doc()
    bad['checks']['item_breakdown']['kind']['line']['status'] = 'failed'
    assert '<details id="checks-details" open>' in section(html_of(run_report(tmp_path, bad, name='badkind')[1]), 5)
    wrong = sample_doc()
    wrong['totals']['line']['a'] += 0.01                                  # the sum check of the page fails
    assert '<details id="checks-details" open>' in section(html_of(run_report(tmp_path, wrong, name='badsum')[1]), 5)
    pending = sample_doc()
    pending['checks']['item_breakdown']['kind']['pixel']['status'] = 'not_evaluated'
    assert '<details id="checks-details" open>' in section(html_of(run_report(tmp_path, pending, name='pend')[1]), 5)
    assert '<details id="checks-details" open>' in section(html_of(run_report(tmp_path, make_ledger(
        [spec('A1', ['A', 'B'], 'line', 0.001, 0.002)], status='partial', reasons=['ledger_totals_check_failed']), name='part',
        execution={'command': 'ledger', 'status': 'partial', 'exit_code': 3})[1]), 5)
    assert 'id="trust"' in section(html_of(run_report(tmp_path, doc, name='fine2')[1]), 1)           # the trust line of section 1 stays


def test_chrome_rtrim_print_opens_the_folded_checks_and_correspondence(tmp_path):
    page = run_report(tmp_path, verified_doc())[1] / 'ledger_report.html'
    dom, text = dump_dom(page, '#print')
    details = {a.get('id'): a for t, a in dom.elements if t == 'details' and a.get('id')}
    assert 'open' in details['checks-details'] and 'open' in details['corr-details'] and 'data-err' not in dom.root


def test_rtrim_correspondence_table_is_folded_only_when_nothing_differs(tmp_path):
    same = verified_doc()
    s2 = section(html_of(run_report(tmp_path, same, name='same')[1]), 2)
    assert '<details id="corr-details"><summary class="lead" id="corr-summary">' in s2 and 'id="el-partners"' in s2
    summary = s2[s2.index('id="corr-summary"'):s2.index('</summary>')]
    assert '신규 <b>0</b> · 제거 <b>0</b> · 레이어 이동 <b>0</b>' in summary
    assert s2.index('<details id="corr-details"') < s2.index('<table id="corr-table">') < s2.index('</details>') < s2.index('id="el-partners"')
    for name, change in (('new', lambda r: r.update(status='new')), ('removed', lambda r: r.update(status='removed')),
                         ('moved', lambda r: r.update(moved=True)), ('unverified', lambda r: r.update(layer_state_status='unverified'))):
        doc = verified_doc()
        change(doc['electrodes'][0])
        s2 = section(html_of(run_report(tmp_path, doc, name=name)[1]), 2)
        assert 'id="corr-details"' not in s2 and '<p class="lead" id="corr-summary">' in s2 and '<table id="corr-table">' in s2, name
        assert 'id="el-partners"' in s2, name                                    # the partner list stays where it was in every case
    assert 'id="el-partners"' in section(html_of(run_report(tmp_path, sample_doc(), name='plain')[1]), 2)


def test_chrome_rtrim_condition_table_is_the_last_block_of_the_detail_panel(tmp_path):
    doc = sample_doc()
    page = run_report(tmp_path, doc)[1] / 'ledger_report.html'
    dom, text = dump_dom(page, '#sel=pair:P2')
    assert 'data-err' not in dom.root and 'id="cond"' not in text
    start = text.index('id="s3-detail"')
    detail = text[start:text.index('</section>', start)]
    assert detail.index('id="dp-items"') < detail.index('id="cond-detail"') < detail.index('id="cond-table"')
    assert '이물 크기' in detail[detail.index('id="cond-detail"'):] and 'density 레이어' in detail[detail.index('id="cond-detail"'):]
    cond = [a for t, a in dom.elements if a.get('id') == 'cond-detail']
    assert cond and 'open' not in cond[0]                                                  # folded by default
    s5_start = text.index('<section id="s5"')
    assert 'id="cond-table"' not in text[s5_start:text.index('</section>', s5_start)] and 'id="cond-link"' in text[s5_start:]
    dom, text = dump_dom(page, '#')
    assert not [a for t, a in dom.elements if a.get('id') in ('cond-detail', 'cond-table')]
    dom, text = dump_dom(page, '#sel=pair:P2&print')
    assert 'open' in [a for t, a in dom.elements if a.get('id') == 'cond-detail'][0]


def test_chrome_rtrim_map_click_selects_the_same_thing_on_the_narrowed_b_map(tmp_path):
    """R1:1 — a real click event on the B map (drawn narrower than A) picks the same electrode as the same relative point of the A map."""
    page = scaled_report(tmp_path)
    script = ("setTimeout(function(){var R=document.documentElement,res=[],cvs={},f,fx,fy,r,j;"
              "['A','B'].forEach(function(r){cvs[r]=document.querySelector('canvas.mapcv[data-view=\"s2\"][data-role=\"'+r+'\"]');});"
              "R.setAttribute('data-wa',cvs.A.getBoundingClientRect().width.toFixed(1));R.setAttribute('data-wb',cvs.B.getBoundingClientRect().width.toFixed(1));"
              "for(fy=0.05;fy<1;fy+=0.1){for(fx=0.05;fx<1;fx+=0.1){var row=[];['A','B'].forEach(function(r){var b=cvs[r].getBoundingClientRect();"
              "location.hash='#layer=L1';cvs[r].dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:b.left+b.width*fx,clientY:b.top+b.height*fy}));"
              "row.push(location.hash.replace(/&?layer=L1/,''));});res.push(row.join('>'));}}"
              "R.setAttribute('data-clicks',res.join(';'));},700);")
    dom, text = run_in_page(page, '#layer=L1', script, tag='rtc')
    assert 'data-err' not in dom.root
    assert float(dom.root['data-wb']) < float(dom.root['data-wa']) * 0.9                  # B is really narrower
    rows = [r.split('>') for r in dom.root['data-clicks'].split(';')]
    picked = [r for r in rows if r[0].startswith('#sel=')]
    assert picked, rows[:5]                                                                # some clicks selected something
    assert all(a == b for a, b in rows), [r for r in rows if r[0] != r[1]][:3]            # A and B give the same selection at the same relative point


# ---- rtrim/T3: particle size range line --------------------------------------------------------------------------------

def _range_line(page):
    return _texts(re.search(r'<p class="muted size-range" id="size-range">(.*?)</p>', section(page, 1), re.S).group(1)).strip()


def test_rtrim_size_range_line_for_a_listed_filter_all_sizes_and_missing_info(tmp_path):
    doc = sample_doc()
    doc['inputs']['diameter_filter'] = {'mode': 'listed', 'diameters_um': [7, 3, 5], 'observed_diameters_um': [3, 5],
                                        'not_observed_um': [7], 'formula_basis': 'diameter_subset'}
    page = html_of(run_report(tmp_path, doc, name='a')[1])
    assert _range_line(page) == '이물 크기 범위: 선택 3, 5, 7 µm (관찰 안 됨: 7 µm) (전체 크기 값은 07 1절에서 봅니다)'
    assert '선택 3, 5, 7 µm' in section(page, 5) and 'id="inputs-size-range"' in section(page, 5)
    many = sample_doc()
    many['inputs']['diameter_filter'] = {'mode': 'listed', 'diameters_um': list(range(1, 11)), 'not_observed_um': []}
    assert '선택 1–10 µm, 10개' in _range_line(html_of(run_report(tmp_path, many, name='m')[1]))
    allsizes = sample_doc()
    allsizes['inputs']['diameter_filter'] = {'mode': 'all'}
    page = html_of(run_report(tmp_path, allsizes, name='b')[1])
    assert _range_line(page) == '이물 크기 범위: 모든 크기'
    assert '모든 크기' in section(page, 5)


def test_rtrim_old_ledger_without_the_key_is_not_rejected(tmp_path):
    doc = sample_doc()
    doc['inputs'].pop('diameter_filter', None)
    code, out = run_report(tmp_path, doc, name='old')
    assert code == 0
    assert _range_line(html_of(out)) == '이물 크기 범위 정보 없음 — 08 을 다시 실행하면 나옵니다'
