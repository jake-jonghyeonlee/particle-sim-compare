"""compare-multiproduct/T2: compare-windows --models --model.

Model datasets are copies of the synthetic demo target dataset under another product_id (T1 fixtures). DL2 is never run.
"""
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from simcompare.cli import main

from test_confirm_pairs import load, query_rows, sha   # noqa: F401
from test_confirm_pairs_models import demo_dir, run_models, scene, tid   # noqa: F401  (env fixture below via import)
from test_confirm_pairs import env   # noqa: F401

BASELINE = '1f48e0d'
REPO = Path(__file__).resolve().parents[1]
SIZES = ['2']


def tree_hashes(root):
    root = Path(root)
    if not root.exists():
        return None
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file()}


def zero_masks(root):
    """Remove every hotspot of the model dataset (manifest masks all 0) and re-hash the manifest."""
    from simcompare.adapters.numeric import source_payload_hash
    path = Path(root)/'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    for case in manifest['cases']:
        for record in case['records']:
            if 'mask' in record:
                record['mask'] = [[0]*len(row) for row in record['mask']]
    manifest['input_hash'] = manifest['source_manifest_hash'] = source_payload_hash(manifest)
    path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')


def rebind_assets(root):
    """The copied review_assets.json still names product 'target' and the target's manifest hash: bind it to the model."""
    manifest = json.loads((Path(root)/'manifest.json').read_text(encoding='utf-8'))
    path = Path(root)/'review_assets.json'
    assets = json.loads(path.read_text(encoding='utf-8'))
    assets['source_manifest_hash'] = manifest['input_hash']
    for case in assets['cases']:
        case['identity']['product_id'] = manifest['cases'][0]['product_id']
    path.write_text(json.dumps(assets, indent=2), encoding='utf-8')


def confirmed(env, accept=(2,), out='conf', *, mutate=None):
    """confirm-pairs --models with C configured (valid review assets) and D pending; returns (models.yaml, confirm out folder)."""
    from test_models_config import model_section, write_models
    models, matches = scene(env)
    demo = demo_dir(env)
    if mutate is not None:
        mutate(demo/'models'/'C')
    rebind_assets(demo/'models'/'C')
    models = write_models(demo, {'C': model_section(demo, 'C'), 'D': model_section(demo, 'D')})
    rows = [[tid(env, rank), 'accept', ''] for rank in accept]
    assert run_models(env, models, matches, rows, out=out) in (0, 3)
    return models, env['tmp']/out


def reference_run(env, conf):
    """The ordinary reference -> target run into the config output_dir (what data/local is in production)."""
    assert main(['compare-windows', '--config', str(env['config']), '--correspondence', str(conf/'roi_correspondence.json'),
                 '--window-sizes', *SIZES]) in (0, 3)


def windows(env, models, conf, key='C', out='win_C', extra=()):
    args = ['compare-windows', '--config', str(env['config']), '--correspondence', str(conf/'models'/key/'roi_correspondence.json'),
            '--window-sizes', *SIZES, '--models', str(models), '--model', key, *extra]
    if out is not None:
        args += ['--out', str(env['tmp']/out)]
    return main(args)


def test_model_run_binds_target_as_a_and_the_model_as_b(env):
    models, conf = confirmed(env)
    assert windows(env, models, conf) in (0, 3)
    out = env['tmp']/'win_C'
    result = load(out/'local_delta.json')
    corr = load(conf/'models'/'C'/'roi_correspondence.json')
    assert result['products']['a']['product_id'] == 'target' and result['products']['b']['product_id'] == 'C'
    assert result['provenance']['n_pairs'] == len(corr['pairs']) == 1
    assert result['provenance']['correspondence_sha256'] == sha(conf/'models'/'C'/'roi_correspondence.json')
    demo = demo_dir(env)
    assert result['provenance']['model'] == {
        'product_id': 'C', 'models_sha256': sha(models), 'manifest_sha256': sha(demo/'models'/'C'/'manifest.json'),
        'asset_manifest_sha256': sha(demo/'models'/'C'/'review_assets.json'), 'electrode_import_record_sha256': None}
    # C is a copy of target: the window areas are equal, so every delta b - a is exactly 0 (sign convention b - a is tested below)
    ready = [row for row in result['rows'] if row.get('status') == 'ready'] if 'rows' in result else []
    for row in ready:
        for value in row.get('delta_um2', {}).values() if isinstance(row.get('delta_um2'), dict) else []:
            assert value in (0, 0.0, None)


