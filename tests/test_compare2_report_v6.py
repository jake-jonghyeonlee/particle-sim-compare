"""report-v6/T2: units (% / %p / change rate), the breakdown table, the verdict rule, the card change table and the new layout."""
import re
from types import SimpleNamespace

import pytest

import simcompare.compare2_report as cr
from test_change_candidates import world  # noqa: F401  (module-scoped fixtures used by `linked`)
from test_compare2_link import real_chain  # noqa: F401
from test_compare2_report import build, linked, linked_report, report, text  # noqa: F401


# ---- (a) units ----------------------------------------------------------------------------------------------------------

def test_a_percent_pp_and_change_rate_formats():
    assert cr._pc(0.00412) == '0.4120%' and cr._pc(None) == '—'
    assert cr._pp(0.00032) == '+0.0320%p' and cr._pp(-0.00005) == '-0.0050%p' and cr._pp(None) == '—'
    assert cr._rel(0.00032, 0.00412) == '+7.8%' and cr._rel(-0.0005, 0.004) == '-12.5%'
    assert cr._dv(0.00032, 0.00412) == '+0.0320%p (+7.8%)'
    assert cr._dv(-0.00005, 0.004) == '-0.0050%p (-1.2%)'


def test_a_a_missing_or_tiny_base_prints_a_dash_in_the_brackets():
    for base in (None, 0.0, cr.RATIO_EPS / 10):
        assert cr._rel(0.001, base) == '—'
        assert cr._dv(0.001, base) == '+0.1000%p (—)'
    assert cr._dv(None, 0.004) == '—' and cr._rel(None, 0.004) == '—'


def test_a_html_uses_the_new_units(tmp_path):
    code, out = build(tmp_path)
    exit_code, folder = report(tmp_path, out)
    html = text(folder)
    assert re.search(r'[+-]\d+\.\d{4}%p \([+-]\d+\.\d%\)', html)
    assert 'id="notation"' in html and '기준 A 의 같은 지표' in html
    assert '--line:#7a4fd6' in html and '--line:#9a78ea' in html


# ---- (b) breakdown -------------------------------------------------------------------------------------------------------

def _loc_view():
    rois = {'r1': {'roi_key': 'r1', 'delta_vi': {'line': 0.006, 'pixel': 0.002}, 'replica_factor': 2, 'map_pairs': [],
                   'design_change': {'change_kinds': ['reshaped']}, 'condition_ids': []},
            'r2': {'roi_key': 'r2', 'delta_vi': {'line': 0.001, 'pixel': 0.0005}, 'replica_factor': 1, 'map_pairs': [],
                   'design_change': {'change_kinds': ['appeared', 'shifted']}, 'condition_ids': []},
            'r3': {'roi_key': 'r3', 'delta_vi': {'line': -0.0004, 'pixel': 0.0001}, 'replica_factor': 1, 'map_pairs': [],
                   'design_change': {'change_kinds': ['shifted']}, 'condition_ids': []}}
    pairs = [{'pair_id': 'p1', 'roi_key': 'r1', 'delta_vi': {'line': 0.0, 'pixel': 0.0004}, 'reasons': []},
             {'pair_id': 'p2', 'roi_key': 'r2', 'delta_vi': {'line': 0.001, 'pixel': 0.0005}, 'reasons': []}]
    loc = {'top_worse': ['r1'], 'top_improved': [], 'rois': list(rois.values()), 'pairs': pairs,
           'counts': {'worse': 2, 'improved': 1, 'negligible': 0, 'uncomputable': 0}}
    return {'link': {'inputs': {'candidates': {}}}, 'loc': loc, 'rois': rois,
            'link_pairs': {'p1': {'cno_case': True}, 'p2': {}}}


def _doc(cno=True, crp=True):
    delta = {'indicators': {'delta_vi_line': 0.010, 'delta_vi_pixel': 0.004},
             'shared': {'delta_vi_line': 0.001, 'delta_vi_pixel': 0.0005},
             'cno': [{'delta_vi_pixel': 0.0008}, {'delta_vi_pixel': 0.0002}] if cno else []}
    if crp:
        delta['crp_difference'] = {'delta_vi_pixel': 0.0003}
    return {'delta': delta, 'sides': {'reference': {'vi_line': 0.04, 'vi_pixel': 0.02}}}


