"""pasx/T12: `ledger-maps --no-cards` (astra:9, PLAN-v2.1 J).

Default (no flag): the output is what it was before the flag existed. `--no-cards`: the card list, the four card checks and
the `options` record differ; numbers, maps and every other field stay equal. Under a narrow budget the card hold is gone,
so the occurrence metric of the second product may have *more* values (pending goes down) and nothing moves any other way."""
import json

import pytest

from simcompare import ledger_maps as lm
import test_ledger_maps as base
from test_ledger_maps_cards import MAP_OPTS, big   # noqa: F401  (big is a fixture)
from test_ledger_maps_v2 import two_card_chain

OPTS = {'tile_um': 0.5, 'range_um': 0.5, 'linear_min_length_um': 5, 'segment_um': 7, 'coord_tolerance_um': 1.0}
CARD_CHECK_KEYS = ('range_sum', 'pair_bound', 'partition', 'row_items')


def run(tmp, out, cards=True, limit=None):
    code = lm.main_ledger_maps(str(tmp / 'out'), str(tmp / 'compare2.yaml'), str(tmp / 'led'), str(tmp / out),
                               lambda record: None, options=OPTS, cards=cards, max_operation_bytes=limit)
    return code, tmp / out


def pngs(folder):
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir()) if p.suffix == '.png'}


def load(folder):
    return json.loads((folder / 'ledger_maps.json').read_text(encoding='utf-8'))


@pytest.fixture
def chain(tmp_path, big):
    two_card_chain(tmp_path, big)
    return tmp_path


def test_default_output_has_no_cards_key_and_is_repeatable(chain):
    code, first = run(chain, 'a')
    code2, second = run(chain, 'b', cards=True)
    assert code == code2 == 0
    doc = load(first)
    assert 'cards' not in doc['options'] and [c['pair_id'] for c in doc['cards']] == ['AB', 'GH']
    assert base.results_hash(first) == base.results_hash(second)          # byte for byte (execution.json is not compared)


def test_cli_flag_default_off_and_on(chain):
    plain_code, plain = base.run_maps(chain, 'plain', *MAP_OPTS)
    explicit_code, explicit = run(chain, 'explicit', cards=True)
    assert plain_code == explicit_code == 0 and base.results_hash(plain) == base.results_hash(explicit)
    code, folder = base.run_maps(chain, 'nocards', *MAP_OPTS, '--no-cards')
    doc = load(folder)
    assert code == 0 and doc['options']['cards'] is False and doc['cards'] == []


def test_no_cards_changes_only_the_allowed_fields_with_enough_budget(chain):
    code, with_cards = run(chain, 'cards')
    code2, without = run(chain, 'nocards', cards=False)
    assert code == code2 == 0
    a, b = load(with_cards), load(without)
    assert {key for key in a if a[key] != b.get(key)} == {'cards', 'options', 'checks'}
    assert b['cards'] == [] and b['cards_dropped_for_budget'] == []
    assert {k: v for k, v in b['options'].items() if k != 'cards'} == a['options'] and b['options']['cards'] is False
    changed = {key for key in a['checks'] if a['checks'][key] != b['checks'][key]}
    assert changed == set(CARD_CHECK_KEYS)
    for key in CARD_CHECK_KEYS:
        assert a['checks'][key]['checked'] > 0 and a['checks'][key]['status'] == 'passed'
        assert b['checks'][key] == {'status': 'not_evaluated', 'checked': 0, 'failed': [], 'failed_count': 0}
    assert a['checks']['status'] == b['checks']['status'] == 'passed'
    assert pngs(with_cards) == pngs(without) and pngs(without)              # the map pictures are the same bytes
    for name in ('sides', 'window', 'changes', 'overlay', 'occurrence', 'inputs', 'report', 'bands', 'warnings'):
        assert a.get(name) == b.get(name), name


def role_records(doc):
    return {(row['item_id'], role): row[role] for row in doc['occurrence']['items'] for role in ('reference', 'target')}


def test_narrow_budget_records_that_only_pending_occurrence_values_can_appear(chain):
    """With cards the reference part of a card holds memory while the target is read; without cards that hold is gone.
    The occurrence metric of the target product may then keep a value it had to give up (pending goes down). Every value the
    default run has is the same in the `--no-cards` run; the maps, sides and checks 1-2 never differ."""
    code, probe = run(chain, 'probe', cards=False)
    execution = json.loads((probe / 'execution.json').read_text(encoding='utf-8'))
    floor = execution['max_reserved_bytes']                 # the least limit the no-cards run needs (without occurrence)
    differing = []
    for step, offset in enumerate(list(range(0, 700, 350)) + list(range(24000, 28400, 300))):
        limit = floor + offset
        code_c, folder_c = run(chain, 'c%d' % step, cards=True, limit=limit)
        code_n, folder_n = run(chain, 'n%d' % step, cards=False, limit=limit)
        assert code_c in (0, 3) and code_n in (0, 3), offset
        a, b = load(folder_c), load(folder_n)
        assert pngs(folder_c) == pngs(folder_n) and a['sides'] == b['sides'] and a['window'] == b['window'], offset
        assert a['checks']['item_sum'] == b['checks']['item_sum'] and a['checks']['kind_sum'] == b['checks']['kind_sum']
        assert 'cards_budget_reduced' not in b['reasons'] and b['cards'] == [] and b['cards_dropped_for_budget'] == []
        before, after = role_records(a), role_records(b)
        assert set(before) == set(after)
        for key, record in before.items():
            if record['state'] != 'pending':
                assert after[key] == record, (offset, key)           # a value is never changed
            elif after[key]['state'] != 'pending':
                differing.append((offset, key))
        pend = lambda doc: sum(r['pending'] for r in doc['occurrence']['counts'].values())
        assert pend(b) <= pend(a), offset                            # pending only goes down
        assert (b['status'] == 'ok') >= (a['status'] == 'ok')       # a run the cards made partial may become ok, not the reverse
    assert differing, 'the fixture shows no occurrence difference between the two modes'
    assert all(key[1] == 'target' for _offset, key in differing)      # the second product is the one that gains room
