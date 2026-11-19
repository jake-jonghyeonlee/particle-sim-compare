"""ledger-report, rnarr/T7: the seven sections (1 summary, 2 correspondence, 3 cause candidates, 4 results, 5 distribution, 6 resolution
difference, appendix), the sentences and their suppression, the links and the hash whitelist, the layer order.

The fixture is a real compare2 -> ledger chain (test_ledger_summary.real_ledger) whose `summary` is edited to the states of the mockup
(same resolution, other conditions differ, rebase waiting, verdict omitted, partial, conflict). Chrome tests skip when Chrome is missing."""
import copy
import json
import re
from html.parser import HTMLParser

import pytest

from simcompare import compare2_report, ledger_report
from test_ledger_report import html_of, make_ledger, new_section, run_report, sample_doc, spec, write_ledger
from test_ledger_report_v2 import CHROME, corr_doc, dump_dom, map_report, ok_changes  # noqa: F401
from test_ledger_summary import real_ledger

KINDS = ('line', 'pixel')


@pytest.fixture(scope='module')
def real(tmp_path_factory):
    doc, _c2 = real_ledger(tmp_path_factory.mktemp('rn7'), b=dict(group='auto', res=[7, 9], glass=40.0))
    return doc


def render(doc, maps=None):
    return ledger_report.render_html(doc, 'a' * 64, 30, 30, maps=maps)[0]


class Visible(HTMLParser):
    """The text a reader sees without opening anything: script, style and the inside of <details> (except its <summary>) are left out."""

    def __init__(self):
        HTMLParser.__init__(self)
        self.text, self.skip, self.det, self.sum_ = [], 0, 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        elif tag == 'details':
            self.det += 1
        elif tag == 'summary':
            self.sum_ += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip -= 1
        elif tag == 'details':
            self.det -= 1
        elif tag == 'summary':
            self.sum_ -= 1

    def handle_data(self, data):
        if not self.skip and (self.det == 0 or self.sum_ > 0):
            self.text.append(data)


def visible(page):
    p = Visible()
    p.feed(page)
    return re.sub(r'\s+', ' ', ' '.join(p.text))


def all_text(fragment):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', re.sub(r'<(script|style).*?</\1>', '', fragment, flags=re.S)))


def variant(real, edit):
    doc = copy.deepcopy(real)
    edit(doc)
    return doc


def cards_of(doc, kind):
    """The cards of a ranking as the T7 page shows them (rnarr/T11: the main direction first, then the opposite one)."""
    rk = doc['summary']['bundles']['ranking'][kind]
    if rk.get('main') not in ('increase', 'decrease'):
        return []
    other = 'decrease' if rk['main'] == 'increase' else 'increase'
    return list(rk[rk['main']]['cards']) + list(rk[other]['cards'])


def clear_cards(doc, kind):
    rk = doc['summary']['bundles']['ranking'][kind]
    for name in ('increase', 'decrease'):
        rk[name]['cards'] = []


# ---- the seven sections, the frame ------------------------------------------------------------------------------------------

def test_seven_sections_in_order_with_a_head_and_a_pointer_each(real):
    page = render(real)
    ids = [m for m in re.findall(r'<section id="(\w+)">', page)]
    assert ids == ['s1', 's2', 's3', 's4', 's5', 's6', 'sA']
    for sid in ids:
        s = new_section(page, sid)
        assert s.startswith('<section id="%s"><p class="q">' % sid) and '<h2>' in s and 'class="nextl"' in s
    assert 'id="purpose"' in page and 'id="explore"' in new_section(page, 's4')
    text = all_text(page)
    for old in '①②③④⑤⑥⑦':
        assert old not in text, old
    assert '때문' not in text


def test_section_1_has_no_table_and_names_the_cause_candidates_with_links(real):
    s1 = new_section(render(real), 's1')
    assert '<table' not in s1
    assert 'id="opinion"' in s1 and '검산' in s1 and '변화량(증가와 감소의 크기를 모두 더한 값)' in s1
    ids = re.findall(r'href="#unit=(bd-[0-9a-f]+)&amp;to=s3"', s1)
    assert ids and all(i in cards_of(real, 'line') + cards_of(real, 'pixel') for i in ids)


