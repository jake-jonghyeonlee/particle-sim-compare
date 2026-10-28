"""compare-multiproduct/T2b: window_cases_v1 (observed hotspot case composition of a model pair inside one window).

Unit tests use the synthetic 2 um x 2 um frames of test_local_delta (pitch 0.5, 4x4 cells); the window is 1 um around
(1, 1), i.e. x, y in [0.5, 1.5]. The CLI tests use the T1/T2 demo fixtures (model C = a copy of the target).
"""
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import numpy as np
import pytest

from simcompare import window_cases
from simcompare.cli import main
from simcompare.local_delta import _as_dataset
from simcompare.numeric_schema import NumericContractError

from test_confirm_pairs import env, load, sha   # noqa: F401
from test_compare_windows_models import REPO, confirmed, tree_hashes, windows, zero_masks   # noqa: F401
from test_confirm_pairs_models import demo_dir   # noqa: F401
from test_local_delta import make_case2d, pair, rect

INSIDE = rect(range(1, 3), range(1, 3))      # the cells of the window [0.5, 1.5]^2
OUTSIDE = rect(range(0, 1), range(0, 1))     # x, y in [0, 0.5): outside the window
STRADDLE = rect(range(0, 2), range(0, 2))    # x, y in [0, 1): a quarter of the window
T2_CANDIDATE = '157024c'


def build(a_cases, b_cases, *, a_kwargs=None, b_kwargs=None, size=1.0, diameter=5):
    ready = pair('p', a_cases, b_cases, (1.0, 1.0), (1.0, 1.0), **(a_kwargs or {}))
    return window_cases.build_window_cases([ready], _as_dataset(a_cases), _as_dataset(b_cases), window_um=size,
                                           diameter_um=diameter, correspondence_sha256='x'*64)


def listed(payload):
    assert len(payload['pairs']) == 1
    return {case['key']: case for case in payload['pairs'][0]['cases']}


def test_both_target_only_and_model_only_with_hand_counts():
    a = [make_case2d('x', 'E1', 'E2', INSIDE), make_case2d('y', 'E3', 'E4', INSIDE), make_case2d('z', 'E5', 'E6', OUTSIDE)]
    b = [make_case2d('x', 'E1', 'E2', INSIDE, product='B'), make_case2d('w', 'E7', 'E8', INSIDE, product='B'),
         make_case2d('z', 'E5', 'E6', INSIDE, product='B')]
    payload = build(a, b)
    entry = payload['pairs'][0]
    cases = listed(payload)
    assert entry['counts'] == {'both': 1, 'target_only': 1, 'model_only': 2, 'unknown': 0}
    assert cases['lateral_short|E1|E2']['class'] == 'both'
    assert cases['lateral_short|E3|E4']['class'] == 'target_only'      # no such case in the model dataset: absent there
    assert cases['lateral_short|E3|E4']['b']['reasons'] == ['no_case_in_dataset']
    assert cases['lateral_short|E7|E8']['class'] == 'model_only'
    # a case outside the window (all window cells 0) is absent, and its key is model_only because b has it
    assert cases['lateral_short|E5|E6']['a'] == {'status': 'absent', 'area_um2': 0.0, 'reasons': []}
    assert cases['lateral_short|E5|E6']['class'] == 'model_only'
    assert cases['lateral_short|E1|E2']['a']['area_um2'] == pytest.approx(1.0) and entry['truncated'] == 0
    assert payload['schema_version'] == 'window_cases_v1' and 'not a causal attribution' in payload['semantics']
    assert [c['class'] for c in entry['cases']] == ['target_only', 'model_only', 'model_only', 'both']   # listing order


def test_absent_on_both_sides_is_not_listed():
    a = [make_case2d('z', 'E5', 'E6', OUTSIDE)]
    b = [make_case2d('z', 'E5', 'E6', set(), product='B')]
    payload = build(a, b)
    assert payload['pairs'][0]['cases'] == [] and sum(payload['pairs'][0]['counts'].values()) == 0


