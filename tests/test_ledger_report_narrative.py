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


# ---- a builder of consistent version 2 cause candidate data (rnarr/T12) -----------------------------------------------------------

TYPES = ('electrode_new', 'electrode_removed', 'layer_change', 'hub', 'pair', 'self', 'cno')
OLD_TERMS = ('늘어난', '줄어든', '늘린', '줄인', '주 방향', '상쇄 요인', '상쇄 묶음', '쌍 전체 Δ', '변화량', '그 밖')


def mk(doc, idx, type_, nets, layer='L1', **extra):
    """One bundle (version 2 shape) whose rows point at a real pair and item of `doc`; nets = {kind: net}."""
    p = doc['pairs'][idx % len(doc['pairs'])]
    item = p['items'][0]['item_id']
    pair_like = type_ == 'pair'
    b = {'id': 'bd-%016x' % (0xabc000 + idx * 7 + extra.pop('salt', 0)), 'key': [type_, idx], 'why': extra.pop('why', []),
         'layer_state_unverified': False, 'layer_state_unknown': False, 'independent_cno': type_ == 'cno', 'type': type_,
         'pair_id': p['pair_id'] if pair_like else None, 'layer': layer, 'layers': [layer],
         'electrodes': list(p['electrodes']) if pair_like else None, 'electrode': None if pair_like else 'E%d' % idx,
         'partners': [], 'partner_count': 0, 'hub': None, 'top_partner': {'line': None, 'pixel': None}, 'conflict': False,
         'partners_elsewhere': []}
    kinds, rows, inc, dec = {}, [], 0, 0
    for k in KINDS:
        n = nets.get(k, 0.0)
        a = (extra.get('abs') or {}).get(k, abs(n))
        kinds[k] = {'net': n, 'abs': a, 'share': None, 'increase': int(n > 0), 'decrease': int(n < 0), 'atoms': 1 if a else 0,
                    'sign': 'none' if a == 0 else 'increase' if n > 0 else 'decrease' if n < 0 else 'zero',
                    'cancelling': abs(n) <= 1e-12 and a > 1e-12, 'internal_cancel': a - abs(n), 'direction_share': None}
        if a:
            rows.append({'kind': k, 'pair_id': p['pair_id'], 'cno': False, 'layer': layer or (extra.get('layers') or ['L1'])[0], 'partner': None,
                         'electrodes': list(p['electrodes']) if pair_like else None, 'net': n, 'abs': a, 'presence': ['both'],
                         'item_ids': [item], 'pair_delta': p.get('delta')})
        inc, dec = inc + int(n > 0), dec + int(n < 0)
    extra.pop('abs', None)
    b.update(kinds=kinds, rows=rows, atoms={'increase': inc, 'decrease': dec, 'count': inc + dec})
    b.update(extra)
    return b


def finish(doc, blist, crp=None):
    """Write `summary.bundles` for `blist`: direction ranking (6 + 3 cards), type totals, the hub sensitivity grid."""
    blist = copy.deepcopy(blist)
    ranking, types, g = {}, {}, {}
    for kind in KINDS:
        g[kind] = sum(b['kinds'][kind]['abs'] for b in blist)
        for b in blist:
            b['kinds'][kind]['share'] = b['kinds'][kind]['abs'] / g[kind] if g[kind] else None
        members = [b for b in blist if b['kinds'][kind]['abs'] > 0]
        net = lambda b: b['kinds'][kind]['net']
        groups = {'increase': sorted((b for b in members if net(b) > 0), key=lambda b: (-net(b), b['id'])),
                  'decrease': sorted((b for b in members if net(b) < 0), key=lambda b: (net(b), b['id'])),
                  'zero': sorted((b for b in members if net(b) == 0), key=lambda b: b['id'])}
        total = {n: sum(net(b) for b in groups[n]) for n in ('increase', 'decrease')}
        net_total = total['increase'] + total['decrease']
        if abs(net_total) <= 1e-9:
            direction, main = 'balanced', 'increase' if total['increase'] > -total['decrease'] else 'decrease'
        else:
            direction = main = 'increase' if net_total > 0 else 'decrease'
        packed = {}
        for n in ('increase', 'decrease'):
            run, k80 = 0.0, None
            for i, b in enumerate(groups[n], 1):
                b['kinds'][kind]['direction_share'] = net(b) / total[n]
                run += net(b) / total[n]
                if k80 is None and run >= 0.8 - 1e-12:
                    k80 = i
            count = 6 if direction == 'balanced' or n == direction else 3
            cards = groups[n][:count]
            packed[n] = {'total': total[n], 'k80': k80, 'cards': [b['id'] for b in cards], 'rest': [b['id'] for b in groups[n][count:]],
                         'cover': sum(b['kinds'][kind]['direction_share'] for b in cards) if cards else None}
        ranking[kind] = dict({'status': 'ok', 'direction': direction, 'main': main}, zero=[b['id'] for b in groups['zero']],
                             cancel_ratio=0.0, net_total=net_total, **packed)
        types[kind] = {t: {'count': 0, 'increase': 0.0, 'decrease': 0.0, 'net': 0.0, 'gross': 0.0} for t in TYPES}
        for b in members:
            row = types[kind][b['type']]
            row['count'] += 1
            row['net'] += net(b)
            row['gross'] += b['kinds'][kind]['abs']
            row['increase' if net(b) > 0 else 'decrease'] += net(b) if net(b) else 0.0
        types[kind]['crp'] = {'net': 0.0 if kind == 'line' else (crp or 0.0)}
        types[kind]['cno_all'] = {'count': 0, 'net': 0.0, 'gross': 0.0, 'reference': True}
    old = doc.get('summary', {}).get('bundles') or {}
    crp_value = {'line': 0.0, 'pixel': crp if crp is not None else (old.get('crp') or {}).get('pixel', 0.0)}
    for kind in KINDS:          # R18:2: the identity of the ledger — sum of increase + sum of decrease + CRP = the kind's vi change
        delta = ranking[kind]['net_total'] + crp_value[kind]
        doc['totals'][kind]['delta'] = delta
        doc['kinds'][kind]['vi_b'] = doc['kinds'][kind]['vi_a'] + delta
    doc.setdefault('summary', {})['bundles'] = {
        'rule': 'bundle_assign_v2', 'version': 2, 'hub_min': 3, 'hub_min_fraction': 0.5, 'hub_min_share': 0.0,
        'hub_sensitivity': [{'hub_min_fraction': f, 'hub_min_share': s, 'kinds': {k: {'hubs': 2, 'atoms': 5, 'share': 0.25} for k in KINDS}}
                            for f in (0.0, 0.3, 0.5) for s in (0.0, 0.01)],
        'status': {k: 'ok' for k in KINDS}, 'reasons': {k: [] for k in KINDS}, 'g': g, 'atoms': {k: 1 for k in KINDS},
        'bundle_count': len(blist), 'list': blist, 'ranking': ranking, 'types': types,
        'crp': crp_value}
    return doc


