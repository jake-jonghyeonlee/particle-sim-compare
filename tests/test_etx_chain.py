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


def _write_dl1(dl1, side, product=None):
    dx, dy = OFFSET
    for number, (name, seed) in enumerate((('CNT1', 21), ('CNT2', 22), ('CNT3', 23)), start=1):
        world = cnt_world(seed)
        if side == 'target':
            world = shifted(world, dx, dy)
            if name == 'CNT1':
                world[500 + 305 + dy:500 + 315 + dy, 500 + 305 + dx:500 + 315 + dx] = True
        save_black(dl1 / (product or PRODUCTS[side]) / 'CNO' / f'{number}-{name}.png', world)


def _write_new_structure(dl1, side, product=None):
    """The new input structure (layer-align): ``Merged_Layer/<electrode>/`` (names only matter), ``Layer/<n>-<layer>.png`` (two layers,
    the full 2x2 unit image size, black = shape). The stage stage-3 import sees only ``CNO`` (written first), so this runs after it."""
    dx, dy = OFFSET
    model = dl1 / (product or PRODUCTS[side])
    for electrode in ROLES:
        save_black(model / 'Merged_Layer' / electrode / '1-L1.png', rect(0, 0, 4, 4))
    for number, (name, seed) in enumerate((('L1', 31), ('L2', 32)), start=1):
        world = cnt_world(seed)
        if side == 'target':
            world = shifted(world, dx, dy)
        save_black(model / 'Layer' / f'{number}-{name}.png', world)


MODEL_FOLDER = MODEL_PRODUCT = 'QA4_C00'     # the runners use the model key as folder name and as --model; models.yaml key = product id