def test_b_rows_add_up_to_the_total_and_the_cno_row_leaves_out_the_cno_pairs_of_the_locations():
    rows = cr._decomposition(_doc(), _loc_view())
    labels = [label for label, _v, _a in rows]
    assert labels[0].startswith('Top 위치 (2·3절 카드 1곳)') and labels[1] == '나머지 위치 2곳'
    assert labels[2:] == ['공유 영역', 'CNO (위치 밖)', '제품군 보정 차이 (CRP)', '기타 잔차', '전체']
    top, rest = rows[0][1], rows[1][1]
    assert top == pytest.approx({'line': 0.006, 'pixel': 0.002})
    assert rest == pytest.approx({'line': 0.0006, 'pixel': 0.0006})
    cno = rows[3][1]
    assert cno['line'] is None and cno['pixel'] == pytest.approx(0.0008 + 0.0002 - 0.0004 * 2)   # replica factor of r1 is 2
    residual, total = rows[-2][1], rows[-1][1]
    for kind in ('line', 'pixel'):
        assert residual[kind] == pytest.approx(total[kind] - sum(r[1][kind] or 0.0 for r in rows[:-2]))
        assert sum(r[1][kind] or 0.0 for r in rows[:-1]) == pytest.approx(total[kind])      # the rows add up to the total
    assert [r[2] for r in rows] == ['근사', '근사', '정확', '근사', '정확', '전체 − 위 합', '정확']


def test_b_without_a_link_the_location_rows_become_one_sentence_and_cno_counts_in_full():
    view = {'link': None, 'loc': None, 'rois': {}, 'link_pairs': {}}
    rows = cr._decomposition(_doc(crp=False), view)
    assert [r[0] for r in rows] == ['공유 영역', 'CNO (위치 밖)', '기타 잔차', '전체']
    assert rows[1][1]['pixel'] == pytest.approx(0.0010)
    html = cr._decomposition_table(_doc(crp=False), view)
    assert '위치 연결 없음' in html and 'Top 위치' not in html


def test_b_a_null_total_gives_a_null_residual():
    doc = _doc()
    doc['delta']['indicators']['delta_vi_pixel'] = None
    rows = cr._decomposition(doc, _loc_view())
    assert rows[-1][1]['pixel'] is None and rows[-2][1]['pixel'] is None and rows[-2][1]['line'] is not None


def test_b_the_real_report_prints_the_breakdown_and_the_cno_sentence(linked, tmp_path):
    html, _ = linked_report(linked, tmp_path)
    table = html[html.index('<table id="decomp">'):html.index('</table>', html.index('<table id="decomp">'))]
    for label in ('Top 위치 (2·3절 카드 1곳)', '나머지 위치 0곳', '공유 영역', 'CNO (위치 밖)', '기타 잔차', '<b>전체</b>'):
        assert label in table, label
    assert '위치 값은 창 면적 기준이라 CNO 총합과 기준이 다릅니다' in html
    assert 'id="scatter"' in html and 'id="kinds"' in html


# ---- (c) verdict ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('line,pixel,expected', [
    (1.0, 2.0, '둘 다 악화'), (-1.0, -2.0, '둘 다 개선'),
    (1.0, -2.0, '상충 (line↑ pixel↓)'), (-1.0, 2.0, '상충 (line↓ pixel↑)'),
    (1.0, 0.0, '악화'), (0.0, 2.0, '악화'), (-1.0, 0.0, '개선'), (0.0, -2.0, '개선'),
    (1.0, None, '악화'), (None, -1.0, '개선'),
    (0.0, 0.0, None), (None, None, None), (0.0, None, None)])
def test_c_verdict_rule(line, pixel, expected):
    verdict = cr._verdict(line, pixel)
    assert (None if verdict is None else verdict[0]) == expected
    assert (cr._verdict_badge(line, pixel) == '') == (expected is None)


def test_c_conflict_needs_both_signs():
    assert cr._is_conflict(1, -1) and cr._is_conflict(-1, 1)
    assert not cr._is_conflict(1, 1) and not cr._is_conflict(1, 0) and not cr._is_conflict(None, -1)


# ---- (d) card change table -------------------------------------------------------------------------------------------------

def test_d_card_rows_match_the_pairs_and_carry_data_pair(linked, tmp_path):
    html, record = linked_report(linked, tmp_path)
    card = html[html.index('<div class="roi"'):]
    table = card[card.index('<table class="chgtable">'):card.index('</table>', card.index('<table class="chgtable">'))]
    pair_ids = re.findall(r'data-pair="([^"]+)"', table)
    assert len(pair_ids) == len(set(pair_ids)) == 2 == table.count('class="pairrow"')
    for head in ('변경', '전극 쌍', '유형', '불량 영역 A→B', 'Δvi_line', 'Δvi_pixel', 'B 제거 시 line', 'B 제거 시 pixel'):
        assert head in table, head
    assert '추가' in table and '제거' in table
    assert '5 µm 이물' in card and '행을 누르면' in card
    assert '창 한 변' in card                        # the maps are unchanged (T3 replaces them)