def test_delta_sign_is_model_minus_target(env, monkeypatch):
    """The model has no hotspot at all: b = 0, a = 4.0 (the four target masks cover the whole 2x2 um window) -> delta = -4.0."""
    import simcompare.linepixel as linepixel
    models, conf = confirmed(env, mutate=zero_masks)
    calls = []
    original = linepixel.compute_areas
    monkeypatch.setattr(linepixel, 'compute_areas', lambda dataset, *args, **kwargs: (calls.append(1), original(dataset, *args, **kwargs))[1])
    assert windows(env, models, conf) in (0, 3)
    assert len(calls) == 2   # recomputed for both sides
    rows = load(env['tmp']/'win_C'/'local_delta.json')['rows']
    row = [r for r in rows if r['diameter_um'] == 5.0][0]['window']
    value = {side: {metric: row[side][metric]['value'] for metric in ('union', 'lateral_only')} for side in ('a', 'b', 'delta')}
    assert value['a']['union'] == 4.0 and value['b']['union'] == 0.0 and value['delta']['union'] == -4.0
    assert value['delta']['lateral_only'] == value['b']['lateral_only'] - value['a']['lateral_only'] == -3.5


def test_model_run_does_not_use_a_stage2_artefact(env, monkeypatch):
    """A comparison_masks.zip next to the config output is the reference's: the model run neither stats nor loads it."""
    import simcompare.pipeline as pipeline
    models, conf = confirmed(env)
    loads = []
    monkeypatch.setattr(pipeline, '_load_stage6_artefact', lambda *args, **kwargs: loads.append(1) or (None, 'x'))
    monkeypatch.setattr(pipeline, '_stage6_artefact_candidate', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('stat')))
    assert windows(env, models, conf) in (0, 3)
    assert loads == []


def test_without_model_the_output_bytes_equal_the_baseline(env, tmp_path):
    """compare-windows without --model: every output file equals the 1f48e0d code's raw bytes (no normalisation)."""
    models, conf = confirmed(env, accept=(1, 2))
    corr = conf/'roi_correspondence.json'
    base_src = tmp_path/'base'
    base_src.mkdir()
    archive = subprocess.run(['git', 'archive', BASELINE, 'src'], cwd=str(REPO), stdout=subprocess.PIPE, check=True).stdout
    tar_path = tmp_path/'base.tar'
    tar_path.write_bytes(archive)
    with tarfile.open(str(tar_path)) as handle:
        handle.extractall(str(base_src))
    command = ['compare-windows', '--config', str(env['config']), '--correspondence', str(corr), '--window-sizes', *SIZES]
    assert main(command + ['--out', str(env['tmp']/'now')]) in (0, 3)
    code = ('import sys; sys.path.insert(0, %r); from simcompare.cli import main; raise SystemExit(main(sys.argv[1:]))'
            % str(base_src/'src'))
    done = subprocess.run([sys.executable, '-c', code] + command + ['--out', str(env['tmp']/'old')],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, 'PYTHONPATH': str(base_src/'src')})
    assert done.returncode in (0, 3), done.stderr.decode('utf-8', 'replace')
    now, old = tree_hashes(env['tmp']/'now'), tree_hashes(env['tmp']/'old')
    assert {'local_delta.json', 'local_delta_rows.csv', 'local_delta.html'} <= set(now)
    # execution.json carries the absolute --out path, which differs by construction; every other file is compared raw
    now.pop('execution.json'), old.pop('execution.json')
    assert now == old
    assert 'model' not in load(env['tmp']/'now'/'local_delta.json')['provenance']


ERROR_CASES = [
    ('only_model', lambda env, models, conf: ['--model', 'C'], 'win_x'),
    ('only_models', lambda env, models, conf: ['--models', str(models)], 'win_x'),
    ('no_out', lambda env, models, conf: ['--models', str(models), '--model', 'C'], None),
    ('unknown_key', lambda env, models, conf: ['--models', str(models), '--model', 'Z'], 'win_x'),
    ('pending_model', lambda env, models, conf: ['--models', str(models), '--model', 'D'], 'win_x'),
]


@pytest.mark.parametrize('name,extra,out', ERROR_CASES, ids=[case[0] for case in ERROR_CASES])
def test_argument_errors_exit_2_and_write_nothing(env, capsys, name, extra, out):
    models, conf = confirmed(env, accept=(1, 2))
    reference_run(env, conf)
    demo = demo_dir(env)
    before = tree_hashes(demo)
    other = tree_hashes(env['tmp']/'win_other') if (env['tmp']/'win_other').exists() else None
    args = ['compare-windows', '--config', str(env['config']), '--correspondence', str(conf/'models'/'C'/'roi_correspondence.json'),
            '--window-sizes', *SIZES, *extra(env, models, conf)]
    if out:
        args += ['--out', str(env['tmp']/out)]
    capsys.readouterr()
    assert main(args) == 2
    assert capsys.readouterr().err.startswith('오류:')
    assert not (env['tmp']/'win_x').exists()
    assert tree_hashes(demo) == before
    assert (tree_hashes(env['tmp']/'win_other') if (env['tmp']/'win_other').exists() else None) == other


