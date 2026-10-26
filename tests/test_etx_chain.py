"""change-roi/T9: the whole ETX flow, stage by stage, with the generated single-file runners.

A synthetic two-model world (the one of test_change_candidates: known layout offset, one planted hotspot change) is laid out
the way the ETX server has it (``data/input/hotspot/QA4_R00|QA4_T00``, ``data/input/dl1``, ``assignment_<side>.json``,
``real.yaml``) and the runners of stages 1, 2, 3, 4 and 6 run one after the other, as a user would: BASE_DIR set, relative
paths, new ``--out`` paths. The manual steps between the stages (pairs.csv copy, stage-3 result placement, the sha256 update in
real.yaml) are done by this test. Stage 5 (the DL2 path) runs ``confirm-pairs --list`` on a synthetic NCC JSON.

Interpreters: the current one and, when ``SIMCOMPARE_ETX_PY`` is set, the ETX Python 3.9. With
``SIMCOMPARE_REQUIRE_ETX_PY=1`` a missing ``SIMCOMPARE_ETX_PY`` is a failure, not a skip (the final gate).
"""
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
import yaml

import stage46_generators as gen
from test_change_candidates import (CASE_AB, CASE_AC, OFFSET, PITCH, PRODUCTS, ROLES, SHAPE, cnt_world, hotspot_masks, pattern,
                                    rect, save_black, shifted, sha)
from test_etx_stage1_expected_cases import _make_runner

ROOT = Path(__file__).resolve().parents[1]
MODELS = {'reference': 'QA4_R00', 'target': 'QA4_T00'}
STAGE_ARGS = {'--align-search-um': '3', '--align-min-dice': '0.3', '--align-layer-tolerance-um': '0.5',
              '--change-tolerance-um': '0.2', '--window-um': '10', '--top-k': '3'}   # small synthetic world (40 x 42 um)


def _stages():
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        import build_etx_stage_runners as stages
    finally:
        sys.path.pop(0)
    return stages.STAGES


def _jobs(stage, overrides=None, only=None):
    """The (command, args) list of the stage exactly as the runner generator defines it; `overrides` replaces the value of a flag."""
    jobs = [(name, list(args)) for name, args, *_ in _stages()[stage][1]]
    for _, args in jobs:
        for flag, value in (overrides or {}).items():
            if flag in args:
                args[args.index(flag) + 1] = value
    return jobs if only is None else jobs[:only]


def _python_params():
    require = os.environ.get('SIMCOMPARE_REQUIRE_ETX_PY') == '1'
    etx = os.environ.get('SIMCOMPARE_ETX_PY')
    if not etx:
        # required: the fixture fails (an error, never a skip); otherwise skip with the reason
        etx_param = pytest.param(None, id='etx39') if require else pytest.param(None, id='etx39', marks=pytest.mark.skip(
            reason='SIMCOMPARE_ETX_PY is not set (path of the ETX Python 3.9 interpreter)'))
    else:
        etx_param = pytest.param(os.path.expanduser(etx), id='etx39')
    return [pytest.param(sys.executable, id='current'), etx_param]


def test_etx_interpreter_is_present_when_required():
    """SIMCOMPARE_REQUIRE_ETX_PY=1 turns a missing ETX interpreter into a failure instead of a silent skip."""
    if os.environ.get('SIMCOMPARE_REQUIRE_ETX_PY') == '1':
        path = os.environ.get('SIMCOMPARE_ETX_PY')
        assert path and Path(os.path.expanduser(path)).is_file(), 'SIMCOMPARE_REQUIRE_ETX_PY=1 but SIMCOMPARE_ETX_PY is not usable'


# ---- the synthetic ETX working folder --------------------------------------------------------------------------------