def _card_view(pairs, loc_pairs, cases=(), one_sided=(), conds=None, cno=None, replica=1):
    roi = {'roi_key': 'roi:x', 'replica_factor': replica}
    lk = {'contributing_cases': list(cases), 'one_sided_cases': list(one_sided)}
    return {'base': {'line': 0.04, 'pixel': 0.02}, 'cfg2': SimpleNamespace(report=SimpleNamespace(diameter_um=5.0)),
            'loc': {'pairs': loc_pairs}, 'link_pairs': {p['pair_id']: p for p in pairs}, 'link_rois': {'roi:x': lk},
            'rois': {'roi:x': roi}, 'cond_by_id': conds or {}, 'cno_by_id': cno or {}}


def _link_pair(pid, ref, tgt, named=None, cno=False, ends=('E1', 'E2')):
    side = {'analysis_layer': 'L1', 'endpoints': [{'electrode_id': e} for e in ends]}
    pair = {'pair_id': pid, 'a': side, 'b': side, 'named_pair': named or {},
            'conditions': {'reference': {'condition_id': ref}, 'target': {'condition_id': tgt}}}
    if cno:
        pair['cno_case'] = True
    return pair


def _loc_pair(pid, line, pixel, reasons=()):
    return {'pair_id': pid, 'roi_key': 'roi:x', 'delta_vi': {'line': line, 'pixel': pixel}, 'reasons': list(reasons)}


def test_d_rows_cover_pairs_in_delta_order_and_one_sided_cases_without_a_pair():
    removal = {'delta_vi_line': -0.002, 'delta_vi_pixel': 0.0}
    conds = {'AB@L1': {'class': 'line', 'removal_b': removal}, 'CD@L1': {'class': 'pixel', 'removal_b': None}}
    cno = {'cno:Z@L1': {'removal_b': {'delta_vi_line': 0.0, 'delta_vi_pixel': 0.001}}}
    named = {'5': {'a_value_um2': 1.0, 'b_value_um2': 2.5, 'a_status': 'available', 'b_status': 'available'}}
    bad = {'5': {'a_value_um2': 1.0, 'b_value_um2': None, 'a_status': 'available', 'b_status': 'unavailable'}}
    pairs = [_link_pair('big', 'AB@L1', 'AB@L1', named), _link_pair('small', 'CD@L1', 'CD@L1', bad),
             _link_pair('cno1', 'cno:Z@L1', 'cno:Z@L1', cno=True, ends=('Z',)), _link_pair('none', None, None)]
    loc_pairs = [_loc_pair('small', 0.0001, 0.0), _loc_pair('big', 0.003, -0.001), _loc_pair('cno1', 0.0, 0.0005),
                 _loc_pair('none', None, None, ['named_pair_missing'])]
    cases = [{'case_key': 'k1', 'change_kind': 'reshaped', 'present_in': 'both',
              'conditions': {'reference': {'condition_id': 'AB@L1'}, 'target': {'condition_id': 'AB@L1'}}}]
    one_sided = [{'case_key': 'k2', 'change_kind': 'appeared', 'present_in': 'target', 'electrodes': ['P', 'Q'],
                  'analysis_layer': 'L1', 'conditions': {'reference': {'condition_id': None}, 'target': {'condition_id': 'CD@L1'}}},
                 {'case_key': 'k3', 'change_kind': 'disappeared', 'present_in': 'reference', 'electrodes': ['R', 'S'],
                  'analysis_layer': 'L2', 'conditions': {'reference': {'condition_id': 'GONE@L2'}, 'target': {'condition_id': None}}}]
    view = _card_view(pairs, loc_pairs, cases, one_sided, conds, cno, replica=4)
    html = cr._change_table(view, 'roi:x')
    assert html.count('class="pairrow"') == 4 + 2                                   # 4 pairs + 2 one-sided cases
    assert re.findall(r'data-pair="([^"]+)"', html) == ['big', 'cno1', 'small', 'none']   # |delta| order, no value last
    assert html.count('data-pair=') == 4                                            # the one-sided case rows carry none
    assert '1.00 → 2.50 µm²' in html and '<span class="chip">CNO</span>' in html and '지도 없음' in html
    big = html[html.index('data-pair="big"'):html.index('data-pair="cno1"')]
    assert '형상 변경' in big and '-0.2000%p (-5.0%)' in big and '+0.3000%p (+7.5%)' in big
    small = html[html.index('data-pair="small"'):html.index('data-pair="none"')]
    assert '<td>—</td>' in small and small.count('+0.0100%p') == 1                  # area unavailable; B removal absent
    none_row = html[html.index('data-pair="none"'):html.index('</tr>', html.index('data-pair="none"'))]
    assert none_row.count('값 없음') == 2 and '6회차 창 결과에 이 쌍의 named_pair 행이 없습니다' in none_row
    assert '추가 (B에만)' in html and '제거 (A에만)' in html and 'P–Q' in html
    assert '표의 값은 대표 위치 1곳 값입니다' in html