def test_a_case_touching_the_window_edge_is_present():
    payload = build([make_case2d('s', 'E1', 'E2', STRADDLE)], [make_case2d('s', 'E1', 'E2', STRADDLE, product='B')])
    case = listed(payload)['lateral_short|E1|E2']
    assert case['class'] == 'both' and case['a']['status'] == 'present' and case['a']['area_um2'] == pytest.approx(0.25)


def test_valid_hole_and_clipped_window_are_unknown_not_absent():
    valid = np.ones((4, 4), bool)
    valid[1, 1] = False                                  # a hole inside the window
    a = [make_case2d('h', 'E1', 'E2', set(), valid=valid)]   # no hotspot at all, but the window is not fully observed
    b = [make_case2d('h', 'E1', 'E2', INSIDE, product='B')]
    case = listed(build(a, b))['lateral_short|E1|E2']
    assert case['a']['status'] == 'unknown' and 'valid_incomplete' in case['a']['reasons'] and case['a']['area_um2'] is None
    assert case['class'] == 'unknown'
    # the window leaves the frame: partial -> unknown
    ready = pair('edge', a, b, (0.2, 1.0), (0.2, 1.0))
    payload = window_cases.build_window_cases([ready], _as_dataset(a), _as_dataset(b), window_um=1.0, diameter_um=5,
                                              correspondence_sha256='x')
    assert payload['pairs'][0]['counts']['unknown'] == 1 and 'window_clipped' in payload['pairs'][0]['cases'][0]['a']['reasons']


def test_a_record_that_is_not_observed_is_unknown():
    case = make_case2d('v', 'E1', 'E2', INSIDE, failure_type='vertical_short')   # d < height 3 is not_applicable
    b = [make_case2d('v', 'E1', 'E2', INSIDE, product='B', failure_type='vertical_short')]
    payload = build([case], b, diameter=2)
    entry = listed(payload)['vertical_short|E1|E2']
    assert entry['a']['status'] == 'unknown' and entry['class'] == 'unknown'


def test_other_layer_is_not_a_candidate():
    a = [make_case2d('x', 'E1', 'E2', INSIDE, layer='M3'), make_case2d('q', 'E9', 'E8', INSIDE, layer='M3')]
    b = [make_case2d('x', 'E1', 'E2', INSIDE, product='B')]
    payload = build(a, b)
    cases = listed(payload)
    assert set(cases) == {'lateral_short|E1|E2'}                    # E9|E8 exists only on the other layer of a: on neither side
    assert cases['lateral_short|E1|E2']['class'] == 'model_only'    # a's M3 case is not a candidate of an M2 pair
    assert payload['pairs'][0]['n_candidates'] == {'a': 0, 'b': 1}


def test_endpoint_order_does_not_change_the_key_and_failure_type_does():
    a = [make_case2d('x', 'E2', 'E1', INSIDE), make_case2d('x2', 'E1', 'E2', INSIDE, failure_type='other_short')]
    b = [make_case2d('x', 'E1', 'E2', INSIDE, product='B')]
    payload = build(a, b)
    cases = listed(payload)
    assert cases['lateral_short|E1|E2']['class'] == 'both' and cases['lateral_short|E1|E2']['endpoints'] == ['E1', 'E2']
    assert cases['other_short|E1|E2']['class'] == 'target_only'      # same electrodes, other failure type: a different key
    assert payload['pairs'][0]['counts']['unknown'] == 0


def test_the_same_key_twice_on_one_side_is_ambiguous_and_unknown():
    a = [make_case2d('x', 'E1', 'E2', INSIDE), make_case2d('x_again', 'E2', 'E1', INSIDE)]
    b = [make_case2d('x', 'E1', 'E2', INSIDE, product='B')]
    payload = build(a, b)
    case = listed(payload)['lateral_short|E1|E2']
    assert case['class'] == 'unknown' and case['a'] == {'status': 'unknown', 'area_um2': None, 'reasons': ['ambiguous_case_key']}
    assert payload['pairs'][0]['counts'] == {'both': 0, 'target_only': 0, 'model_only': 0, 'unknown': 1}