def test_design_and_resolution_numbers_are_only_in_section_6(real):
    def edit(d):
        seg = d['summary']['design']['line']['segments']
        seg['design'], seg['resolution'] = 0.0023, 0.012345      # numbers that no other section can show by chance
        d['summary']['design']['line']['negligible'] = []
    page = render(variant(real, edit))
    needle = ledger_report.pp_txt(0.012345)
    assert needle in new_section(page, 's6')
    for sid in ('s1', 's3', 's4', 's5', 'sA'):
        assert needle not in new_section(page, sid), sid
    assert needle not in new_section(page, 's2')


def test_the_default_screen_has_no_04_roi(tmp_path):
    doc, maps, out = map_report(tmp_path, changes=ok_changes())
    page = html_of(out)
    text = visible(page)
    for word in ('형상 태그', '변경 위치 #', '#1 ', 'roi-table'):
        assert word not in text, word
    assert '<details id="roi-ref">' in page and 'id="roi-table"' in page            # it is there, folded
    assert 'data-f="entry"' not in page


def test_old_inputs_render_and_say_how_to_rerun(tmp_path):
    page = html_of(run_report(tmp_path, sample_doc())[1])
    for sid in ('s1', 's3', 's6'):
        assert '02·08 을 새 번들로 다시 실행하세요' in new_section(page, sid), sid
    assert 'id="cards-line"' not in page.split('<script')[0]


def test_same_input_twice_gives_the_same_bytes(real):
    assert render(real) == render(copy.deepcopy(real))


def test_every_data_go_and_anchor_target_exists(real):
    page = render(real)
    ids = set(re.findall(r'\bid="([^"]+)"', page))
    for target in re.findall(r'data-go="([^"]+)"', page) + re.findall(r'href="#([A-Za-z][\w-]*)"', page):
        assert target in ids, target
    for unit in re.findall(r'#unit=(bd-[0-9a-f]+)', page):
        assert unit in ids or ('rest-line-' + unit) in ids or ('rest-pixel-' + unit) in ids, unit


# ---- 1 sentences and their suppression (H4) -------------------------------------------------------------------------------------

def _statuses(doc, line=None, pixel=None, crp=None):
    b = doc['summary']['bundles']
    for kind, st in (('line', line), ('pixel', pixel)):
        if st:
            b['status'][kind] = st
            b['ranking'][kind] = {'status': st, 'k80': None, 'cards': [], 'cover': None, 'rest_share': None, 'rest': []}
            b['reasons'][kind] = ['kind_not_computable'] if st == 'not_evaluated' else []
    if crp is not None:
        b['crp']['pixel'] = crp


def test_h4_nothing_changed_says_no_item_change_for_both_kinds(real):
    s1 = new_section(render(variant(real, lambda d: _statuses(d, 'no_change', 'no_change', 0.0))), 's1')
    assert 'line 에는 item 변화가 없습니다' in s1 and 'pixel 에는 item 변화가 없습니다' in s1 and '보정값(CRP) 차이' not in s1
    assert '가장 큰' not in s1


def test_h4_only_the_crp_changes(real):
    s1 = new_section(render(variant(real, lambda d: _statuses(d, 'no_change', 'no_change', -0.13))), 's1')
    assert 'pixel 에는 item 변화가 없습니다.' in s1 and 'pixel 변화 −13.0000%p 는 보정값(CRP) 차이에서 나왔습니다.' in s1


def test_r12b_3_no_change_with_a_crp_below_the_limit_has_no_crp_sentence_and_a_positive_one_keeps_its_sign(real):
    tiny = new_section(render(variant(real, lambda d: _statuses(d, 'no_change', 'no_change', 1e-15))), 's1')
    assert 'pixel 에는 item 변화가 없습니다.' in tiny and '보정값(CRP) 차이' not in tiny
    plus = new_section(render(variant(real, lambda d: _statuses(d, 'no_change', 'no_change', 0.02))), 's1')
    assert 'pixel 변화 +2.0000%p 는 보정값(CRP) 차이에서 나왔습니다.' in plus