def test_out_equal_to_the_default_output_folder_is_refused(env, capsys):
    models, conf = confirmed(env, accept=(1, 2))
    corr = str(conf/'models'/'C'/'roi_correspondence.json')
    reference_run(env, conf)
    demo = demo_dir(env)
    default_out = [p for p in demo.iterdir() if (p/'local_delta.json').exists()]
    assert default_out, 'the reference-style run writes local_delta.json into the config output_dir'
    before = tree_hashes(demo)
    assert main(['compare-windows', '--config', str(env['config']), '--correspondence', corr, '--window-sizes', *SIZES,
                 '--models', str(models), '--model', 'C', '--out', str(default_out[0])]) == 2
    assert 'model_out_conflict' in capsys.readouterr().err
    assert tree_hashes(demo) == before


def test_out_with_another_products_local_delta_is_refused(env, capsys):
    models, conf = confirmed(env)
    assert windows(env, models, conf, out='win_C') in (0, 3)
    # a second model D becomes configured with a different product; writing it into the folder of C must be refused
    from test_models_config import make_model, model_section, write_models
    demo = demo_dir(env)
    make_model(demo, 'D')
    models2 = write_models(demo, {'C': model_section(demo, 'C'), 'D': model_section(demo, 'D')})
    before = tree_hashes(env['tmp']/'win_C')
    capsys.readouterr()
    assert windows(env, models2, conf, key='D', out='win_C') == 2
    assert 'model_out_conflict' in capsys.readouterr().err
    assert tree_hashes(env['tmp']/'win_C') == before
    # the same product pair into the same folder is an ordinary regeneration
    assert windows(env, models2, conf, key='C', out='win_C') in (0, 3)


def test_unreadable_existing_local_delta_is_a_conflict(env, capsys):
    models, conf = confirmed(env)
    folder = env['tmp']/'win_C'
    folder.mkdir()
    (folder/'local_delta.json').write_text('not json', encoding='utf-8')
    assert windows(env, models, conf) == 2
    assert 'model_out_conflict' in capsys.readouterr().err
    assert (folder/'local_delta.json').read_text(encoding='utf-8') == 'not json'


def test_calculation_error_after_the_checks_follows_the_existing_rule(env):
    models, conf = confirmed(env)
    assert windows(env, models, conf) in (0, 3)
    out = env['tmp']/'win_C'
    before = (out/'local_delta.json').read_bytes()
    assert windows(env, models, conf, extra=['--jitter-um', '-1']) == 2   # invalid argument found while computing
    assert (out/'local_delta.json').read_bytes() == before                 # the previous generation is kept
    assert load(out/'error.json')['exit_code'] == 2


def test_model_assets_load_when_cwd_is_not_the_config_folder(env, tmp_path, monkeypatch):
    models, conf = confirmed(env)
    elsewhere = tmp_path/'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert windows(env, models, conf) in (0, 3)
    thumbs = load(env['tmp']/'win_C'/'local_delta_thumbs.json')
    side_b = [side for side in thumbs['sides'] if side['side'] == 'b'][0]
    # the model's review_assets.json was found and accepted relative to the config folder, not the cwd
    assert side_b['product_id'] == 'C' and 'source_asset_manifest' not in (side_b['note'] or '')
    assert not any('source_asset_manifest' in (crop['note'] or '') for crop in thumbs['crops'])
    assert list(elsewhere.iterdir()) == []


def test_guard_receives_the_swapped_datasets(env, monkeypatch):
    from simcompare.adapters import real_hotspot
    models, conf = confirmed(env)
    calls = []
    original = real_hotspot.guard_stage_compare_windows

    def spy(name, reference, target, **kwargs):
        calls.append((name, frozenset(c.identity.product_id for c in reference.cases),
                      frozenset(c.identity.product_id for c in target.cases), kwargs.get('reuse', False)))
        return original(name, reference, target, **kwargs)
    monkeypatch.setattr(real_hotspot, 'guard_stage_compare_windows', spy)
    assert windows(env, models, conf) in (0, 3)
    assert calls and {call[1:] for call in calls} == {(frozenset({'target'}), frozenset({'C'}), False)}   # every stage (windows, report)