def _write_hotspot_tree(root, side):
    for case, mask in hotspot_masks(side).items():
        folder = root / 'images' / case
        (folder / 'result').mkdir(parents=True)
        save_black(folder / '1.png', pattern(1))
        save_black(folder / '2.png', pattern({CASE_AB: 2, CASE_AC: 3}[case]))
        Image.fromarray(mask.astype(np.uint8) * 255).save(folder / 'hotspot_lateral_short05.png')
        with (folder / 'result/lateral_short.csv').open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.writer(handle)
            writer.writerow(['defect_size(um)', 'Area(um2)'])
            writer.writerow(['5', f'{int(mask.sum()) * PITCH * PITCH:.2f}'])


def _write_dl1(dl1, side):
    dx, dy = OFFSET
    for number, (name, seed) in enumerate((('CNT1', 21), ('CNT2', 22), ('CNT3', 23)), start=1):
        world = cnt_world(seed)
        if side == 'target':
            world = shifted(world, dx, dy)
            if name == 'CNT1':
                world[500 + 305 + dy:500 + 315 + dy, 500 + 305 + dx:500 + 315 + dx] = True
        save_black(dl1 / PRODUCTS[side] / 'cno' / f'{number}_{name}.png', world)


def _assignment(side):
    return {'product_id': PRODUCTS[side], 'design_version': 'DESIGN', 'source_id': 'SOURCE', 'process_step_id': 'STEP',
            'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT', 'pair_table_path': 'pairs.csv', 'electrode_roles': dict(ROLES),
            'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}]}


def _config(base, with_assets):
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'outputs/numeric_real',
           'map': {'diameter_um': 5}}
    for side in ('reference', 'target'):
        data_dir = f'data/imported/{side}'
        section = {'product_id': PRODUCTS[side], 'data_dir': data_dir, 'condition_assignment': _assignment(side)}
        if with_assets:
            section['asset_manifest'] = {'path': 'review_assets.json', 'sha256': sha(base / data_dir / 'review_assets.json')}
            section['electrode_import_record'] = {'path': 'electrode_asset_import_record.json',
                                                  'sha256': sha(base / data_dir / 'electrode_asset_import_record.json')}
        cfg[side] = section
    cfg['map']['input'] = cfg['reference']      # the electrode-count map reads the reference side (as configs/etx/*.example.yaml)
    (base / 'real.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')


def _run_stage(python, base, tmp, stage, jobs, label=None):
    """One runner copy for `jobs`, executed from a folder other than BASE_DIR (so BASE_DIR is what matters). Returns (log text, result)."""
    log_file = f'runs/simcompare_{label or stage}.log'
    script = _make_runner(tmp / 'runners' / (label or stage), jobs, log_file=log_file, base_dir=str(base))
    result = subprocess.run([python, '-I', str(script)], capture_output=True, text=True, cwd=tmp)
    return (base / log_file).read_text(encoding='utf-8'), result


def _exit_codes(text):
    return re.findall(r'^\[simcompare_job\] \S+ \S+ (\S+) exit (\d+) in \d+s$', text, re.M)