def identity_holds(doc):
    """Σ increase + Σ decrease + CRP = Δ, per kind, of the bundles of a fixture."""
    bd = doc['summary']['bundles']
    return all(abs(bd['ranking'][k]['increase']['total'] + bd['ranking'][k]['decrease']['total'] + bd['crp'][k] - doc['totals'][k]['delta']) < 1e-9
               and abs(doc['kinds'][k]['vi_b'] - doc['kinds'][k]['vi_a'] - doc['totals'][k]['delta']) < 1e-9 for k in KINDS)


def hub(changed, total, inc, dec):
    return {'changed_partners': ['P%d' % i for i in range(changed)], 'changed_count': changed, 'partner_total': total,
            'fraction': changed / total, 'increase': inc, 'decrease': dec}


def rich(real):
    """Decrease first: a removed electrode, a layer change, two hubs carry the line decrease; two pair cards increase it."""
    doc = copy.deepcopy(real)
    b5 = mk(doc, 4, 'pair', {'line': 0.005})
    blist = [
        mk(doc, 0, 'electrode_removed', {'line': -0.04}, layer=None, electrode='Test2', layers=['SD1', 'PXL'],
           layer_rows=[{'layer': 'SD1', 'state': 'a_only', 'line': -0.03, 'pixel': None, 'cno': None},
                       {'layer': 'PXL', 'state': 'a_only', 'line': -0.01, 'pixel': None, 'cno': None}]),
        mk(doc, 1, 'layer_change', {'line': -0.02, 'pixel': -0.003}, layer=None, electrode='Data', layers=['GAT2', 'SD1'],
           layer_rows=[{'layer': 'GAT2', 'state': 'b_only', 'line': -0.015, 'pixel': -0.003, 'cno': None},
                       {'layer': 'SD1', 'state': 'a_only', 'line': -0.005, 'pixel': None, 'cno': None}],
           **{'from': ['SD1'], 'to': ['GAT2']}),
        mk(doc, 2, 'hub', {'line': -0.01}, layer='SD1', electrode='Scan3', partner_count=9, hub=hub(8, 10, 4, 4),
           partners_elsewhere=[{'partner': 'P1', 'bundle_id': b5['id']}, {'partner': 'P2', 'bundle_id': b5['id']}]),
        mk(doc, 3, 'hub', {'line': -0.0001}, layer='SD1', electrode='Hub2', partner_count=6, hub=hub(5, 6, 5, 0)),
        mk(doc, 5, 'pair', {'line': 0.015, 'pixel': 0.02}), b5,
        mk(doc, 6, 'pair', {'line': 0.0}, abs={'line': 0.01}),
    ]
    return finish(doc, blist)


def unit_of(doc, kind='line', pos=0):
    return cards_of(doc, kind)[pos]


def bundle_of_id(doc, uid):
    return next(b for b in doc['summary']['bundles']['list'] if b['id'] == uid)


# ---- the seven sections, the frame ------------------------------------------------------------------------------------------

def test_seven_sections_in_order_with_a_head_and_a_pointer_each(real):
    page = render(real)
    ids = [m for m in re.findall(r'<section id="(\w+)">', page)]
    assert ids == ['s1', 's2', 's3', 's4', 's5', 's6', 'sA']
    for sid in ids:
        s = new_section(page, sid)
        assert s.startswith('<section id="%s"><p class="q">' % sid) and '<h2>' in s and 'class="nextl"' in s
    assert 'id="purpose"' in page and 'id="pair-find"' in new_section(page, 's4') and 'id="s3-detail"' in new_section(page, 's3')       # rexp/T2
    text = all_text(page)
    for old in '①②③④⑤⑥⑦':
        assert old not in text, old
    assert '때문' not in text


def test_section_1_has_no_table_and_names_the_cause_candidates_with_links(real):
    s1 = new_section(render(real), 's1')
    assert '<table' not in s1
    # rnarr/T12 (M1): 변화량 is retired; the sentence names the two factors and the candidates that hold the main one
    assert 'id="opinion"' in s1 and '검산' in s1 and '감소 요인은 없고, 증가 요인은 <b>+' in s1 and '0%p</b>, 증가' not in s1 and '변화량' not in s1
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


