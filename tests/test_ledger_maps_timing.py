"""pasx/T1: `elapsed_by_stage` in the ledger-maps execution.json (observation only)."""
import json

from simcompare import ledger_maps as lm
from test_ledger_maps import chain, maps_direct

PRODUCT_KEYS = ('side_info_manifest',) + lm.STAGE_TIME_KEYS
COMMON_KEYS = ('inputs_read', 'side_info', 'geometry', 'correspondence', 'sides_total', 'changes_section', 'document',
               'json_text')


def result_bytes(folder):
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir())
            if path.suffix == '.png' or path.name == 'ledger_maps.json'}


def test_execution_json_has_elapsed_by_stage_with_non_negative_floats(tmp_path):
    chain(tmp_path)
    code, out = maps_direct(tmp_path)
    assert code in (0, 3)
    stages = json.loads((out / 'execution.json').read_text(encoding='utf-8'))['elapsed_by_stage']
    assert set(stages) == {'reference', 'target', 'common'}
    for role in ('reference', 'target'):
        assert set(stages[role]) == set(PRODUCT_KEYS)
        for key in PRODUCT_KEYS:
            assert isinstance(stages[role][key], float) and stages[role][key] >= 0, (role, key)
        # the read stage contains its parts
        assert stages[role]['read_total'] + 0.002 >= stages[role]['read_load_side']
    assert set(stages['common']) == set(COMMON_KEYS)
    for key in COMMON_KEYS:
        assert isinstance(stages['common'][key], float) and stages['common'][key] >= 0, key
    assert 'elapsed_by_stage' not in (out / 'ledger_maps.json').read_text(encoding='utf-8')


def test_the_timing_wrappers_do_not_change_ledger_maps_json_or_the_pngs(tmp_path, monkeypatch):
    chain(tmp_path)
    code, timed = maps_direct(tmp_path, 'timed')
    assert code in (0, 3)
    monkeypatch.setattr(lm, '_timed', lambda acc, name, func: func)       # wrappers switched off
    code_off, plain = maps_direct(tmp_path, 'plain')
    assert code_off == code
    a, b = result_bytes(timed), result_bytes(plain)
    assert a.keys() == b.keys() and 'ledger_maps.json' in a
    assert a == b