def _build_chain(python, tmp):
    started = time.time()
    base = tmp / 'work'
    logs, results = {}, {}
    for side, model in MODELS.items():
        _write_hotspot_tree(base / 'data/input/hotspot' / model, side)
        _write_dl1(base / 'data/input/dl1', side)
        (base / f'assignment_{side}.json').write_text(json.dumps(_assignment(side)), encoding='utf-8')

    def stage(name, jobs, label=None):
        logs[label or name], results[label or name] = _run_stage(python, base, tmp, name, jobs, label)

    stage('1_import_hotspots', _jobs('1_import_hotspots'))
    for side in MODELS:                                      # manual step: pairs.csv next to each import
        (base / f'data/imported/{side}/pairs.csv').write_text(
            'pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\nAB,A,B,line,pair-v1\nAC,A,C,line,pair-v1\n',
            encoding='utf-8')
    _config(base, with_assets=False)                         # manual step: real.yaml (stage 2 form)
    stage('2_validate_compare', _jobs('2_validate_compare'))
    stage('3_import_electrode_assets', _jobs('3_import_electrode_assets'))
    for side in MODELS:                                      # manual step: place the stage-3 result, then the sha256 update
        out, data = base / f'data/assets/{side}', base / f'data/imported/{side}'
        for name in ('review_assets.json', 'electrode_asset_import_record.json'):
            shutil.copyfile(out / name, data / name)
        (data / 'assets').mkdir(exist_ok=True)
        for path in (out / 'assets').iterdir():
            shutil.copyfile(path, data / 'assets' / path.name)
        for electrode in ROLES:                              # DL1 electrode folders: only the names matter to stage 4
            save_black(base / 'data/input/dl1' / PRODUCTS[side] / electrode / '1-L1.png', rect(0, 0, 4, 4))
    _config(base, with_assets=True)
    stage('4_candidates', _jobs('4_candidates', STAGE_ARGS))
    # the DL2 path: a synthetic NCC JSON, then confirm-pairs --list only (decisions need the human)
    models = {side: SimpleNamespace(spec=SimpleNamespace(shape=SHAPE, product_id=PRODUCTS[side])) for side in MODELS}
    (base / 'data/dl2').mkdir()
    if (base / 'data/changes/candidate_queries_v1.json').is_file():
        matches = gen.fake_matches(base / 'data/changes', models, base / 'data/dl2', seed=3)
        shutil.move(str(matches['reference_to_target']), base / 'data/dl2/ncc_r2t.json')
        shutil.move(str(matches['target_to_reference']), base / 'data/dl2/ncc_t2r.json')
        stage('5_confirm_pairs', _jobs('5_confirm_pairs', only=1))
    stage('6_compare_windows', _jobs('6_compare_windows'))
    # a wrong DL1 root: the stage-4 log alone must say which argument and which path is wrong
    wrong = _jobs('4_candidates', {**STAGE_ARGS, '--cnt-from-dl1-root': 'data/input/dl1_TYPO', '--out': 'data/changes_typo'})
    # a later stage's job is appended to show that the runner stops at exit 2 and never starts it
    stage('4_candidates', wrong + _jobs('6_compare_windows'), label='4_candidates_wrong_root')
    return SimpleNamespace(base=base, logs=logs, results=results, seconds=time.time() - started, python=python)


@pytest.fixture(scope='module', params=_python_params())
def chain(request, tmp_path_factory):
    if request.param is None:
        pytest.fail('SIMCOMPARE_REQUIRE_ETX_PY=1 but SIMCOMPARE_ETX_PY is not set')
    return _build_chain(request.param, tmp_path_factory.mktemp('etx_chain'))


# ---- the checks -------------------------------------------------------------------------------------------------------

EXPECTED_COMMANDS = {
    '1_import_hotspots': ['import-hotspots', 'import-hotspots'],
    '2_validate_compare': ['validate-data', 'compare'],
    '3_import_electrode_assets': ['import-electrode-assets', 'import-electrode-assets'],
    '4_candidates': ['candidates-change'],
    '5_confirm_pairs': ['confirm-pairs'],
    '6_compare_windows': ['compare-windows']}


def test_every_stage_log_identifies_the_run_and_every_command_exits_zero_or_three(chain):
    for stage, commands in EXPECTED_COMMANDS.items():
        text = chain.logs[stage]
        assert re.search(r'payload_sha256=[0-9a-f]{64}', text), stage
        assert re.search(r'python \d+\.\d+\.\d+ \| numpy \S+ \| Pillow \S+ \| simcompare from ', text), stage     # versions
        assert f'BASE_DIR requested={str(chain.base)!r} resolved={chain.base.resolve()}' in text, stage
        codes = _exit_codes(text)
        assert [name for name, _ in codes] == commands, (stage, codes, text[-1500:])
        assert all(int(code) in (0, 3) for _, code in codes), (stage, codes, text[-1500:])
        assert chain.results[stage].returncode in (0, 3), (stage, chain.results[stage].stderr[-1500:])
    if chain.python != sys.executable:
        assert 'python 3.9.' in chain.logs['4_candidates']


