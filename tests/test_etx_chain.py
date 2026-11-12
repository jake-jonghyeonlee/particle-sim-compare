"""change-roi/T9, vdist-filter/T4: the whole ETX flow with the generated integrated runner (bundles/etx/simcompare_job_etx.py).

A synthetic two-model world (the one of test_change_candidates: known layout offset, one planted hotspot change) is laid out
the way the ETX server has it (``data/input/QA4_R00|QA4_T00``, ``data/input/dl1``, ``data/input/pairs.csv``,
``data/input/thk_info-<model>.txt``, ``data/input/density``, ``real.yaml``, ``compare2.yaml``) and the integrated runner runs
01, 03, 02, diag, 04, 06 and 07 in one go, as a user would: BASE_DIR set, relative paths, no file copied between the steps.
Step 05 (the DL2 path) runs ``confirm-pairs --list`` on a synthetic NCC JSON. The multi-product stages (models_01..05) keep
their own runners.

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
from test_compare2 import RES, write_density
from test_etx_flow import edit_settings, flow_runner
from test_etx_stage1_expected_cases import _make_runner

ROOT = Path(__file__).resolve().parents[1]
MODELS = {'reference': 'QA4_R00', 'target': 'QA4_T00'}
STAGE_ARGS = {'--align-search-um': '3', '--align-min-dice': '0.3', '--align-layer-tolerance-um': '0.5',
              '--change-tolerance-um': '0.2', '--top-k': '3'}   # small synthetic world (40 x 42 um); --window-um comes from compare2.yaml
FLOW_COMMANDS = ['import-hotspots', 'import-hotspots', 'import-electrode-assets', 'import-electrode-assets', 'compare2',
                 'validate-data', 'compare', 'candidates-change', 'compare-windows', 'compare2-report']


def _stages():
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        import build_etx_stage_runners as stages
    finally:
        sys.path.pop(0)
    return stages.STAGES


def _jobs(stage, overrides=None, only=None):
    """The (command, args) list of a model stage exactly as the runner generator defines it; `overrides` replaces the value of a flag."""
    jobs = [(name, list(args)) for name, args, *_ in _stages()[stage][1]]
    for _, args in jobs:
        for flag, value in (overrides or {}).items():
            if flag in args:
                args[args.index(flag) + 1] = value
    return jobs if only is None else jobs[:only]


def _align_args():
    """The runner's ALIGN_ARGS with the values the small synthetic world needs."""
    args = list(edit_settings()['align_args'])
    for flag, value in STAGE_ARGS.items():
        args[args.index(flag) + 1] = value
    return args


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
    the full 2x2 unit image size, black = shape). Written before stage 1, next to ``CNO``; no electrode folder at the top level."""
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


def _assignment(model_folder, product):
    """The condition_assignment of a real.yaml / models.yaml section: input files under data/input, paths relative to the config folder."""
    return {'product_id': product, 'design_version': 'DESIGN', 'source_id': 'SOURCE', 'process_step_id': 'STEP',
            'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT', 'pair_table_path': 'data/input/pairs.csv',
            'interlayer_distance_path': f'data/input/thk_info-{model_folder}.txt', 'electrode_roles': dict(ROLES),
            'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}]}


def _write_models_yaml(base, with_assets):
    section = {'product_id': MODEL_PRODUCT, 'data_dir': f'data/models/{MODEL_FOLDER}',
               'condition_assignment': _assignment(MODEL_FOLDER, MODEL_PRODUCT)}
    if with_assets:     # the 03 output where it was written (not copied); sha256 omitted: checked through the record
        out = f'data/assets/models/{MODEL_FOLDER}'
        section['asset_manifest'] = {'path': f'{out}/review_assets.json'}
        section['electrode_import_record'] = {'path': f'{out}/electrode_asset_import_record.json'}
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


def _write_real_yaml(base):
    """real.yaml as the template has it: asset paths without sha256 (the 03 output is read where 03 wrote it), pairs and thk under data/input."""
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'outputs/numeric_real', 'map': {'diameter_um': 5}}
    for side, model in MODELS.items():
        cfg[side] = {'product_id': PRODUCTS[side], 'data_dir': f'data/imported/{side}',
                     'asset_manifest': {'path': f'data/assets/{side}/review_assets.json'},
                     'electrode_import_record': {'path': f'data/assets/{side}/electrode_asset_import_record.json'},
                     'condition_assignment': _assignment(model, PRODUCTS[side])}
    cfg['map']['input'] = cfg['reference']      # the electrode-count map reads the reference side (as configs/etx/templates/real.yaml)
    (base / 'real.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')


def _write_compare2_yaml(base, name='compare2.yaml', dl1_root='data/input/dl1'):
    cfg = {'schema_version': 'compare2_config_v1', 'numeric_config': 'real.yaml', 'output_dir': 'data/compare2',
           'density_dir': 'data/input/density',
           'model_groups': {'grp': {'glass_area': {'value': 200.0, 'unit': 'um2'}, 'weight_line': 1.0, 'weight_pixel': 1.0}},
           'products': {side: {'model_group': 'grp', 'resolution': RES, 'hotspot_root': f'data/input/{model}',
                               'dl1_model': PRODUCTS[side]} for side, model in MODELS.items()},
           'report': {'dl1_root': dl1_root, 'map_window_um': 10, 'diameter_um': 5, 'top_n': 4}}
    (base / name).write_text(yaml.safe_dump(cfg), encoding='utf-8')


def _write_inputs(base, with_models):
    """data/input/: the hotspot model folders, DL1, pairs.csv (one file for both models), thk_info-<model>.txt, density."""
    names = dict(MODELS)
    for side, model in MODELS.items():
        _write_hotspot_tree(base / 'data/input' / model, side)
        _write_dl1(base / 'data/input/dl1', side)
        _write_new_structure(base / 'data/input/dl1', side)      # the real data has the new structure from the start
    if with_models:
        _write_model_tree(base / 'data/input' / MODEL_FOLDER)
        _write_dl1(base / 'data/input/dl1', 'target', MODEL_PRODUCT)
        _write_new_structure(base / 'data/input/dl1', 'target', MODEL_PRODUCT)
        names['model'] = MODEL_FOLDER
    for model in names.values():
        (base / f'data/input/thk_info-{model}.txt').write_text('layers\tdistance\nL1L1\t0\n', encoding='utf-8')
    (base / 'data/input/pairs.csv').write_text(
        'pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\nAB,A,B,line,pair-v1\nAC,A,C,line,pair-v1\n', encoding='utf-8')
    write_density(base / 'data/input/density', 'grp', {'P1': {5: 1.0}}, {'P1': {5: 1.0}})
    _write_real_yaml(base)
    _write_compare2_yaml(base)


def _run_stage(python, base, tmp, stage, jobs, label=None):
    """One runner copy for `jobs` (a model stage), executed from a folder other than BASE_DIR (so BASE_DIR is what matters). Returns (log text, result)."""
    log_file = f'runs/simcompare_{label or stage}.log'
    script = _make_runner(tmp / 'runners' / (label or stage), jobs, log_file=log_file, base_dir=str(base))
    result = subprocess.run([python, '-I', str(script)], capture_output=True, text=True, cwd=tmp)
    return (base / log_file).read_text(encoding='utf-8'), result


def _run_flow(python, base, tmp, label, **constants):
    """The integrated runner with the given EDIT-block constants; its own log file `runs/etx_<label>.log`. Returns (log text, result)."""
    constants = {'ALIGN_ARGS': _align_args(), 'LOG_FILE': f'runs/etx_{label}.log', **constants}
    script = flow_runner(tmp / 'runners' / f'etx_{label}', base, constants)
    result = subprocess.run([python, '-I', str(script)], capture_output=True, text=True, cwd=tmp)
    return (base / constants['LOG_FILE']).read_text(encoding='utf-8'), result


def _exit_codes(text):
    return re.findall(r'^\[simcompare_job\] \S+ \S+ (\S+) exit (\d+) in \d+s$', text, re.M)


def _build_chain(python, tmp, with_models=False):
    started = time.time()
    base = tmp / 'work'
    logs, results = {}, {}
    _write_inputs(base, with_models)

    def flow(label, **constants):
        logs[label], results[label] = _run_flow(python, base, tmp, label, **constants)

    def stage(name, jobs, label=None):
        logs[label or name], results[label or name] = _run_stage(python, base, tmp, name, jobs, label)

    if not with_models:
        flow('main')            # 01, 03, 02, diag, 04, 06, 07 of one runner: no file is copied or edited between the steps
    else:
        flow('01', STEPS=['01'])
        _write_models_yaml(base, with_assets=False)
        stage('models_01_import', _jobs('models_01_import'))
        stage('models_02_import_assets', _jobs('models_02_import_assets'))
        _write_models_yaml(base, with_assets=True)      # the 03 output stays where models_02 wrote it: no copy, no sha256
        flow('rest', STEPS=['03', '02', 'diag', '04', '06', '07'])
    # the DL2 path: a synthetic NCC JSON, then confirm-pairs --list only (decisions need the human: the second job stops with exit 2)
    models = {side: SimpleNamespace(spec=SimpleNamespace(shape=SHAPE, product_id=PRODUCTS[side])) for side in MODELS}
    (base / 'data/dl2').mkdir()
    if (base / 'data/changes/candidate_queries_v1.json').is_file():
        matches = gen.fake_matches(base / 'data/changes', models, base / 'data/dl2', seed=3)
        shutil.move(str(matches['reference_to_target']), base / 'data/dl2/ncc_r2t.json')
        shutil.move(str(matches['target_to_reference']), base / 'data/dl2/ncc_t2r.json')
        flow('05', STEPS=['05'], DL2_DECISION_ARGS=['--decisions', 'data/dl2/no_decisions_yet.csv', '--labelled-by', 'TEST',
                                                    '--mutual-tolerance-um', '5'])
    if with_models:     # the multi-product flow: models_03, models_04 (reference pairs on the DL2 correspondence first), models_05
        _write_search_batch(base)
        stage('models_03_confirm_pairs', _jobs('models_03_confirm_pairs'))
        stage('models_04_compare_windows', _jobs('models_04_compare_windows'))
        seven = [(name, [arg.replace('QA4_R00=', f'{PRODUCTS["reference"]}=') for arg in args]) for name, args in _jobs('models_05_report')]
        stage('models_05_report', seven)      # the synthetic world names its reference PROD_R, the runner example QA4_R00
    else:
        # re-run only 02: the previous data/compare2 is moved to data/_runs/AGAIN/, not deleted, and a new one is written
        flow('again', STEPS=['02'], RUN_TAG='AGAIN')
    # a wrong DL1 root in compare2.yaml: the 04 log alone must say which argument and which path is wrong
    _write_compare2_yaml(base, 'compare2_typo.yaml', dl1_root='data/input/dl1_TYPO')
    # a later step (06) is chosen to show that the runner stops at exit 2 and never starts it; it writes to its own folders
    flow('wrong_root', STEPS=['04', '06'], COMPARE2_CONFIG='compare2_typo.yaml', CHANGES_DIR='data/changes_typo',
         LOCAL_DIR='data/local_typo')
    return SimpleNamespace(base=base, logs=logs, results=results, seconds=time.time() - started, python=python)


@pytest.fixture(scope='module', params=_python_params())
def chain(request, tmp_path_factory):
    if request.param is None:
        pytest.fail('SIMCOMPARE_REQUIRE_ETX_PY=1 but SIMCOMPARE_ETX_PY is not set')
    return _build_chain(request.param, tmp_path_factory.mktemp('etx_chain'))


@pytest.fixture(scope='module', params=_python_params())
def model_chain(request, tmp_path_factory):
    """The same world plus the multi-product stages models_01..05 (models_04 also runs the reference pairs on the DL2 correspondence)."""
    if request.param is None:
        pytest.fail('SIMCOMPARE_REQUIRE_ETX_PY=1 but SIMCOMPARE_ETX_PY is not set')
    return _build_chain(request.param, tmp_path_factory.mktemp('etx_chain_models'), with_models=True)


# ---- the checks -------------------------------------------------------------------------------------------------------

def test_the_integrated_log_identifies_the_run_and_every_command_exits_zero_or_three(chain):
    text = chain.logs['main']
    assert re.search(r'payload_sha256=[0-9a-f]{64}', text)
    assert re.search(r'python \d+\.\d+\.\d+ \| numpy \S+ \| Pillow \S+ \| simcompare from ', text)     # versions
    assert f'BASE_DIR requested={str(chain.base)!r} resolved={chain.base.resolve()}' in text
    codes = _exit_codes(text)
    assert [name for name, _ in codes] == FLOW_COMMANDS, (codes, text[-2500:])
    assert all(int(code) in (0, 3) for _, code in codes), (codes, text[-2500:])
    assert chain.results['main'].returncode in (0, 3), chain.results['main'].stderr[-2500:]
    assert 'flow: 10 job(s):' in text and 'flow: no previous output folder to move' in text      # a first run has nothing to move
    if chain.python != sys.executable:
        assert 'python 3.9.' in text


def test_stage_one_to_three_wrote_the_expected_folders(chain):
    base = chain.base
    for side in MODELS:
        assert (base / f'data/imported/{side}/manifest.json').is_file()
        assert (base / f'data/imported/{side}/expected_cases.txt').is_file()
        assert (base / f'data/assets/{side}/review_assets.json').is_file()
        assert not (base / f'data/imported/{side}/pairs.csv').exists()           # nothing is copied into data_dir
        assert not (base / f'data/imported/{side}/review_assets.json').exists()
    assert (base / 'outputs/numeric_real/comparison.json').is_file()


def test_stage_two_and_seven_write_the_vi_comparison_and_the_report(chain):
    base = chain.base
    assert (base / 'data/compare2/compare2.json').is_file()
    doc = json.loads((base / 'data/compare2/compare2.json').read_text(encoding='utf-8'))
    assert doc['sides']['reference'] and doc['sides']['target']
    assert (base / 'data/compare2_report/report.html').is_file() and (base / 'data/compare2_report/report.json').is_file()
    text = chain.logs['main']
    assert "--candidates 'data/changes'" in text and "--windows 'data/local'" in text      # 07 links the 04 and 06 outputs


def test_stage_four_writes_the_change_roi_outputs_and_the_direct_correspondence(chain):
    changes = chain.base / 'data/changes'
    for name in ('candidate_queries_v1.json', 'alignment.json', 'alignment.html', 'funnel.json', 'execution.json',
                 'direct/roi_correspondence.json', 'direct/direct_pairs_sidecar.json', 'reference_export/review_rois.json',
                 'target_export/review_rois.json', 'reference_index_export/review_rois.json', 'target_index_export/review_rois.json',
                 'reference_export/mask_derivation.json', 'target_export/mask_derivation.json',
                 'reference_index_export/mask_derivation.json', 'target_index_export/mask_derivation.json',
                 'roi_local_alignment.json'):          # the runner's ALIGN_ARGS pass --local-align-um 1
        assert (changes / name).is_file(), name
    assert json.loads((changes / 'roi_local_alignment.json').read_text(encoding='utf-8'))['search_um'] == 1.0
    payload = json.loads((changes / 'candidate_queries_v1.json').read_text(encoding='utf-8'))
    assert payload['roi_source'] == 'cnt_change' and payload['alignment']['status'] == 'aligned'
    alignment = json.loads((changes / 'alignment.json').read_text(encoding='utf-8'))
    assert alignment['status'] == 'aligned' and alignment['offset_xy_px'] == list(OFFSET)
    assert payload['change_args']['apply_align'] == 'local' and len(alignment['unit_offsets']) == 4     # the example applies the alignment
    corr = json.loads((changes / 'direct/roi_correspondence.json').read_text(encoding='utf-8'))
    assert corr['schema_version'] == 'roi_correspondence_v1' and corr['pairs']
    text = chain.logs['main']
    assert 'candidates-change exit 0' in text or 'candidates-change exit 3' in text
    assert ' --window-um 10 ' in text and ' --diameter-um 5 ' in text       # the window and the diameter come from compare2.yaml


def test_stage_six_reads_the_direct_file_and_writes_local_delta_rows(chain):
    local = chain.base / 'data/local'
    delta = json.loads((local / 'local_delta.json').read_text(encoding='utf-8'))
    ready = {p['pair_id'] for p in json.loads((chain.base / 'data/changes/direct/roi_correspondence.json').read_text(
        encoding='utf-8'))['pairs'] if p['status'] == 'ready'}
    assert ready and delta['rows'] and {r['pair_id'] for r in delta['rows']} == ready
    assert (local / 'local_delta.html').is_file()
    assert "--correspondence 'data/changes/direct/roi_correspondence.json'" in chain.logs['main']
    assert {row['window_size_um'] for row in delta['rows']} == {10.0}       # the one window of compare2.yaml report.map_window_um


def test_stage_five_lists_the_dl2_candidates_of_the_change_folder(chain):
    text = chain.logs['05']
    assert "--candidates 'data/changes'" in text and 'confirm-pairs exit 0' in text
    assert 'n_candidates' in chain.results['05'].stdout or 'n_candidates' in text
    assert [name for name, _ in _exit_codes(text)] == ['confirm-pairs', 'confirm-pairs']
    assert _exit_codes(text)[1][1] == '2' and chain.results['05'].returncode == 2      # no decisions.csv yet: the second job stops
    assert not (chain.base / 'data/confirmed').exists()


def test_a_second_run_of_one_step_moves_the_old_output_and_keeps_it(chain):
    text = chain.logs['again']
    base = chain.base
    assert [name for name, _ in _exit_codes(text)] == ['compare2'] and chain.results['again'].returncode in (0, 3)
    assert 'data/_runs/AGAIN' in text
    assert (base / 'data/_runs/AGAIN/data/compare2/compare2.json').is_file()           # the previous result, moved
    assert (base / 'data/compare2/compare2.json').is_file()                            # the new result
    assert (base / 'data/changes/candidate_queries_v1.json').is_file()                 # the other steps' folders stayed
    assert (base / 'data/compare2_report/report.html').is_file()


def test_wrong_dl1_root_is_visible_in_the_stage_four_log_alone(chain):
    """A DL1 root that does not exist is an input error: exit 2 before anything is written, the runner stops (the next step's
    job is never started) and no `--out` folder exists. The log alone shows the argument with its resolved path and the code."""
    text = chain.logs['wrong_root']
    resolved = chain.base.resolve() / 'data/input/dl1_TYPO'
    assert f"--cnt-from-dl1-root 'data/input/dl1_TYPO' -> {resolved}" in text      # the argument and its resolved path
    assert 'cnt_dl1_root_missing: --cnt-from-dl1-root 경로가 없습니다: data/input/dl1_TYPO' in text
    assert f'(resolved: {resolved})' in text
    assert 'candidates-change exit 2' in text and chain.results['wrong_root'].returncode == 2
    assert 'compare-windows' not in ''.join(line for line in text.splitlines() if ' exit ' in line or ' start ' in line)
    assert not (chain.base / 'data/changes_typo').exists() and not (chain.base / 'data/local_typo').exists()


MODEL_COMMANDS = {
    'models_01_import': ['import-hotspots'],
    'models_02_import_assets': ['import-electrode-assets'],
    'models_03_confirm_pairs': ['confirm-pairs', 'confirm-pairs'],
    'models_04_compare_windows': ['compare-windows', 'compare-windows'],
    'models_05_report': ['model-report']}


def test_multiproduct_stages_run_with_the_runners_and_join_reference_and_model_pairs(model_chain):
    """models_01..05 as runners: the model report puts the reference pair and the model pair of one target ROI in one row group,
    from the runner files alone (the integrated runner made the base outputs)."""
    chain = model_chain
    for stage, commands in MODEL_COMMANDS.items():
        text = chain.logs[stage]
        assert re.search(r'payload_sha256=[0-9a-f]{64}', text), stage
        codes = _exit_codes(text)
        assert [name for name, _ in codes] == commands, (stage, codes, text[-1500:])
        assert all(int(code) in (0, 3) for _, code in codes), (stage, codes, text[-1500:])
    assert [name for name, _ in _exit_codes(chain.logs['01'])] == ['import-hotspots'] * 2
    assert [name for name, _ in _exit_codes(chain.logs['rest'])] == FLOW_COMMANDS[2:]
    base = chain.base
    assert (base / f'data/models/{MODEL_FOLDER}/manifest.json').is_file() and (base / f'data/assets/models/{MODEL_FOLDER}/review_assets.json').is_file()
    assert (base / f'data/confirmed/models/{MODEL_PRODUCT}/roi_correspondence.json').is_file()
    model_local = base / f'data/local/models/{MODEL_FOLDER}'
    assert (model_local / 'window_cases.json').is_file() and (model_local / 'local_delta.json').is_file()
    assert (base / 'data/local/local_delta.json').is_file()       # the reference-pair generation did not remove the model folder
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