def test_side_filters_apply_but_failure_type_and_endpoints_do_not():
    a = [make_case2d('x', 'E1', 'E2', INSIDE), make_case2d('y', 'E3', 'E4', INSIDE, step='other_step')]
    b = [make_case2d('x', 'E1', 'E2', INSIDE, product='B')]
    payload = build(a, b, a_kwargs={'filters': {'process_step_id': 'step'}, 'failure_type': 'vertical_short', 'eps': None})
    assert set(listed(payload)) == {'lateral_short|E1|E2'}     # E3|E4 is filtered out by the step; the failure_type does not filter


def test_candidate_limit_exceeded_lists_nothing(monkeypatch):
    monkeypatch.setattr(window_cases, 'MAX_CANDIDATES_PER_SIDE', 2)
    a = [make_case2d(f'c{i}', f'E{i}', f'F{i}', INSIDE) for i in range(3)]
    entry = build(a, [make_case2d('c0', 'E0', 'F0', INSIDE, product='B')])['pairs'][0]
    assert entry['status'] == 'case_limit_exceeded' and entry['cases'] == [] and entry['n_candidates'] == {'a': 3, 'b': 1}


def test_listed_rows_are_capped_and_counts_keep_everything(monkeypatch):
    monkeypatch.setattr(window_cases, 'MAX_LISTED_CASES', 2)
    a = [make_case2d(f'c{i}', f'E{i}', f'F{i}', INSIDE) for i in range(3)]
    b = [make_case2d('m', 'M1', 'M2', INSIDE, product='B')]
    entry = build(a, b)['pairs'][0]
    assert entry['counts'] == {'both': 0, 'target_only': 3, 'model_only': 1, 'unknown': 0}
    assert len(entry['cases']) == 2 and entry['truncated'] == 2
    assert [c['class'] for c in entry['cases']] == ['target_only', 'target_only']      # differences are listed first


def test_cases_are_measured_one_at_a_time_and_only_small_entries_are_kept(monkeypatch):
    seen = []
    original = window_cases._measure_case
    monkeypatch.setattr(window_cases, '_measure_case', lambda case, diameter, window: (seen.append(case.identity.case_id), original(case, diameter, window))[1])
    a = [make_case2d(f'c{i}', f'E{i}', f'F{i}', INSIDE) for i in range(3)]
    b = [make_case2d(f'c{i}', f'E{i}', f'F{i}', INSIDE, product='B') for i in range(3)]
    payload = build(a, b)
    assert sorted(seen) == sorted(['c0', 'c1', 'c2'] * 2)           # each case of each side exactly once
    assert all(set(case) == {'key', 'failure_type', 'endpoints', 'a', 'b', 'class'} for case in payload['pairs'][0]['cases'])
    assert all(set(case['a']) == {'status', 'area_um2', 'reasons'} for case in payload['pairs'][0]['cases'])


def test_blocked_pairs_are_skipped():
    a = [make_case2d('x', 'E1', 'E2', INSIDE)]
    ready = pair('p', a, a, (1.0, 1.0), (1.0, 1.0))
    from dataclasses import replace
    blocked = replace(ready, pair_id='q', status='blocked', reasons=('x',))
    payload = window_cases.build_window_cases([ready, blocked], _as_dataset(a), _as_dataset(a), window_um=1.0, diameter_um=5,
                                              correspondence_sha256='x')
    assert [p['pair_id'] for p in payload['pairs']] == ['p'] and (payload['n_pairs'], payload['n_ready'], payload['n_blocked']) == (2, 1, 1)