def test_h4_one_kind_that_cannot_be_computed_leaves_the_other_alone(real):
    s1 = new_section(render(variant(real, lambda d: _statuses(d, None, 'not_evaluated'))), 's1')
    assert 'pixel 원인 후보는 계산할 수 없습니다' in s1 and '이 종류의 값을 계산할 수 없습니다' in s1
    assert '가장 큰 line 원인 후보' in s1 and '가장 큰 pixel' not in s1


def test_h4_six_cards_or_fewer_hold_everything_and_more_say_the_rest(real):
    s1 = new_section(render(real), 's1')
    assert 'line 변화량(증가와 감소의 크기를 모두 더한 값)은 원인 후보 3 개에 모두 있습니다' in s1 and '나머지' not in s1

    def more(d):
        rk = d['summary']['bundles']['ranking']['line']
        rk[rk['main']]['rest'] = [cards_of(d, 'pixel')[0]]
        rk[rk['main']]['cover'] = 0.9
    s1 = new_section(render(variant(real, more)), 's1')
    assert '나머지 1 원인 후보가 10% 를 나눠 가집니다' in s1 and '모두 있습니다' not in s1.split('pixel item 변화량')[0]


def test_hub_without_partner_and_mixed_signs(real):
    def edit(d):
        b = d['summary']['bundles']['list'][0]
        b.update(type='hub', electrode='X', layer='L1', electrodes=None, pair_id=None, partner_count=0, partners=[], why=[])
    s3 = new_section(render(variant(real, edit)), 's3')
    assert '이 전극 안의 불량이 변했습니다' in s3 and '상대 0 개' not in s3

    def mixed(d):
        b = d['summary']['bundles']['list'][0]
        b.update(type='hub', electrode='X', layer='L1', electrodes=None, pair_id=None, partner_count=4, partners=['a', 'b', 'c', 'd'],
                 atoms={'increase': 3, 'decrease': 1, 'count': 4})
    assert '상대 4 개와의 불량이 함께 변했습니다(늘어난 3 · 줄어든 1)' in new_section(render(variant(real, mixed)), 's3')


def test_conflict_sentence_and_card_badge(real):
    def edit(d):
        for b in d['summary']['bundles']['list']:
            b['conflict'] = True
            b['kinds']['line']['net'], b['kinds']['pixel']['net'] = 0.01, -0.01
    page = render(variant(real, edit))
    assert '같은 변화가 pixel 을 줄였습니다' in new_section(page, 's1') and 'b-mv">상충' in new_section(page, 's3')


def test_why_texts_for_one_reason_and_mixed_reasons(real):
    def edit(d):
        d['summary']['bundles']['list'][0]['why'] = ['hubs', 'nohub']
        d['summary']['bundles']['list'][1]['why'] = ['both']
    s3 = new_section(render(variant(real, edit)), 's3')
    assert '두 전극 중 한쪽에 맡길 수 없어 이 쌍 하나로 묶었습니다' in s3 and '두 전극 모두 이 레이어의 상태가 바뀌었습니다' in s3


# ---- 3 cards: only ranking.cards / rest, R10b:2 --------------------------------------------------------------------------------

def test_r10b2_cards_come_from_the_ranking_only_and_an_empty_list_says_so(real):
    def edit(d):
        clear_cards(d, 'line')
    page = render(variant(real, edit))
    s3 = new_section(page, 's3')
    assert 'id="cards-line-none"' in s3 and 'id="cards-line"' not in s3
    # the bundles of the line list that no pixel card shows have no card at all
    line_only = [i for i in cards_of(real, 'line') if i not in cards_of(real, 'pixel')]
    assert line_only and all('id="%s"' % i not in s3 for i in line_only)
    s3b = new_section(render(variant(real, lambda d: clear_cards(d, 'line'))), 's3')
    assert '원인 후보마다 아주 작아' in s3b