def test_the_screen_has_no_04_roi_and_no_location_cards(tmp_path):
    doc, maps, out = map_report(tmp_path, changes=ok_changes())
    page = html_of(out)
    text = visible(page)
    for word in ('형상 태그', '변경 위치', '#1 ', 'roi-table', '위치 카드', '겹쳐 보기'):
        assert word not in text, word
    for gone in ('id="roi-ref"', 'id="roi-table"', 'id="card-ref"', 'id="card-store"', 'data-f="entry"', 'data-f="tab"', 'data-f="kind"', 'data-f="cat"',
                 'id="pl-table"', 'id="pl-filters"', 'id="s3-panel"'):
        assert gone not in page, gone


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
            empty = {'total': None, 'k80': None, 'cards': [], 'rest': [], 'cover': None}
            b['ranking'][kind] = {'status': st, 'direction': None, 'main': None, 'increase': dict(empty), 'decrease': dict(empty),
                                  'zero': [], 'cancel_ratio': None, 'net_total': None}
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
    assert s1.count('가장 큰 증가 요인은') == 1 and 'line 증가 요인' not in s1.split('pixel 원인 후보는')[1]     # the line sentence stays, pixel names no candidate


def test_h4_six_cards_or_fewer_hold_everything_and_more_say_the_rest(real):
    s1 = new_section(render(real), 's1')
    assert '증가 요인은 원인 후보 3 개에 모두 있습니다' in s1 and '나머지' not in s1

    def more(d):
        rk = d['summary']['bundles']['ranking']['line']
        rk[rk['main']]['rest'] = [cards_of(d, 'pixel')[0]]
        rk[rk['main']]['cover'] = 0.9
    s1 = new_section(render(variant(real, more)), 's1')
    assert '나머지 1 원인 후보가 10% 를 나눠 가집니다' in s1 and '모두 있습니다' not in s1.split('pixel vi 는')[0]


def test_a_one_electrode_card_and_the_hub_sentence_numbers(real):
    def own(d):
        b = d['summary']['bundles']['list'][0]
        b.update(type='self', electrode='X', layer='L1', electrodes=None, pair_id=None, partner_count=0, partners=[], why=[])
    s3 = new_section(render(variant(real, own)), 's3')
    assert 'X 전극 안의 L1 불량이 변했습니다' in s3 and 'X · L1 — 동일 전극' in s3

    def mixed(d):
        b = d['summary']['bundles']['list'][0]
        b.update(type='hub', electrode='X', layer='L1', electrodes=None, pair_id=None, partner_count=9, partners=['a'] * 9,
                 atoms={'increase': 1, 'decrease': 1, 'count': 2}, hub=hub(4, 5, 3, 1))
    s3 = new_section(render(variant(real, mixed)), 's3')
    # R17:3: the numbers are the judged partners (hub.*), not partner_count (9) nor the atom counts
    assert '상대 4 개와의 불량이 함께 변했습니다(증가 3 · 감소 1)' in s3 and 'X · L1 — 다중 쌍 변화(4 쌍: 증가 3 · 감소 1)' in s3
    assert '상대 9 개' not in s3


def test_r17b_2_the_hub_verb_follows_the_judged_partners_not_the_bundle_net(real):
    doc = rich(real)
    s3 = new_section(render(doc), 's3')
    hub2 = bundle_of_id(doc, next(b['id'] for b in doc['summary']['bundles']['list'] if b['electrode'] == 'Hub2'))
    assert hub2['kinds']['line']['net'] < 0                       # the bundle net is a decrease ...
    assert 'Hub2 전극의 SD1 에서 상대 5 개와의 불량이 함께 증가했습니다.' in s3   # ... the judged partners all increased
    assert 'Scan3 전극의 SD1 에서 상대 8 개와의 불량이 함께 변했습니다(증가 4 · 감소 4).' in s3


def test_r17b_3_a_hub_card_names_changed_partners_of_all_and_those_on_other_cards(real):
    s3 = new_section(render(rich(real)), 's3')
    assert '변한 상대 8 / 전체 10 · 그중 2 개는 다른 카드' in s3
    assert '변한 상대 5 / 전체 6</p>' in s3                           # no other card: the clause is left out


def test_r17b_1_the_assignment_rules_say_the_partner_signs_join_line_and_pixel(real):
    s3 = new_section(render(real), 's3')
    rules = s3[s3.index('id="assign-rules"'):]
    assert '상대별 증가·감소는 line·pixel 순 변화를 합친 부호입니다.' in rules[:rules.index('</details>')]
    assert '허브 기준: 변한 상대가 3 개 이상이고 전체 상대의 50% 이상입니다' in rules


def test_conflict_sentence_and_card_badge(real):
    def edit(d):
        for b in d['summary']['bundles']['list']:
            b['conflict'] = True
            b['kinds']['line']['net'], b['kinds']['pixel']['net'] = 0.01, -0.01
    page = render(variant(real, edit))
    assert '같은 변화가 pixel 을 감소시켰습니다' in new_section(page, 's1') and 'b-mv">상충' in new_section(page, 's3')


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
    assert '쌍 전체 변화' in card and '이 카드 순 변화' in card
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
    assert '변경 위치(04, 보조)' not in page


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
    # the real fixture is CRP-dominated (pixel vi decreases, the cause candidates increase): R18:1 names the CRP once, as the bridge sentence
    bridge = '보정값(CRP) 차이 %s 를 빼면 원인 후보의 순 변화는 증가입니다.' % signed
    assert bridge in s1 and '보정값(CRP) 차이 %s' % signed in s4          # the same signed text in 1 and 4
    assert '원인 후보 3 개에 모두 있습니다' in s1 and '는 보정값(CRP) 차이에서 나왔고' not in s1
    assert s1.index('증가 요인은 +') < s1.index(bridge) < s1.rindex('원인 후보 3 개에 모두 있습니다')

    # not dominated (the vi change and the cause candidates go the same way): the old CRP sentence after the card sentences
    doc = copy.deepcopy(real)
    finish(doc, [mk(doc, 0, 'pair', {'line': 0.01, 'pixel': -0.02}), mk(doc, 1, 'pair', {'pixel': -0.01})], crp=-0.01)
    assert identity_holds(doc)
    s1b = all_text(new_section(render(doc), 's1'))
    sentence = 'pixel 변화 중 %s 는 보정값(CRP) 차이에서 나왔고, 원인 후보에는 들어 있지 않습니다.' % ledger_report.raw_pp(-0.01)
    assert sentence in s1b and '를 빼면' not in s1b and s1b.rindex('원인 후보 2 개에 모두 있습니다') < s1b.index(sentence)

    zero = new_section(render(variant(real, lambda d: _statuses(d, crp=0.0))), 's1')
    assert '보정값(CRP) 차이에서 나왔' not in zero