def test_diameter_and_window_checks():
    a = [make_case2d('x', 'E1', 'E2', INSIDE)]
    with pytest.raises(NumericContractError) as caught:
        window_cases.resolve_diameter(_as_dataset(a), _as_dataset(a), 0.5)
    assert caught.value.code == 'case_diameter_missing' and caught.value.writes_nothing
    with pytest.raises(NumericContractError) as caught:
        window_cases.check_window(3, [1.0, 2.0])
    assert caught.value.code == 'case_window_not_in_sizes'
    assert window_cases.check_window(2, [1.0, 2.0]) == 2.0


# ---- CLI ------------------------------------------------------------------------------------------------------------

CASE_ARGS = ['--case-window-um', '2', '--case-diameter-um', '5']


def test_cli_writes_json_csv_and_provenance_in_the_same_generation(env):
    models, conf = confirmed(env)
    assert windows(env, models, conf, extra=CASE_ARGS) in (0, 3)
    out = env['tmp']/'win_C'
    payload = load(out/'window_cases.json')
    delta = load(out/'local_delta.json')
    assert payload['schema_version'] == 'window_cases_v1' and payload['window_um'] == 2.0 and payload['diameter_um'] == 5.0
    assert payload['products'] == {'a': {'product_id': 'target'}, 'b': {'product_id': 'C'}}
    assert payload['inputs']['correspondence_sha256'] == delta['provenance']['correspondence_sha256'] == sha(conf/'models'/'C'/'roi_correspondence.json')
    assert delta['provenance']['window_cases'] == {'window_um': 2.0, 'diameter_um': 5.0, 'file': 'window_cases.json'}
    entry = payload['pairs'][0]
    assert entry['pair_id'] == delta['pairs'][0]['pair_id'] and entry['status'] == 'available'
    # C is a copy of the target: nothing differs
    assert entry['counts']['target_only'] == entry['counts']['model_only'] == entry['counts']['unknown'] == 0
    assert entry['counts']['both'] == len(entry['cases']) > 0
    rows = (out/'window_cases.csv').read_text(encoding='utf-8').splitlines()
    assert rows[0] == ','.join(window_cases.CSV_COLUMNS) and len(rows) == 1 + len(entry['cases'])
    assert load(out/'execution.json')['outputs'] and any(p.endswith('window_cases.json') for p in load(out/'execution.json')['outputs'])


def test_cli_model_without_hotspots_gives_target_only(env):
    models, conf = confirmed(env, mutate=zero_masks)
    assert windows(env, models, conf, extra=CASE_ARGS) in (0, 3)
    entry = load(env['tmp']/'win_C'/'window_cases.json')['pairs'][0]
    assert entry['counts']['both'] == entry['counts']['model_only'] == 0 and entry['counts']['target_only'] > 0
    assert all(case['b']['status'] == 'absent' and case['a']['status'] == 'present' for case in entry['cases'])


def test_cli_regeneration_without_case_args_removes_the_stale_file(env):
    models, conf = confirmed(env)
    assert windows(env, models, conf, extra=CASE_ARGS) in (0, 3)
    assert (env['tmp']/'win_C'/'window_cases.json').exists()
    assert windows(env, models, conf) in (0, 3)
    out = env['tmp']/'win_C'
    assert not (out/'window_cases.json').exists() and not (out/'window_cases.csv').exists()
    assert 'window_cases' not in load(out/'local_delta.json')['provenance']


ARGUMENT_ERRORS = [
    ('without_model', lambda models: [*CASE_ARGS], 'case_args_require_model'),
    ('window_only', lambda models: ['--models', str(models), '--model', 'C', '--case-window-um', '2'], 'case_args_incomplete'),
    ('diameter_only', lambda models: ['--models', str(models), '--model', 'C', '--case-diameter-um', '5'], 'case_args_incomplete'),
    ('window_not_in_sizes', lambda models: ['--models', str(models), '--model', 'C', '--case-window-um', '3', '--case-diameter-um', '5'],
     'case_window_not_in_sizes'),
    ('diameter_not_in_data', lambda models: ['--models', str(models), '--model', 'C', '--case-window-um', '2', '--case-diameter-um', '0.5'],
     'case_diameter_missing'),
]