def test_the_card_has_the_four_parts_and_the_evidence_line(real):
    s3 = new_section(render(real), 's3')
    card = s3[s3.index('<div class="bcard"'):]
    for head in ('무엇이 바뀌었나', '어떤 불량이 어떻게', '얼마나', '근거'):
        assert '<h4>%s</h4>' % head in card, head
    assert '쌍 전체 Δ' in card and '이 카드 몫' in card
    assert '지도 폴더가 없어 연결 영역 지표가 없습니다' in card


def test_the_evidence_sums_observed_regions_and_gives_a_reason_otherwise(tmp_path, real):
    b = real['summary']['bundles']['list'][0]
    ids = [i for r in b['rows'] for i in r['item_ids']]

    def occ(states):
        items = []
        for item_id, (sa, sb) in zip(ids, states):
            ref = {'state': sa, 'n': 4 if sa == 'observed' else None}
            tgt = {'state': sb, 'n': 9 if sb == 'observed' else None}
            cmp = {'kind': 'both_observed'} if sa == sb == 'observed' else {'kind': 'suppressed', 'reason': 'pending' if 'pending' in (sa, sb) else 'not_observed'}
            items.append({'item_id': item_id, 'reference': ref, 'target': tgt, 'compare': cmp})
        return {'diameter_um': 3.0, 'items': items, 'counts': {'reference': {}, 'target': {}}}
    ctx = ledger_report.Ctx(real, 30, 30, maps={'doc': {'occurrence': occ([('observed', 'observed')] * len(ids))}, 'pngs': {}})
    assert ledger_report._occ_evidence(ctx, b) == '관측 연결 영역 합(3 µm): A %d → B %d' % (4 * len(ids), 9 * len(ids))
    ctx = ledger_report.Ctx(real, 30, 30, maps={'doc': {'occurrence': occ([('pending', 'observed')] * len(ids))}, 'pngs': {}})
    assert ledger_report._occ_evidence(ctx, b).startswith('— (대기') and '메모리 예산 부족' in ledger_report._occ_evidence(ctx, b)
    ctx = ledger_report.Ctx(real, 30, 30, maps={'doc': {'occurrence': occ([('not_observed', 'observed')] * len(ids))}, 'pngs': {}})
    assert '지도 지름 자료 없음' in ledger_report._occ_evidence(ctx, b)
    ctx = ledger_report.Ctx(real, 30, 30, maps={'doc': {}, 'pngs': {}})
    assert '02·08 을 새 번들로 다시 실행하세요' in ledger_report._occ_evidence(ctx, b)


# ---- 6 the states of the mockup ---------------------------------------------------------------------------------------------------

def test_6_normal_state_table_and_lists(real):
    s6 = new_section(render(real), 's6')
    assert 'id="design-table"' in s6 and 'id="cond6"' in s6 and 'id="d-w-line"' in s6 and '해상도 비교 검산' in s6
    assert '기준 = A' in s6 and 'A 의 해상도' in s6


def test_r10b1_a_negligible_design_with_pairs_that_cancel_does_not_say_none(real):
    d = real['summary']['design']['line']
    assert 'design' in d['negligible'] and d['top_increase']
    s6 = new_section(render(real), 's6')
    assert '설계 차이 합은 0 에 가깝지만 쌍 사이에서 오르고 내린 곳이 있습니다' in s6
    assert '설계 차이는 거의 없습니다' not in s6.split('line vi 차이')[1].split('pixel vi 차이')[0]

    def empty(dd):
        dd['summary']['design']['line'].update(top_increase=[], top_decrease=[])
    assert '설계 차이는 거의 없습니다' in new_section(render(variant(real, empty)), 's6')


def test_6_same_resolution(real):
    def edit(d):
        for k in KINDS:
            d['summary']['design'][k]['negligible'] = ['design', 'resolution', 'crp', 'density']
            d['summary']['design'][k]['names']['show_outside_conditions'] = False
    s6 = new_section(render(variant(real, edit)), 's6')
    assert '두 제품의 해상도가 같아 나눌 몫이 없습니다' in s6 and 'id="design-table"' not in s6
    assert '해상도 몫도 들어 있습니다' not in new_section(render(variant(real, edit)), 's3')