def test_d_a_roi_without_pairs_says_so():
    html = cr._change_table(_card_view([], []), 'roi:x')
    assert 'pairrow' not in html and '연결된 변경 쌍이 없습니다' in html


# ---- (e)(f)(g) layout ---------------------------------------------------------------------------------------------------------

def _both(linked, tmp_path):
    unlinked_dir = tmp_path / 'plain'
    unlinked_dir.mkdir()
    code, out = build(unlinked_dir)
    _exit, folder = report(unlinked_dir, out)
    return {'linked': linked_report(linked, tmp_path)[0], 'plain': text(folder)}


def test_e_old_sections_are_gone_and_the_new_ids_exist(linked, tmp_path):
    for name, html in _both(linked, tmp_path).items():
        for gone in ('class="story"', 'id="dist-el"', 'for="dist-el"', 'id="s0"', 'id="s7"', 'id="s8"', '주요 결함 조건과 영향',
                     '4. line 불량 — 전극', '5. pixel 불량 — 전극'):
            assert gone not in html, (name, gone)
        for sec in ('s1', 's2', 's3', 's4', 's5', 's6', 's9'):
            assert f'id="{sec}"' in html and f'href="#{sec}"' in html, (name, sec)
        assert '4. 전극별 영향' in html and '5. 전체 분포' in html and '용어' in html and '제품 정보' in html
        assert html.index('id="s6"') < html.index('id="s9"') and html.index('id="s9"') < html.index('id="prod"')
        assert 'id="dist-layer"' in html and 'id="dist-size"' in html


def test_e_sort_buttons_and_the_condition_table_link_column(linked, tmp_path):
    html = linked_report(linked, tmp_path)[0]
    assert html.count('data-sort-for="rank-s2"') == 1 and 'data-s="pixel"' in html and 'tr[data-net]' in html
    conds = html[html.index('<table id="conds">'):html.index('</table>', html.index('<table id="conds">'))]
    assert '변경 위치' in conds and 'href="#roiroi:' in conds and '악화 1' in conds
    elec = html[html.index('<section id="s4">'):html.index('<section id="s5">')]
    assert '변경 위치' in elec and '판정' in elec and 'href="#roiroi:' in elec


def test_f_no_ten_to_the_minus_four_notation(linked, tmp_path):
    for name, html in _both(linked, tmp_path).items():
        assert '×10⁻⁴' not in html and '10⁻⁴' not in html, name


def test_g_no_external_url(linked, tmp_path):
    for name, html in _both(linked, tmp_path).items():
        for banned in ('http://', 'https://', '<link', '<script src', 'xmlns'):
            assert banned not in html, (name, banned)
        for target in re.findall(r'(?:src|href)="([^"]*)"', html):
            assert target.startswith('#') or target.startswith('data:image/png;base64,'), (name, target)


# ---- scatter and kind table -----------------------------------------------------------------------------------------------------

def test_scatter_draws_cards_large_and_caps_the_small_dots():
    view = _loc_view()
    for i in range(305):
        key = f'x{i}'
        view['rois'][key] = {'roi_key': key, 'delta_vi': {'line': 0.0001 * (i + 1), 'pixel': -0.0001}, 'replica_factor': 1,
                             'map_pairs': [], 'design_change': {'change_kinds': []}, 'condition_ids': []}
        view['loc']['rois'].append(view['rois'][key])
    svg = cr._scatter(_doc(), view)
    assert svg.count('r="3.5"') == cr.MAX_SCATTER_DOTS == 300 and svg.count('r="7"') == 1
    assert '나머지 7곳은 그리지 않았습니다' in svg and 'href="#roir1"' in svg and '<title>' in svg
    assert 'viewBox' in svg and 'http' not in svg


def test_scatter_without_a_link_is_a_sentence():
    assert '위치 연결 없음' in cr._scatter(_doc(), {'link': None, 'loc': None})


def test_kind_table_counts_a_location_under_every_kind_and_says_double_count():
    html = cr._kind_table(_doc(), _loc_view())
    assert 'id="kinds"' in html and '중복 합' in html
    shifted = html[html.index('위치 이동'):html.index('</tr>', html.index('위치 이동'))]
    assert '<td>2</td>' in shifted and '+0.0600%p (+1.5%)' in shifted            # r2 + r3: 0.001 - 0.0004 on line
    reshaped = html[html.index('형상 변경'):html.index('</tr>', html.index('형상 변경'))]
    assert '<td>1</td>' in reshaped and '+0.6000%p (+15.0%)' in reshaped