def test_r12_5_a_missing_rule_ratio_is_a_dash_not_an_error(real):
    def edit(d):
        d['summary']['rebase']['ratios']['line']['r_rule'] = None
    s6 = new_section(render(variant(real, edit)), 's6')
    row = re.search(r'<tr><td class="l">유효 해상도 규칙\(비\)</td>(.*?)</tr>', s6, re.S)
    assert row and '판정 불가' in row.group(1) and '—' in row.group(1)


def test_r12_6_the_missing_04_result_is_not_told_at_all(tmp_path):
    from test_ledger_report_maps import setup as maps_setup
    from test_ledger_report_v2 import ledger_report_run
    doc, folder, maps = maps_setup(tmp_path)
    page = html_of(ledger_report_run(tmp_path, folder, maps, 'rn_b6'))
    assert 'id="band-changes"' not in page and '04 결과 없음' not in page and 'id="roi-ref"' not in page and 'id="locations"' not in page


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


def test_r12_3a_a_layer_change_card_has_its_title_type_layer_table_and_row_layers(real):
    doc = rich(real)
    uid = next(b['id'] for b in doc['summary']['bundles']['list'] if b['type'] == 'layer_change')
    page = render(doc)
    card = page[page.index('<div class="bcard" id="%s"' % uid):]
    card = card[:card.index('<h4>근거</h4>')]
    assert '<b class="bt">Data — 레이어 변경(GAT2 추가 · SD1 제거)</b>' in card and 'badge b-mv">레이어 변경' in card
    assert 'Data 전극의 레이어 구성이 바뀌었습니다(GAT2 추가 · SD1 제거).' in card
    # the per-layer table shows every layer of the bundle (not the first one only)
    table = card[card.index('<h4>레이어별 내역</h4>'):card.index('<h4>어떤 불량이 어떻게</h4>')]
    assert '추가(B 에만)' in table and '제거(A 에만)' in table and '<b>GAT2</b>' in table and '<b>SD1</b>' in table
    assert re.search(r'<span class="muted small">GAT2</span></td>', card)


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
    dom, text = dump_dom(real_page, '#sel=pair:%s&it=%s&to=explore' % (pair['pair_id'], item))
    assert 'data-err' not in dom.root and dom.root['data-dp-pair'] == pair['pair_id']
    assert [a for t, a in dom.elements if t == 'tr' and 'itsel' in (a.get('class') or '').split()]       # the item row of the detail panel (③)


# ---- rnarr/T12: the direction reading (K4 / M1 / M2), the area reading of 6, the hub sensitivity, the old inputs -----------------

def test_decrease_first_names_the_decrease_factor_then_the_biggest_opposite_one(real):
    doc = rich(real)
    line = doc['summary']['bundles']['ranking']['line']
    assert line['direction'] == 'decrease' and line['decrease']['cards'][0] != line['increase']['cards'][0]
    first, opposite = bundle_of_id(doc, line['decrease']['cards'][0]), bundle_of_id(doc, line['increase']['cards'][0])
    assert first['kinds']['line']['net'] < 0 < opposite['kinds']['line']['net']
    page = render(doc)
    s1 = new_section(page, 's1')
    assert '감소 요인은 <b>−' in s1 and 'href="#unit=%s&amp;to=s3">Test2 — B 에서 삭제(SD1·PXL)</a>, line −4.0000%%p' % first['id'] in s1
    assert s1.index('가장 큰 감소 요인은') < s1.index('가장 큰 반대 방향 요인은')
    assert 'href="#unit=%s&amp;to=s3"' % opposite['id'] in s1[s1.index('가장 큰 반대 방향 요인은'):]
    assert '감소 요인의 ' in s1 and '% 는 \'전극 삭제\' 유형에서 나왔습니다' in s1            # the type sentence, one sentence per thought
    s3 = new_section(page, 's3')
    assert s3.index('id="cards-line"') < s3.index('id="cards-line-opposite"')
    assert s3.index('id="%s"' % first['id']) < s3.index('id="%s"' % opposite['id'])
    assert 'vi 변화 방향 — 감소 요인 상위 4 개' in s3 and '반대 방향 요인 — 증가 요인 상위 2 개' in s3


def test_balanced_says_so_and_shows_both_directions(real):
    doc = copy.deepcopy(real)
    finish(doc, [mk(doc, 0, 'pair', {'line': 0.02}), mk(doc, 1, 'pair', {'line': -0.02}), mk(doc, 2, 'pair', {'pixel': 0.01})])
    assert doc['summary']['bundles']['ranking']['line']['direction'] == 'balanced'
    page = render(doc)
    assert '증가 요인과 감소 요인이 비슷합니다' in new_section(page, 's1')
    s3 = new_section(page, 's3')
    assert 'id="cards-line"' in s3 and 'id="cards-line-decrease"' in s3 and 'id="cards-line-opposite"' not in s3