@pytest.mark.parametrize('name,extra,code', ARGUMENT_ERRORS, ids=[case[0] for case in ARGUMENT_ERRORS])
def test_cli_case_argument_errors_exit_2_and_create_no_folder(env, capsys, name, extra, code):
    models, conf = confirmed(env)
    demo = demo_dir(env)
    before = tree_hashes(demo)
    args = ['compare-windows', '--config', str(env['config']), '--correspondence', str(conf/'models'/'C'/'roi_correspondence.json'),
            '--window-sizes', '2', *extra(models), '--out', str(env['tmp']/'win_x')]
    capsys.readouterr()
    assert main(args) == 2
    assert capsys.readouterr().err.startswith('오류:')
    assert not (env['tmp']/'win_x').exists()
    assert tree_hashes(demo) == before


def test_cli_diameter_refusal_keeps_an_existing_generation(env):
    models, conf = confirmed(env)
    assert windows(env, models, conf) in (0, 3)
    before = tree_hashes(env['tmp']/'win_C')
    assert windows(env, models, conf, extra=['--case-window-um', '2', '--case-diameter-um', '0.5']) == 2
    assert tree_hashes(env['tmp']/'win_C') == before


def extract_t2_candidate(tmp_path):
    src = tmp_path/'t2'
    src.mkdir()
    archive = subprocess.run(['git', 'archive', T2_CANDIDATE, 'src'], cwd=str(REPO), stdout=subprocess.PIPE, check=True).stdout
    (tmp_path/'t2.tar').write_bytes(archive)
    with tarfile.open(str(tmp_path/'t2.tar')) as handle:
        handle.extractall(str(src))
    return src/'src'


def run_old(code_dir, args):
    code = 'import sys; sys.path.insert(0, %r); from simcompare.cli import main; raise SystemExit(main(sys.argv[1:]))' % str(code_dir)
    return subprocess.run([sys.executable, '-c', code, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env={**os.environ, 'PYTHONPATH': str(code_dir)})


def test_model_run_outputs_equal_the_t2_candidate_with_and_without_case_args(env, tmp_path):
    """Raw bytes (no normalisation): without case args every file equals T2's; with them only window_cases.* and the provenance key differ."""
    models, conf = confirmed(env, accept=(1, 2))
    base = ['compare-windows', '--config', str(env['config']), '--correspondence', str(conf/'models'/'C'/'roi_correspondence.json'),
            '--window-sizes', '2', '--models', str(models), '--model', 'C']
    old_code = extract_t2_candidate(tmp_path)
    done = run_old(old_code, base + ['--out', str(env['tmp']/'old')])
    assert done.returncode in (0, 3), done.stderr.decode('utf-8', 'replace')
    assert main(base + ['--out', str(env['tmp']/'plain')]) in (0, 3)
    assert main(base + CASE_ARGS + ['--out', str(env['tmp']/'cases')]) in (0, 3)
    old, plain, cases = (tree_hashes(env['tmp']/name) for name in ('old', 'plain', 'cases'))
    for tree in (old, plain, cases):
        tree.pop('execution.json')       # carries the absolute --out and the output list
    assert plain == old
    assert set(cases) - set(old) == {'window_cases.json', 'window_cases.csv'}
    changed = {name for name in old if cases[name] != old[name]}
    assert changed == {'local_delta.json'}
    with_cases, without = load(env['tmp']/'cases'/'local_delta.json'), load(env['tmp']/'old'/'local_delta.json')
    with_cases['provenance'].pop('window_cases')
    assert with_cases == without