def test_stage_one_to_three_wrote_the_expected_folders(chain):
    base = chain.base
    for side in MODELS:
        assert (base / f'data/imported/{side}/manifest.json').is_file()
        assert (base / f'data/imported/{side}/expected_cases.txt').is_file()
        assert (base / f'data/assets/{side}/review_assets.json').is_file()
    assert (base / 'outputs/numeric_real/comparison.json').is_file()


def test_stage_four_writes_the_change_roi_outputs_and_the_direct_correspondence(chain):
    changes = chain.base / 'data/changes'
    for name in ('candidate_queries_v1.json', 'alignment.json', 'alignment.html', 'funnel.json', 'execution.json',
                 'direct/roi_correspondence.json', 'direct/direct_pairs_sidecar.json', 'reference_export/review_rois.json',
                 'target_export/review_rois.json', 'reference_index_export/review_rois.json', 'target_index_export/review_rois.json',
                 'reference_export/mask_derivation.json', 'target_export/mask_derivation.json',
                 'reference_index_export/mask_derivation.json', 'target_index_export/mask_derivation.json'):
        assert (changes / name).is_file(), name
    payload = json.loads((changes / 'candidate_queries_v1.json').read_text(encoding='utf-8'))
    assert payload['roi_source'] == 'cnt_change' and payload['alignment']['status'] == 'aligned'
    alignment = json.loads((changes / 'alignment.json').read_text(encoding='utf-8'))
    assert alignment['status'] == 'aligned' and alignment['offset_xy_px'] == list(OFFSET)
    corr = json.loads((changes / 'direct/roi_correspondence.json').read_text(encoding='utf-8'))
    assert corr['schema_version'] == 'roi_correspondence_v1' and corr['pairs']
    assert 'candidates-change exit 0' in chain.logs['4_candidates'] or 'candidates-change exit 3' in chain.logs['4_candidates']


def test_stage_six_reads_the_direct_file_and_writes_local_delta_rows(chain):
    local = chain.base / 'data/local'
    delta = json.loads((local / 'local_delta.json').read_text(encoding='utf-8'))
    ready = {p['pair_id'] for p in json.loads((chain.base / 'data/changes/direct/roi_correspondence.json').read_text(
        encoding='utf-8'))['pairs'] if p['status'] == 'ready'}
    assert ready and delta['rows'] and {r['pair_id'] for r in delta['rows']} == ready
    assert (local / 'local_delta.html').is_file()
    assert "--correspondence 'data/changes/direct/roi_correspondence.json'" in chain.logs['6_compare_windows']


def test_stage_five_lists_the_dl2_candidates_of_the_change_folder(chain):
    text = chain.logs['5_confirm_pairs']
    assert "--candidates 'data/changes'" in text and 'confirm-pairs exit 0' in text
    assert 'n_candidates' in chain.results['5_confirm_pairs'].stdout or 'n_candidates' in text


def test_wrong_dl1_root_is_visible_in_the_stage_four_log_alone(chain):
    """A DL1 root that does not exist is an input error: exit 2 before anything is written, the runner stops (the next stage's
    job is never started) and no `--out` folder exists. The log alone shows the argument with its resolved path and the code."""
    label = '4_candidates_wrong_root'
    text = chain.logs[label]
    resolved = chain.base.resolve() / 'data/input/dl1_TYPO'
    assert f"--cnt-from-dl1-root 'data/input/dl1_TYPO' -> {resolved}" in text      # the argument and its resolved path
    assert 'cnt_dl1_root_missing: --cnt-from-dl1-root 경로가 없습니다: data/input/dl1_TYPO' in text
    assert f'(resolved: {resolved})' in text
    assert 'candidates-change exit 2' in text and chain.results[label].returncode == 2
    assert 'compare-windows' not in ''.join(line for line in text.splitlines() if ' exit ' in line or ' start ' in line)
    assert not (chain.base / 'data/changes_typo').exists()


def test_chain_elapsed_time_is_reported(chain):
    """Timing evidence for the final report (-s shows it)."""
    print(f'\netx-chain[{Path(chain.python).name}] elapsed {chain.seconds:.1f}s')
    assert chain.seconds > 0