def test_a_spread_out_direction_is_read_through_the_type_totals(real):
    doc = copy.deepcopy(real)
    finish(doc, [mk(doc, i, 'pair', {'line': -0.001}, salt=i * 1000) for i in range(30)] + [mk(doc, 0, 'pair', {'pixel': 0.01}, salt=77)])
    rk = doc['summary']['bundles']['ranking']['line']
    assert rk['decrease']['k80'] > 20 and len(rk['decrease']['cards']) == 6
    page = render(doc)
    assert '변화가 넓게 퍼져 있어 유형별 합계로 봅니다' in new_section(page, 's1')
    assert 'id="spread-line"' in new_section(page, 's3') and 'id="spread-pixel"' not in new_section(page, 's3')


def test_m2_the_type_table_has_no_crp_row_and_a_crp_difference_gets_one_line_below_it(real):
    s3 = new_section(render(real), 's3')
    table = s3[s3.index('id="type-table"'):s3.index('</table>', s3.index('id="type-table"'))]
    assert 'CRP' not in table and '합(원인 후보)' in table and '모든 CNO 합(합계 제외)' in table
    assert all(name in table for name in ('전극 신규', '전극 삭제', '레이어 변경', '다중 쌍 변화', '단일 쌍 변화', '동일 전극', 'CNO 단독'))
    note = s3[s3.index('id="types-crp"'):]
    note = note[:note.index('</p>')]
    assert 'pixel 의 보정값(CRP) 차이' in note and ledger_report.pp_txt(real['summary']['bundles']['crp']['pixel']) in note
    zero = new_section(render(variant(real, lambda d: _statuses(d, crp=0.0))), 's3')
    assert 'id="types-crp"' not in zero
    assert 'class="sum"' in table and table.count('<tr data-type=') == 7


def test_an_electrode_card_lists_every_layer_and_a_removed_electrode_says_so(real):
    doc = rich(real)
    uid = next(b['id'] for b in doc['summary']['bundles']['list'] if b['type'] == 'electrode_removed')
    page = render(doc)
    card = page[page.index('<div class="bcard" id="%s"' % uid):]
    card = card[:card.index('<h4>근거</h4>')]
    table = card[card.index('<h4>레이어별 내역</h4>'):card.index('<h4>어떤 불량이 어떻게</h4>')]
    assert 'Test2 전극이 B 에서 삭제되었습니다(SD1·PXL).' in card and table.count('<td class="l">삭제</td>') == 2
    assert '<b>SD1</b>' in table and '<b>PXL</b>' in table
    assert '이 카드 순 변화' in card and '쌍 전체 변화' in card


def test_m3_every_card_has_the_map_and_the_correspondence_buttons_with_a_whitelisted_id(real):
    doc = rich(real)
    s3 = new_section(render(doc), 's3')
    for b in doc['summary']['bundles']['list']:
        if b['id'] in cards_of(doc, 'line'):
            assert 'href="#sel=unit:%s"' % b['id'] in s3 and 'href="#unit=%s&amp;to=s2"' % b['id'] in s3
    assert '상세 보기 ▸' in s3 and '지도에서 보기' not in s3 and '제품 대응 ▸' in s3
    assert all(re.fullmatch(r'bd-[0-9a-f]{16,24}', i) for i in re.findall(r'#sel=unit:([^&"]+)', s3))


def test_a_card_with_an_internal_cancel_says_so(real):
    s3 = new_section(render(rich(real)), 's3')
    assert '내부 상쇄' in s3
    assert 'id="bd-rest-line"' in s3 and '순 변화 0' in s3[s3.index('id="bd-rest-line"'):]


def test_every_screen_text_uses_the_m1_names(real):
    pages = [render(real), render(rich(real))]
    doc = copy.deepcopy(real)
    finish(doc, [mk(doc, 0, 'pair', {'line': 0.02}), mk(doc, 1, 'pair', {'line': -0.02})])
    pages.append(render(doc))
    for page in pages:
        text = all_text(page)
        for old in OLD_TERMS:
            assert old not in text, old
        assert not re.search(r'\bnet\b', text)
        assert '때문' not in text
    assert '증가 요인' in all_text(pages[1]) and '다중 쌍 변화' in all_text(pages[1])


def _area_doc(real, unit_a=(50.0, 50.0), unit_b=(60.0, 60.0)):
    def edit(d):
        d['inputs']['products']['reference']['unit_size_um'] = list(unit_a)
        d['inputs']['products']['target']['unit_size_um'] = list(unit_b)
        for kind, base in (('line', 0.0123), ('pixel', 0.0045)):
            des = d['summary']['design'][kind]
            des['basis'] = 'area'
            des['negligible'] = []
            des['segments'].update(design=0.0777, design_area=base, unit_size=base / 3, resolution=base / 5)
            des['names']['show_outside_conditions'] = False
    return variant(real, edit)


def test_r12_10b_no_design_or_screen_area_number_outside_6_when_the_area_basis_is_used(real):
    doc = _area_doc(real)
    page = render(doc)
    seg = doc['summary']['design']['line']['segments']
    s6 = new_section(page, 's6')
    for value in (seg['design_area'], seg['unit_size']):
        assert ledger_report.pp_txt(value) in s6
        for sid in ('s1', 's2', 's3', 's4', 's5', 'sA'):
            assert ledger_report.pp_txt(value) not in all_text(new_section(page, sid)), (sid, value)