def test_6_other_conditions_differ_adds_a_column(real):
    def edit(d):
        d['summary']['design']['pixel']['names']['show_outside_conditions'] = True
        d['summary']['design']['pixel']['segments']['crp'] = -0.01
    s6 = new_section(render(variant(real, edit)), 's6')
    assert '해상도 외 조건 몫' in s6 and '해상도 외 조건이 다름' in s6
    # R12b:4: the note under the line that names the outside conditions
    assert '환산 경로(설계 → 해상도 → 보정값 → 밀도)로 나눈 몫이라 1·4 의 보정값(CRP) 차이와 다를 수 있습니다.' in s6


def test_6_waiting_conversion_and_not_evaluated_and_not_available(real):
    def wait(d):
        d['summary']['design']['pixel'].update(status='unavailable', reasons=['density_cell_missing'], segments=None, a_relative=None, top_increase=[], top_decrease=[])
    page = render(variant(real, wait))
    s6 = new_section(page, 's6')
    assert '환산 대기' in s6 and 'pixel 은 지금 나눌 수 없습니다(density_cell_missing)' in s6 and 'id="d-w-pixel"' not in s6
    assert '해상도 몫은 지금 나눌 수 없습니다' in new_section(page, 's1') or '해상도 차이 몫도 들어 있습니다' in new_section(page, 's1')

    def ne(d):
        d['summary']['design']['line'].update(status='not_evaluated', reasons=['rebase_check_failed'], segments=None)
    assert '판정을 생략했습니다(환산 검산이 맞지 않습니다)' in new_section(render(variant(real, ne)), 's6')

    def na(d):
        d['summary']['design'] = None
        d['summary'].pop('design')
    assert '02·08 을 새 번들로 다시 실행하세요' in new_section(render(variant(real, na)), 's6')


def test_r10b3_cno_and_cell_scopes_show_area_and_counts_without_a_partial_warning(real):
    def edit(d):
        d['summary']['design']['line']['missing_cells'] = [
            {'scope': 'cno', 'density_layer': 'L1', 'diameter_um': 3.0, 'area_b_um2': 12.0, 'lambda_b_share': None, 'share_partial': True,
             'item_count': 0, 'cno_count': 2},
            {'scope': 'item', 'density_layer': 'L2', 'diameter_um': 3.0, 'area_b_um2': 5.0, 'lambda_b_share': 0.1, 'share_partial': True,
             'item_count': 1, 'cno_count': 0}]
    s6 = new_section(render(variant(real, edit)), 's6')
    rows = re.findall(r'<tr><td class="l">line</td><td class="l">(\w+)</td>(.*?)</tr>', s6[s6.index('id="missing-cells"'):], re.S)
    by = {scope: row for scope, row in rows}
    assert '12' in by['cno'] and '0 / 2' in by['cno'] and '일부 몫 없음' not in by['cno']
    assert '일부 몫 없음' in by['item']


def test_no_verdict_partial_and_no_occurrence_states(real):
    def edit(d):
        st = d['summary']['concentration']['pixel']['actual']['pair']
        st.update(verdict='omitted', omit_reasons=['atom_null'], k80=None)
    s4 = new_section(render(variant(real, edit)), 's4')
    assert '판정을 생략했습니다(값 없는 항목이 있음)' in s4
    partial = variant(real, lambda d: d.update(status='partial', reasons=['ledger_totals_check_failed']))
    assert 'id="partial"' in render(partial)
    assert '지도 폴더가 없어 연결 영역 지표가 없습니다' in new_section(render(real), 's3')


def test_reason_label_for_a_failed_summary_check():
    assert '검산' in ledger_report.REASON_LABELS['ledger_summary_check_failed']


# ---- layer order (E10) ------------------------------------------------------------------------------------------------------------------