def _write_model_tree(root):
    """The model hotspot tree: the target's, with the case-AB hotspot moved 1.5 um (so its hotspot differs from the target's)."""
    _write_hotspot_tree(root, 'target')
    mask = np.roll(hotspot_masks('target')[CASE_AB], 15, axis=1)
    folder = root / 'images' / CASE_AB
    Image.fromarray(mask.astype(np.uint8) * 255).save(folder / 'hotspot_lateral_short05.png')
    with (folder / 'result/lateral_short.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(['defect_size(um)', 'Area(um2)'])
        writer.writerow(['5', f'{int(mask.sum()) * PITCH * PITCH:.2f}'])


def _write_models_yaml(base, with_assets):
    section = {'product_id': MODEL_PRODUCT, 'data_dir': f'data/models/{MODEL_FOLDER}',
               'condition_assignment': _assignment('target', MODEL_PRODUCT)}
    if with_assets:
        data = base / 'data/models' / MODEL_FOLDER
        section['asset_manifest'] = {'path': 'review_assets.json', 'sha256': sha(data / 'review_assets.json')}
        section['electrode_import_record'] = {'path': 'electrode_asset_import_record.json',
                                              'sha256': sha(data / 'electrode_asset_import_record.json')}
    (base / 'models.yaml').write_text(yaml.safe_dump({'schema_version': 'compare_models_v1', 'target_product_id': PRODUCTS['target'],
                                                      'models': {MODEL_PRODUCT: section}}), encoding='utf-8')


def _write_search_batch(base):
    """psdl2_search_batch_v1 by hand (DL2 is not run): every target ROI gets rank 1 = the reference ROI place, rank 2 = the model."""
    cand = base / 'data/changes'
    ref_frame = json.loads((cand / 'reference_index_export/review_rois.json').read_text(encoding='utf-8'))['rois'][0]['frame_id']
    tgt_frame = json.loads((cand / 'target_index_export/review_rois.json').read_text(encoding='utf-8'))['rois'][0]['frame_id']
    export = json.loads((cand / 'target_export/review_rois.json').read_text(encoding='utf-8'))
    dx, dy = OFFSET
    rankings, candidate_ids = [], []
    for row in sorted((r for r in export['rois'] if r['status'] == 'ready'), key=lambda r: r['roi_id']):
        x, y = row['center_xy_um']
        rankings.append({'query_roi_id': row['roi_id'], 'query_product_id': PRODUCTS['target'], 'query_export_sha256': sha(cand / 'target_export/review_rois.json'),
                         'candidates': [{'rank': 1, 'roi_id': 'idx_1', 'product_id': PRODUCTS['reference'], 'frame_id': ref_frame,
                                         'center_xy_um': [x - dx * PITCH, y - dy * PITCH], 'search_scope': 'primary'},
                                        {'rank': 2, 'roi_id': 'idx_2', 'product_id': MODEL_PRODUCT, 'frame_id': tgt_frame,
                                         'center_xy_um': [x, y], 'search_scope': 'primary'}]})
        candidate_ids += [f'target_to_reference:{row["roi_id"]}:r1', f'target_to_reference:{row["roi_id"]}:r2']
    payload = {'schema_version': 'psdl2_search_batch_v1', 'checkpoint_fingerprint': 'c' * 64, 'view_fingerprint': 'd' * 64,
               'index_sources': [{'export_sha256': 'e' * 64, 'patch_set_sha256s': [],
                                  'product_ids': [PRODUCTS['reference'], PRODUCTS['target'], MODEL_PRODUCT]}],
               'inputs': {'query_export_sha256': None, 'query_dataset_sha256': None, 'index_sha256': 'f' * 64},
               'arguments': {'topk': 2}, 'results': [], 'rankings': rankings}
    (base / 'data/dl2/search_batch.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    with (base / 'data/dl2/decisions.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['candidate_id', 'decision', 'note'])
        writer.writerows([[cid, 'accept', ''] for cid in candidate_ids])


def _assignment(side, product=None):
    return {'product_id': product or PRODUCTS[side], 'design_version': 'DESIGN', 'source_id': 'SOURCE', 'process_step_id': 'STEP',
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


def _build_chain(python, tmp, with_models=False):
    started = time.time()
    base = tmp / 'work'
    logs, results = {}, {}
    for side, model in MODELS.items():
        _write_hotspot_tree(base / 'data/input/hotspot' / model, side)
        _write_dl1(base / 'data/input/dl1', side)
        (base / f'assignment_{side}.json').write_text(json.dumps(_assignment(side)), encoding='utf-8')
    if with_models:
        _write_model_tree(base / 'data/input/hotspot' / MODEL_FOLDER)
        _write_dl1(base / 'data/input/dl1', 'target', MODEL_PRODUCT)
        (base / f'assignment_{MODEL_FOLDER}.json').write_text(json.dumps(_assignment('target', MODEL_PRODUCT)), encoding='utf-8')

    def stage(name, jobs, label=None):
        logs[label or name], results[label or name] = _run_stage(python, base, tmp, name, jobs, label)

    stage('1_import_hotspots', _jobs('1_import_hotspots'))
    if with_models:
        stage('1m_import_models', _jobs('1m_import_models'))
        (base / f'data/models/{MODEL_FOLDER}/pairs.csv').write_text(
            'pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\nAB,A,B,line,pair-v1\nAC,A,C,line,pair-v1\n',
            encoding='utf-8')
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
        _write_new_structure(base / 'data/input/dl1', side)    # Merged_Layer electrode folders (names) + Layer PNGs for stage 4
    if with_models:
        stage('3m_import_model_assets', _jobs('3m_import_model_assets'))
        out, data = base / f'data/assets/models/{MODEL_FOLDER}', base / f'data/models/{MODEL_FOLDER}'
        for name in ('review_assets.json', 'electrode_asset_import_record.json'):
            shutil.copyfile(out / name, data / name)
        (data / 'assets').mkdir(exist_ok=True)
        for path in (out / 'assets').iterdir():
            shutil.copyfile(path, data / 'assets' / path.name)
        _write_new_structure(base / 'data/input/dl1', 'target', MODEL_PRODUCT)
        _write_models_yaml(base, with_assets=True)
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
    if with_models:     # the multi-product flow: 5m instead of 5, 6 again on the DL2 correspondence (reference pairs), 6m, 7
        _write_search_batch(base)
        stage('5m_confirm_pairs_models', _jobs('5m_confirm_pairs_models'))
        stage('6_compare_windows', _jobs('6_compare_windows', {'--correspondence': 'data/confirmed/roi_correspondence.json'}),
              label='6_compare_windows_dl2')
        stage('6m_compare_windows_models', _jobs('6m_compare_windows_models'))
        seven = [(name, [arg.replace('QA4_R00=', f'{PRODUCTS["reference"]}=') for arg in args]) for name, args in _jobs('7_model_report')]
        stage('7_model_report', seven)      # the synthetic world names its reference PROD_R, the runner example QA4_R00
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


@pytest.fixture(scope='module', params=_python_params())
def model_chain(request, tmp_path_factory):
    """The same world plus the multi-product stages 1m, 3m, 5m, 6 (DL2 correspondence), 6m and 7."""
    if request.param is None:
        pytest.fail('SIMCOMPARE_REQUIRE_ETX_PY=1 but SIMCOMPARE_ETX_PY is not set')
    return _build_chain(request.param, tmp_path_factory.mktemp('etx_chain_models'), with_models=True)


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
                 'reference_index_export/mask_derivation.json', 'target_index_export/mask_derivation.json',
                 'roi_local_alignment.json'):          # the stage-4 example passes --local-align-um 1
        assert (changes / name).is_file(), name
    assert json.loads((changes / 'roi_local_alignment.json').read_text(encoding='utf-8'))['search_um'] == 1.0
    payload = json.loads((changes / 'candidate_queries_v1.json').read_text(encoding='utf-8'))
    assert payload['roi_source'] == 'cnt_change' and payload['alignment']['status'] == 'aligned'
    alignment = json.loads((changes / 'alignment.json').read_text(encoding='utf-8'))
    assert alignment['status'] == 'aligned' and alignment['offset_xy_px'] == list(OFFSET)
    assert payload['change_args']['apply_align'] == 'local' and len(alignment['unit_offsets']) == 4     # the example applies the alignment
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


MODEL_COMMANDS = {
    '1m_import_models': ['import-hotspots'],
    '3m_import_model_assets': ['import-electrode-assets'],
    '5m_confirm_pairs_models': ['confirm-pairs', 'confirm-pairs'],
    '6_compare_windows_dl2': ['compare-windows'],
    '6m_compare_windows_models': ['compare-windows'],
    '7_model_report': ['model-report']}


def test_multiproduct_stages_run_with_the_runners_and_join_reference_and_model_pairs(model_chain):
    """1m, 3m, 5m, 6 (DL2 correspondence), 6m and 7 as runners: the model report puts the reference pair and the model pair of one
    target ROI in one row group, from the runner files alone."""
    chain = model_chain
    for stage, commands in MODEL_COMMANDS.items():
        text = chain.logs[stage]
        assert re.search(r'payload_sha256=[0-9a-f]{64}', text), stage
        codes = _exit_codes(text)
        assert [name for name, _ in codes] == commands, (stage, codes, text[-1500:])
        assert all(int(code) in (0, 3) for _, code in codes), (stage, codes, text[-1500:])
    base = chain.base
    assert (base / f'data/models/{MODEL_FOLDER}/manifest.json').is_file() and (base / f'data/assets/models/{MODEL_FOLDER}/review_assets.json').is_file()
    assert (base / f'data/confirmed/models/{MODEL_PRODUCT}/roi_correspondence.json').is_file()
    model_local = base / f'data/local/models/{MODEL_FOLDER}'
    assert (model_local / 'window_cases.json').is_file() and (model_local / 'local_delta.json').is_file()
    assert (base / 'data/local/local_delta.json').is_file()       # the stage-6 generation did not remove the model folder
    report = base / 'data/model_report'
    assert sorted(p.name for p in report.iterdir()) == ['execution.json', 'model_report.csv', 'model_report.html', 'model_report.json']
    with (report / 'model_report.csv').open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    by_roi = {}
    for row in rows:
        if row['pair_id'] and row['roi_linked'] == 'True':
            by_roi.setdefault(row['roi_id'], set()).add(row['model'])
    assert any({PRODUCTS['reference'], MODEL_PRODUCT} <= models for models in by_roi.values()), by_roi
    print(f'\netx-chain-models[{Path(chain.python).name}] elapsed {chain.seconds:.1f}s')
    for stage in MODEL_COMMANDS:
        print('  ', stage, [(n, c, s) for n, c, s in re.findall(r'^\[simcompare_job\] \S+ \S+ (\S+) exit (\d+) in (\d+)s$', chain.logs[stage], re.M)])


def test_chain_elapsed_time_is_reported(chain):
    """Timing evidence for the final report (-s shows it)."""
    print(f'\netx-chain[{Path(chain.python).name}] elapsed {chain.seconds:.1f}s')
    assert chain.seconds > 0