def test_6_area_basis_names_the_area_design_and_the_screen_area_with_its_two_parts(real):
    doc = _area_doc(real)
    s6 = new_section(render(doc), 's6')
    seg = doc['summary']['design']['line']['segments']
    pp = ledger_report.pp_txt
    assert '같은 면적이면 설계 차이는, 화면 면적 차이는?' in s6 and '면적당 설계' in s6
    assert '면적당 설계 차이는 <b>%s</b>' % pp(seg['design_area']) in s6
    assert '(unit 크기 %s + unit 수 %s)' % (pp(seg['unit_size']), pp(seg['resolution'])) in s6
    table = s6[s6.index('id="design-table"'):s6.index('</table>', s6.index('id="design-table"'))]
    assert '= 면적당 설계 차이' in table and '화면 면적' in table and pp(seg['design']) not in s6      # ca is not added next to design_area
    fold = s6[s6.index('id="area-split"'):]
    fold = fold[:fold.index('</details>')]
    assert 'unit 크기 몫' in fold and 'unit 수 몫' in fold
    cond = s6[s6.index('id="cond6"'):s6.index('</table>', s6.index('id="cond6"'))]
    assert 'unit 면적(크기², µm²)' in cond and '2500' in cond and '3600' in cond and '화면 면적(unit 면적 × unit 수, µm²)' in cond
    assert '2×2 창 경계 효과는 면적당 설계 몫에 남습니다' in s6 and '"같은 면적이면" 의 비교입니다' in s6
    assert 'id="s6-unit-basis"' not in s6


def test_6_unit_basis_without_unit_sizes_keeps_the_old_reading_and_says_why(real):
    s6 = new_section(render(real), 's6')
    assert 'id="s6-unit-basis"' in s6 and 'unit 크기 정보가 없어 unit 기준으로 비교합니다' in s6
    assert '설계 차이' in s6 and '면적당 설계' not in s6 and 'id="area-split"' not in s6


def test_6_the_head_sentence_of_an_area_basis_uses_the_design_area_not_the_old_design(real):
    doc = _area_doc(real)
    head = new_section(render(doc), 's6').split('<h3>')[0]
    pp = ledger_report.pp_txt
    assert pp(doc['summary']['design']['line']['segments']['design_area']) in head
    assert pp(doc['summary']['design']['line']['segments']['design']) not in head


def test_m4_the_appendix_has_the_hub_sensitivity_table_with_the_current_setting_marked(real):
    sA = new_section(render(real), 'sA')
    table = sA[sA.index('id="hub-sensitivity"'):sA.index('</table>', sA.index('id="hub-sensitivity"'))]
    assert table.count('<tr') == 1 + 6                                  # head + 3 ratios x 2 sizes
    assert table.count('현재 설정') == 1 and '<tr class="sum"><td class="l">50%</td><td class="l">0</td>' in table
    assert '<td class="l">30%</td><td class="l">1%</td>' in table
    checks = sA[sA.index('id="bundle-checks"'):sA.index('</table>', sA.index('id="bundle-checks"'))]
    for text in ('방향 검산', '목록 검산', '유형 검산'):
        assert text in checks, text


def _legacy(real):
    def edit(d):
        b = d['summary']['bundles']
        for key in ('version', 'types', 'hub_sensitivity', 'hub_min_fraction', 'hub_min_share'):
            b.pop(key, None)
        for kind in KINDS:
            rk = b['ranking'][kind]
            main = rk['main']
            cover = rk[main]['cover']
            b['ranking'][kind] = {'status': 'ok', 'k80': rk[main]['k80'], 'cards': list(rk[main]['cards']), 'cover': cover,
                                  'rest_share': 1.0 - cover, 'rest': list(rk[main]['rest'])}
    return variant(real, edit)


def test_an_older_ledger_without_a_bundle_version_keeps_the_old_reading_and_says_to_rerun(real):
    page = render(_legacy(real))
    for sid in ('s1', 's3'):
        s = new_section(page, sid)
        assert '총 변동 기준으로 순위 매긴 옛 방식' in s and '02·08 을 새 번들로 다시 실행하세요' in s, sid
    s3 = new_section(page, 's3')
    assert 'id="type-table"' not in s3 and 'id="cards-line"' in s3 and 'id="cards-pixel"' in s3
    assert '총 변동의' in new_section(page, 's1')
    assert '02·08' in new_section(page, 'sA')
    assert render(_legacy(real)) == page


def test_an_older_compare2_without_the_area_basis_still_renders_the_unit_reading(real):
    def edit(d):
        for kind in KINDS:
            d['summary']['design'][kind].pop('basis', None)
            for key in ('design_area', 'unit_size'):
                d['summary']['design'][kind]['segments'].pop(key, None)
    s6 = new_section(render(variant(real, edit)), 's6')
    assert '02·08 을 새 번들로 다시 실행하면 면적 기준이 나옵니다' in s6 and 'id="design-table"' in s6


def test_the_rich_page_is_the_same_bytes_twice_and_section_1_has_no_table(real):
    doc = rich(real)
    assert render(doc) == render(copy.deepcopy(doc))
    assert '<table' not in new_section(render(doc), 's1')


# ---- #sel=unit:<id> (M3), headless Chrome -----------------------------------------------------------------------------------------

def _unit_page(tmp_path):
    doc = sample_doc()
    p0, p1 = doc['pairs'][0], doc['pairs'][1]
    layer = p0['items'][0]['analysis_layer']
    one = mk(doc, 0, 'pair', {'line': -0.01}, layer=layer)
    two = mk(doc, 1, 'pair', {'line': 0.01}, layer=p1['items'][0]['analysis_layer'])
    finish(doc, [one, two])
    doc, maps, out = map_report(tmp_path, doc=doc, changes=ok_changes())
    return doc, one, p0, layer, out / 'ledger_report.html'