def test_layer_order_in_2_5_and_the_maps(tmp_path):
    doc = corr_doc()
    doc['inputs']['layer_order'] = ['PXL', 'L1']
    page = html_of(run_report(tmp_path, doc)[1])
    head = new_section(page, 's2')
    assert head.index('data-layer="PXL"') < head.index('data-layer="L1"')
    assert json.loads(re.search(r'id="report-data">(.*?)</script>', page, re.S).group(1))['layer_order'] == ['PXL', 'L1']
    doc2 = corr_doc()
    page2 = html_of(run_report(tmp_path, doc2, name='byname')[1])
    assert new_section(page2, 's2').index('data-layer="L1"') < new_section(page2, 's2').index('data-layer="PXL"')


def test_layer_order_sd1_sd2_pxl_in_the_07_report():
    doc = {'inputs': {'layer_order': ['SD1', 'SD2', 'PXL']}}
    assert compare2_report._by_layer_order(doc, {'PXL', 'SD2', 'SD1', 'ZZ'}) == ['SD1', 'SD2', 'PXL', 'ZZ']
    assert compare2_report._by_layer_order({'inputs': {}}, {'PXL', 'SD2', 'SD1'}) == ['PXL', 'SD1', 'SD2']


def test_ledger_report_layer_key_puts_unknown_names_last_by_name(real):
    def edit(d):
        d['inputs']['layer_order'] = ['SD1', 'SD2', 'PXL']
    ctx = ledger_report.Ctx(variant(real, edit), 30, 30)
    assert ctx.sort_layers(['PXL', 'ZZ', 'SD2', 'AA', 'SD1']) == ['SD1', 'SD2', 'PXL', 'AA', 'ZZ']


def test_r11_6_maps_are_found_by_their_layer_field(tmp_path):
    doc, maps, out = map_report(tmp_path, changes=ok_changes())
    page = html_of(out)
    data = json.loads(re.search(r'id="report-data">(.*?)</script>', page, re.S).group(1))
    mdoc = json.loads((maps / 'ledger_maps.json').read_text(encoding='utf-8'))
    for role, key in (('reference', 'A'), ('target', 'B')):
        want = sorted((e['file'], e['layer']) for e in mdoc['sides'][role]['maps'])
        assert sorted((m['f'], m['l']) for m in data['maps']['sides'][key]['maps']) == want
    assert '변경 위치(04, 보조)' in page


# ---- the script: the hash whitelist and the links (headless Chrome) ---------------------------------------------------------------

@pytest.fixture(scope='module')
def real_page(tmp_path_factory, real):
    folder = write_ledger(tmp_path_factory.mktemp('rn7c') / 'led', real)
    out = tmp_path_factory.mktemp('rn7o') / 'out'
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None) == 0
    return out / 'ledger_report.html'


def test_chrome_normal_page_has_no_script_error_and_the_unit_hash_selects_a_card(real_page, real):
    unit = cards_of(real, 'line')[0]
    for fragment in ('#', '#unit=%s&to=s3' % unit, '#unit=%s&to=s2' % unit, '#to=s6', '#to=explore'):
        dom, text = dump_dom(real_page, fragment)
        assert 'data-err' not in dom.root, fragment
    dom, text = dump_dom(real_page, '#unit=%s&to=s3' % unit)
    assert dom.root['data-unit'] == unit
    card = [a for t, a in dom.elements if a.get('id') == unit]
    assert card and 'selcard' in (card[0].get('class') or '').split()      # R12:3: the card element itself carries the class


def test_chrome_hash_values_outside_the_whitelist_are_ignored(real_page):
    for fragment in ('#unit=bd-zzzz&to=nowhere&it=%22%3E%3Cb%3E', '#unit=bd-0123456789abcdef&to=s9', '#unit=%3Cscript%3E&to=sA%22'):
        dom, text = dump_dom(real_page, fragment)
        assert 'data-err' not in dom.root and dom.root.get('data-unit', '') == '', fragment
        assert '<b>"' not in text.split('<script')[0]