def test_chrome_sel_unit_opens_the_detail_panel_of_that_candidate_only(tmp_path):
    doc, one, p0, layer, page = _unit_page(tmp_path)
    assert len(doc['pairs']) > 2
    dom, text = dump_dom(page, '#sel=unit:%s&to=explore' % one['id'])
    assert 'data-err' not in dom.root and dom.root['data-sel'] == 'unit:' + one['id']
    assert dom.root['data-dp-unit'] == one['id'] and dom.root['data-dp-pair'] == p0['pair_id']        # ② the candidate's pair is picked, ③④ follow it
    shown = [a['data-dp'] for t, a in dom.elements if a.get('data-dp')]
    assert shown == [p0['pair_id']]                                              # ② lists the pairs of the candidate only
    layers = [a for t, a in dom.elements if t == 'option']
    assert [a['value'] for a in layers] == [layer] and dom.root['data-dp-layer'] == layer         # ④ the layer select holds the layers of the pair
    assert dom.root['data-dp-drawn'] == '2' and dom.root.get('data-map-check') == 'passed'
    assert 'id="unit-band"' not in text and one['id'] in text


def test_chrome_an_unknown_or_malformed_unit_selection_is_ignored(tmp_path):
    doc, one, p0, layer, page = _unit_page(tmp_path)
    for fragment in ('#sel=unit:bd-0123456789abcdef', '#sel=unit:%3Cscript%3E', '#sel=unit:' + one['id'][:-1] + 'Z', '#sel=unit:', '#sel=pair:P1&cand=bd-zzzz'):
        dom, text = dump_dom(page, fragment)
        assert 'data-err' not in dom.root and dom.root.get('data-sel', '') in ('', 'pair:P1'), fragment
        assert dom.root.get('data-dp-unit', '') == '' or fragment.startswith('#sel=pair'), fragment
    dom, text = dump_dom(page, '#sel=unit:bd-0123456789abcdef')
    assert 'id="dp-empty"' in text                                               # the empty prompt stays
    dom, text = dump_dom(page, '#sel=pair:%s' % p0['pair_id'])             # a pair selection opens the panel on that pair (its candidate too)
    assert 'data-err' not in dom.root and dom.root['data-sel'] == 'pair:' + p0['pair_id']
    assert dom.root['data-dp-pair'] == p0['pair_id'] and dom.root['data-dp-unit'] == one['id']


def test_chrome_the_rich_page_runs_without_a_script_error(tmp_path, real):
    doc = rich(real)
    folder = write_ledger(tmp_path / 'led', doc)
    out = tmp_path / 'out'
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None) == 0
    uid = unit_of(doc)
    for fragment in ('#', '#sel=unit:%s&to=explore' % uid, '#unit=%s&to=s3' % uid):
        dom, text = dump_dom(out / 'ledger_report.html', fragment)
        assert 'data-err' not in dom.root, fragment


# ---- rnarr/T12b (R18) --------------------------------------------------------------------------------------------------------------

def _crp_dominated(real):
    """pixel: Σ net +0.2%p, CRP −0.6%p, Δ −0.4%p (the identity holds); line: −1.0%p, no CRP."""
    doc = copy.deepcopy(real)
    finish(doc, [mk(doc, 0, 'pair', {'line': -0.01, 'pixel': 0.002}), mk(doc, 1, 'pair', {'line': -0.0002})], crp=-0.006)
    return doc


def test_r18_1_a_crp_dominated_kind_says_the_cause_candidate_direction_not_the_vi_direction(real):
    doc = _crp_dominated(real)
    assert identity_holds(doc) and abs(doc['totals']['pixel']['delta'] + 0.004) < 1e-12
    page = render(doc)
    s1, s3 = all_text(new_section(page, 's1')), all_text(new_section(page, 's3'))
    pixel = s1[s1.index('pixel vi 는'):]
    pixel = pixel[:pixel.index('원인 후보 값에는') if '원인 후보 값에는' in pixel else len(pixel)]
    print('R18 1 pixel part:', pixel)
    assert '−0.4000%p 감소했습니다' in pixel and '감소 요인은 없고, 증가 요인은 +0.2000%p' in pixel
    assert '보정값(CRP) 차이 −0.6000%p 를 빼면 원인 후보의 순 변화는 증가입니다.' in pixel
    assert pixel.index('증가 요인은') < pixel.index('를 빼면') and s1.count('보정값(CRP)') == 1       # said once, not twice
    head = [l for l in s3.split('pixel: ') if l.startswith('원인 후보 순 변화 방향은')]
    assert head, s3
    print('R18 3 head:', 'pixel: ' + head[0][:120])
    assert 'pixel: vi 변화 방향' not in s3 and 'line: vi 변화 방향은 감소 입니다' in s3
    assert '원인 후보 순 변화 방향 — 증가 요인 상위' in s3 and '원인 후보 순 변화 방향(증가 요인)의 100%' in s3
    assert 'vi 변화 방향 — 감소 요인 상위' in s3                                    # line is not CRP-dominated
    assert '원인 후보 순 변화(보정값 차이 제외)의 부호' in s3 and 'pixel vi 는 A' in s1
    for text in (s1, s3):                                                          # no sentence says the pixel vi direction is "increase"
        assert not re.search(r'pixel[^.]*vi 변화 방향은 증가', text)


def test_r18_1_a_kind_that_goes_the_same_way_keeps_the_vi_direction_wording(real):
    doc = copy.deepcopy(real)
    finish(doc, [mk(doc, 0, 'pair', {'line': -0.01, 'pixel': -0.003})], crp=-0.001)
    s3 = all_text(new_section(render(doc), 's3'))
    assert 'pixel: vi 변화 방향은 감소 입니다' in s3 and '원인 후보 순 변화 방향' not in s3.replace('\'원인 후보 순 변화 방향\' 이라고', '')


def test_r18_2_the_fixture_identity_holds_for_every_finished_fixture(real):
    for doc in (rich(real), _crp_dominated(real)):
        assert identity_holds(doc)


def test_r18_4_an_older_ledger_hub_without_a_partner_is_the_same_electrode_type(real):
    doc = copy.deepcopy(real)
    finish(doc, [mk(doc, 0, 'hub', {'line': -0.01}, layer='SD1', electrode='Solo', partner_count=0, hub=None), mk(doc, 1, 'pair', {'pixel': 0.01})])
    page = render(_legacy(doc))
    text = all_text(new_section(page, 's3'))
    assert 'Solo · SD1 — 동일 전극' in text and 'Solo 전극 안의 SD1 불량이 변했습니다.' in text
    assert '상대 0 개' not in text and '0 쌍' not in text


def test_r18_5_section_6_names_why_the_unit_basis_is_used(real):
    assert 'unit 크기 정보가 없어 unit 기준으로 비교합니다' in new_section(render(real), 's6')
    old = variant(real, lambda d: [d['summary']['design'][k].pop('basis', None) for k in KINDS])
    s6 = all_text(new_section(render(old), 's6'))
    assert '02·08 을 새 번들로 다시 실행하면 면적 기준이 나옵니다' in s6 and 'unit 크기 정보가 없어' not in s6

    def edit(d):
        for k in KINDS:
            d['summary']['design'][k]['basis'] = 'unit'
        d['summary']['rebase']['area_basis'] = {'status': 'unavailable', 'reasons': ['u_area_nonpositive']}
        for role in ('reference', 'target'):
            d['inputs']['products'][role]['unit_size_um'] = [50.0, 50.0]
    s6 = all_text(new_section(render(variant(real, edit)), 's6'))
    assert '면적 기준을 계산할 수 없어(u_area_nonpositive) unit 기준으로 비교합니다' in s6


def _two_layer_page(tmp_path):
    doc = sample_doc()
    p0 = doc['pairs'][0]
    p1 = next(p for p in doc['pairs'][1:] if p['items'][0]['analysis_layer'] != p0['items'][0]['analysis_layer'])
    l0, l1 = p0['items'][0]['analysis_layer'], p1['items'][0]['analysis_layer']
    b = mk(doc, 0, 'electrode_removed', {'line': -0.01}, layer=None, electrode=p0['electrodes'][0], layers=[l0, l1])
    row0 = b['rows'][0]
    b['rows'] = [dict(row0, layer=l0), dict(row0, pair_id=p1['pair_id'], layer=l1, item_ids=[p1['items'][0]['item_id']]),
                 {'kind': 'pixel', 'pair_id': None, 'cno': True, 'layer': l0, 'partner': None, 'electrodes': None, 'net': 0.001, 'abs': 0.001,
                  'presence': ['gone'], 'item_ids': ['CNO:%s' % p0['electrodes'][0]], 'pair_delta': None}]
    finish(doc, [b])
    doc, maps, out = map_report(tmp_path, doc=doc, changes=ok_changes())
    return doc, b, p0, p1, l0, l1, out / 'ledger_report.html'


def test_chrome_r18_3_a_two_layer_candidate_has_both_layers_both_pairs_and_counts_cno_apart(tmp_path):
    doc, b, p0, p1, l0, l1, page = _two_layer_page(tmp_path)
    dom, text = dump_dom(page, '#sel=unit:%s&to=explore' % b['id'])
    assert 'data-err' not in dom.root and dom.root['data-sel'] == 'unit:' + b['id']
    assert dom.root['data-sel-items'] == '2'                                          # the two item ids of the two pairs, not the CNO id
    shown = {a['data-dp'] for t, a in dom.elements if a.get('data-dp')}
    assert shown == {p0['pair_id'], p1['pair_id']}                                    # ② both pairs of the candidate
    cno = [a for t, a in dom.elements if a.get('id') == 'dp-cno']
    assert cno and 'CNO 1 건 — 지도에 없음' in re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text[text.index('id="dp-cno"'):]))[:80]
    dom2, text2 = dump_dom(page, '#sel=pair:%s&cand=%s' % (p1['pair_id'], b['id']))      # the second pair: the candidate stays
    assert 'data-err' not in dom2.root and dom2.root['data-dp-unit'] == b['id'] and dom2.root['data-dp-pair'] == p1['pair_id']
    assert dom2.root['data-dp-layer'] == l1 and {a['data-dp'] for t, a in dom2.elements if a.get('data-dp')} == {p0['pair_id'], p1['pair_id']}


def test_r18_6_the_bundle_data_carries_item_positions_not_item_ids(tmp_path):
    n = 500
    pairs = [spec('P%d' % i, ['E%d' % i, 'F%d' % i], 'line', 0.001, 0.002) for i in range(n)]
    doc = make_ledger(pairs)
    b = mk(doc, 0, 'electrode_removed', {'line': -0.01}, layer=None, electrode='E0', layers=['L1'])
    row0 = b['rows'][0]
    b['rows'] = [dict(row0, pair_id=p['pair_id'], item_ids=[i['item_id'] for i in p['items']]) for p in doc['pairs']]
    finish(doc, [b])
    page = ledger_report.render_html(doc, 'a' * 64, 30, 30)[0]
    data = json.loads(re.search(r'<script type="application/json" id="report-data">(.*?)</script>', page, re.S).group(1))
    unit = data['bundles'][0]
    assert 'its' not in unit and len(unit['pairs']) == n and unit['cno'] == 0
    assert all(v[3] == [0] for v in unit['pairs'].values())
    old_bytes = len(json.dumps(sorted(i['item_id'] for p in doc['pairs'] for i in p['items']), ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    new_bytes = len(json.dumps([v[3] for v in unit['pairs'].values()], separators=(',', ':')).encode('utf-8'))
    print('R18 6 html bytes after:', len(page.encode('utf-8')), '| its before:', old_bytes, 'after:', new_bytes,
          '| html before ~', len(page.encode('utf-8')) - new_bytes + old_bytes)
    assert new_bytes < old_bytes / 3