def test_chrome_clearing_the_selection_clears_the_cause_candidate(real_page, real):
    unit = cards_of(real, 'line')[0]
    from test_ledger_report_v2 import run_in_page
    script = ("setTimeout(function(){document.getElementById('sel-clear').click();setTimeout(function(){"
              "document.documentElement.setAttribute('data-after',location.hash+'|'+document.documentElement.getAttribute('data-unit'));},200);},300);")
    dom, text = run_in_page(real_page, '#unit=%s' % unit, script)
    assert 'data-err' not in dom.root and dom.root['data-after'].endswith('|') and 'unit=' not in dom.root['data-after']


# ---- rnarr/T7b (R12): the CRP sentences, the scroll, the move and odd-name cards, the 6 ratios --------------------------------------

def test_r12_1_the_crp_change_is_named_in_1_and_4_and_kept_out_of_the_cause_candidates(real):
    crp = real['summary']['bundles']['crp']['pixel']
    assert crp != 0.0
    page = render(real)
    s1, s4 = all_text(new_section(page, 's1')), all_text(new_section(page, 's4'))
    signed = ledger_report.raw_pp(crp)
    assert signed[0] in '+−'
    sentence = 'pixel 변화 중 %s 는 보정값(CRP) 차이에서 나왔고, 원인 후보에는 들어 있지 않습니다.' % signed
    assert sentence in s1 and '보정값(CRP) 차이 %s' % signed in s4          # the same signed text in 1 and 4
    assert 'pixel item 변화량' in s1 and '원인 후보 3 개에 모두 있습니다' in s1
    # R12b:2: the CRP sentence comes right after the sentence that says how much of the item change the cards hold
    assert s1.index('원인 후보 3 개에 모두 있습니다') < s1.index(sentence) < s1.index('가장 큰 pixel 원인 후보')

    zero = new_section(render(variant(real, lambda d: _statuses(d, crp=0.0))), 's1')
    assert '보정값(CRP) 차이에서 나왔' not in zero and 'pixel item 변화량' not in zero


def test_r12_5_a_missing_rule_ratio_is_a_dash_not_an_error(real):
    def edit(d):
        d['summary']['rebase']['ratios']['line']['r_rule'] = None
    s6 = new_section(render(variant(real, edit)), 's6')
    row = re.search(r'<tr><td class="l">유효 해상도 규칙\(비\)</td>(.*?)</tr>', s6, re.S)
    assert row and '판정 불가' in row.group(1) and '—' in row.group(1)


def test_r12_6_the_missing_04_result_is_no_band_but_one_line_in_the_folded_reference(tmp_path):
    from test_ledger_report_maps import setup as maps_setup
    from test_ledger_report_v2 import ledger_report_run
    doc, folder, maps = maps_setup(tmp_path)
    page = html_of(ledger_report_run(tmp_path, folder, maps, 'rn_b6'))
    assert '<div class="band info" id="band-changes"' not in page
    pn = page[page.index('id="roi-ref"'):]
    assert 'id="band-changes"' in pn[:pn.index('</details>')]


def test_r12_9_the_nearly_no_design_difference_branch_names_the_outside_conditions(real):
    def edit(d):
        pix = d['summary']['design']['pixel']
        pix['negligible'] = ['design']
        pix['names']['show_outside_conditions'] = True
        pix['segments']['crp'], pix['segments']['density'] = -0.02, 0.0
    s6 = all_text(new_section(render(variant(real, edit)), 's6'))
    assert '해상도 외 조건 차이는 −2.0000%p' in s6


def test_r12_10_no_design_or_resolution_number_outside_6(real):
    design = real['summary']['design']
    values = set()
    for kind in KINDS:
        d = design[kind]
        if d.get('status') != 'available':
            continue
        values.update([d['segments']['design'], d['segments']['resolution']])
        values.update(e['design_delta'] for e in d['top_increase'] + d['top_decrease'])
    actual = [real['totals'][k]['delta'] for k in KINDS]     # a design value that equals the real vi change is shown outside 6 as that change
    texts = {ledger_report.raw_pp(v) for v in values if v and all(abs(v - a) > 1e-12 for a in actual)}
    page = render(real)
    outside = ''.join(all_text(new_section(page, s)) for s in ('s1', 's2', 's3', 's4', 's5'))
    assert texts and not [t for t in texts if t in outside]


def _move_doc(real):
    def edit(d):
        b = d['summary']['bundles']['list'][0]
        b.update(type='move', electrode='E', layer=None, electrodes=None, pair_id=None, partner_count=0, partners=[], why=[])
        b['from'], b['to'] = ['L1'], ['L2']
        for row in b['rows']:
            row['layer'] = 'L2'
    return variant(real, edit)


def test_r12_3a_a_move_card_has_its_title_type_and_row_layers(real):
    doc = _move_doc(real)
    uid = doc['summary']['bundles']['list'][0]['id']
    page = render(doc)
    card = page[page.index('<div class="bcard" id="%s"' % uid):]
    card = card[:card.index('<h4>근거</h4>')]
    assert '>E · L1 → L2</a>' in card and 'badge b-mv">레이어 이동' in card
    assert 'E 전극이 A 의 L1 에서 B 의 L2 로 옮겨졌습니다.' in card
    assert re.search(r'<span class="muted small">L2</span></td>', card)


ODD = 'E|"q"한글'


def _odd_doc(real):
    text = json.dumps(real, ensure_ascii=False).replace('"C"', json.dumps(ODD, ensure_ascii=False))
    return json.loads(text)


def test_r12_3b_odd_electrode_names_are_escaped_everywhere(real):
    doc = _odd_doc(real)
    page = render(doc)
    assert ODD in [e for p in doc['pairs'] for e in p['electrodes']]
    assert 'data-el="E|&quot;q&quot;한글"' in page
    head = re.sub(r'<script.*?</script>', '', page, flags=re.S)
    assert not re.search(r'="[^"<>]*E\|"q"', head)           # a raw quote of the name never closes an attribute
    data = re.search(r'id="report-data">(.*?)</script>', page, re.S).group(1)
    assert '</script' not in data.lower() and json.dumps(ODD, ensure_ascii=False)[1:-1] in data


def test_chrome_r12_3b_odd_names_select_the_card_and_mark_the_cell(tmp_path, real):
    doc = _odd_doc(real)
    unit = next(b['id'] for b in doc['summary']['bundles']['list'] if ODD in (b.get('electrodes') or []))
    folder = write_ledger(tmp_path / 'led', doc)
    out = tmp_path / 'out'
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None) == 0
    dom, text = dump_dom(out / 'ledger_report.html', '#unit=%s&to=s3' % unit)
    assert 'data-err' not in dom.root and dom.root['data-unit'] == unit
    card = [a for t, a in dom.elements if a.get('id') == unit]
    assert card and 'selcard' in (card[0].get('class') or '').split()
    cells = [a for t, a in dom.elements if t == 'td' and 'unitcell' in (a.get('class') or '').split()]
    assert cells and cells[0].get('data-layer')
    rows = [a for t, a in dom.elements if t == 'tr' and a.get('data-row') == 'corr' and a.get('data-el') == ODD]
    assert rows


def test_chrome_r12_2_a_later_hash_change_does_not_pull_the_page_back_to_the_card(real_page, real):
    unit = cards_of(real, 'line')[0]
    from test_ledger_report_v2 import run_in_page
    script = ("setTimeout(function(){window.scrollTo(0,0);location.hash='#unit=%s&layer=L1';setTimeout(function(){"
              "document.documentElement.setAttribute('data-y',String(Math.round(window.pageYOffset)));},300);},300);" % unit)
    dom, text = run_in_page(real_page, '#unit=%s' % unit, script, tag='t7b_scroll')
    assert 'data-err' not in dom.root and dom.root['data-y'] == '0'


def test_chrome_r12_4_the_it_hash_marks_the_item_row(real_page, real):
    pair = next(p for p in real['pairs'] if p.get('items'))
    item = pair['items'][0]['item_id']
    dom, text = dump_dom(real_page, '#sel=pair:%s&layer=L1&it=%s&to=explore' % (pair['pair_id'], item))
    assert 'data-err' not in dom.root
    assert [a for t, a in dom.elements if t == 'tr' and a.get('id') == 'pd-it-sel']
