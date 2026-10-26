"""change-roi/T12: compare <-> the real DL2 code, from a synthetic two-model world to ``compare-windows`` rows.

Optional test: it runs only when ``SIMCOMPARE_DL2_SRC`` (the DL2 ``src`` folder) and ``SIMCOMPARE_DL2_PY`` (the DL2 interpreter) are
both set; ``SIMCOMPARE_REQUIRE_DL2=1`` turns a missing setting into a failure. DL2 is only run as ``python -m psdl2.<module>``
subprocesses with ``PYTHONPATH=<src>``; nothing is written outside pytest's tmp folders.

Flow (the numbers in the comments are the steps of ``docs/plan/change_roi/README.md``, "T12"):
 1. synthetic reference / target models (known CNT offset, planted hotspot change) + one database-only product
 2. ``export-template-rois`` per product: the DL2 database (template export + ``mask_derivation.json``)
 3. ``candidates-change``: query exports (+ ``mask_derivation.json``)
 4. DL2: ``patches`` -> ``make_split`` -> ``train`` (1 epoch, CPU) -> ``index`` -> ``search --all-ready``  (``psdl2_search_batch_v1``)
 5. ``confirm-pairs --list`` / ``confirm-pairs`` with a decisions.csv
 6. ``compare-windows --correspondence <confirmed>``;  7. ``compare-windows --correspondence direct/roi_correspondence.json``
"""
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from dl2_e2e_helpers import DB_PRODUCT, ORIGIN_UM, build_world_product, dl2_probe, dl2_run, dl2_settings, export_template, sha
from simcompare.cli import main
from test_change_candidates import cli_args, write_config

VIEWS = {'crop': {'near_size_xy_um': [3, 3], 'wide_size_xy_um': [5, 5], 'intermediate_layer_policy': 'preserve_exported_layers_v1'},
         'near_pitch_um': 0.5, 'wide_pitch_um': 0.5, 'near_resampling_mode': 'area_coverage', 'wide_resampling_mode': 'area_coverage',
         'jitter_xy_um': [0.3, 0.3], 'core_size_xy_um': [2, 2], 'topology_policy': 'common_core_connectivity_v3',
         'connection_semantics': 'global_contact_proxy_unverified', 'comparison_basis': 'between_views_only', 'max_pair_attempts': 3}
RUN_CONFIG = {'schema_version': 'psdl2_run_v1',
              'model': {'roles': ['gate', 'data'], 'layers': ['L1'], 'width': 4, 'feature_dim': 8, 'projection_dim': 4},
              'views': VIEWS,
              'loss': {'alignment_weight': 1, 'variance_weight': 1, 'covariance_weight': 1, 'variance_floor': 2,
                       'variance_epsilon': 0.25, 'supervised_weight': 1, 'supervised_temperature': 1},
              'epochs': 1, 'batch_size': 2, 'learning_rate': 0.001, 'seed': 7}
PER_CASE, MIN_SPACING_UM, GRID_STEP_UM, TOPK = 6, 1.0, 1.0, 6
LINEAGE = {'PROD_R': 'line_train', 'PROD_T': 'line_validation', DB_PRODUCT: 'line_test'}
ASSIGN = {'line_train': 'train', 'line_validation': 'validation', 'line_test': 'test'}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1), encoding='utf-8')
    return path


def rel(path, start):
    return os.path.relpath(Path(path).resolve(), Path(start).resolve())


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class Flow:
    """Runs and records the steps; `step` fails the test with the full stderr when a DL2 or compare step breaks."""

    def __init__(self, tmp):
        self.tmp, self.log = tmp, []
        self.settings = dl2_settings()

    def dl2(self, label, module, *argv, ok=(0,)):
        started = time.time()
        done = dl2_run(self.settings, module, *argv, cwd=self.tmp)
        self.log.append((label, done.returncode, round(time.time() - started, 1)))
        assert done.returncode in ok, f'DL2 step {label!r}: psdl2.{module} exit {done.returncode}\nargv={[str(a) for a in argv]}\n' \
                                      f'stderr={done.stderr[-3000:]}\nstdout={done.stdout[-1500:]}'
        return done


def manifest(path, sources):
    """psdl2_dataset_v1; relative paths are relative to the manifest."""
    base = path.parent
    rows = []
    for source in sources:
        row = {'export': rel(source['export'], base), 'export_sha256': sha(source['export']),
               'mask_derivation': rel(source['mask_derivation'], base), 'patch_sets': [rel(p, base) for p in source.get('patch_sets', [])]}
        if source.get('assets_root') is not None:
            row['assets_root'] = rel(source['assets_root'], base)
        rows.append(row)
    return write_json(path, {'schema_version': 'psdl2_dataset_v1', 'sources': rows})


@pytest.fixture(scope='module')
def flow(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('dl2_e2e')
    f = Flow(tmp)
    # DL2 must be the worktree code, not an editable install of another checkout
    where, probe = dl2_probe(f.settings)
    f.psdl2_file = where
    assert where.startswith(str(Path(f.settings[0]).resolve())) or where.startswith(f.settings[0]), (where, probe.stderr)

    # 1 + 3: the models and candidates-change (top-k 1: one planted change)
    base = tmp / 'world'
    base.mkdir()
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = build_world_product(base, side)
    config = write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    changes = base / 'changes'
    f.changes_code = main(cli_args(config, changes, base / 'dl1', top_k=1))
    f.base, f.config, f.changes = base, config, changes
    f.payload = load_json(changes / 'candidate_queries_v1.json')
    queries = {d['direction']: d['queries'][0] for d in f.payload['directions']}
    centre_r, centre_t = queries['reference_to_target']['center_xy_um'], queries['target_to_reference']['center_xy_um']
    f.queries = queries

    # 2: the database = one template export per product (the three products' ROIs sit at known places)
    f.templates = {}
    spots = {'PROD_R': centre_r, 'PROD_T': centre_t, DB_PRODUCT: [centre_r[0], centre_r[1] - 4.0]}
    sides = {'PROD_R': 'reference', 'PROD_T': 'target', DB_PRODUCT: 'reference'}
    for product, spot in spots.items():
        code, out = export_template(base, product, sides[product], {case: spot for case in
                                    ('lateral_short-A-L1-B-L1', 'lateral_short-A-L1-C-L1')})
        assert code == 0, (product, code)
        f.templates[product] = out
    sources = {p: {'export': out / 'roi/review_rois.json', 'mask_derivation': out / 'roi/mask_derivation.json'}
               for p, out in f.templates.items()}

    # 4: DL2
    dl2 = tmp / 'dl2'
    dl2.mkdir()
    write_json(dl2 / 'config.json', RUN_CONFIG)
    write_json(dl2 / 'lineages.json', LINEAGE)
    write_json(dl2 / 'assign.json', ASSIGN)
    plain = manifest(dl2 / 'plain.json', list(sources.values()))
    products = list(sources)
    patch_sets = {'random': {}, 'grid': {}}
    for number, product in enumerate(products):
        patch_sets['random'][product] = dl2 / f'random_{product}.json'
        f.dl2(f'patches random {product}', 'patches', '--dataset', plain, '--source', number, '--config', dl2 / 'config.json',
              '--mode', 'random', '--seed', 3, '--per-case', PER_CASE, '--min-spacing-um', MIN_SPACING_UM,
              '--out', patch_sets['random'][product])
        patch_sets['grid'][product] = dl2 / f'grid_{product}.json'
        f.dl2(f'patches grid {product}', 'patches', '--dataset', plain, '--source', number, '--config', dl2 / 'config.json',
              '--mode', 'grid', '--grid-step-um', GRID_STEP_UM, '--out', patch_sets['grid'][product])
    train_manifest = manifest(dl2 / 'train_dataset.json', [{**sources[p], 'patch_sets': [patch_sets['random'][p]]} for p in products])
    index_manifest = manifest(dl2 / 'index_dataset.json', [{**sources[p], 'patch_sets': [patch_sets['grid'][p]]} for p in products])
    f.dl2('make_split', 'make_split', '--dataset', train_manifest, '--config', dl2 / 'config.json', '--lineages', dl2 / 'lineages.json',
          '--assign', dl2 / 'assign.json', '--out', dl2 / 'split.json')
    f.dl2('train', 'train', '--dataset', train_manifest, '--config', dl2 / 'config.json', '--out', dl2 / 'model.pt',
          '--lineages', dl2 / 'lineages.json', '--split', dl2 / 'split.json', '--no-progress')
    f.dl2('index', 'index', '--dataset', index_manifest, '--checkpoint', dl2 / 'model.pt', '--out', dl2 / 'index.json')
    f.dl2_dir, f.sources, f.grid_origin = dl2, sources, (ORIGIN_UM, ORIGIN_UM)
    f.batches = {}

    # 4 (database = per-product template export): one envelope per direction, and one envelope holding both directions
    exports = {'reference_to_target': ('reference_export', 'PROD_R'), 'target_to_reference': ('target_export', 'PROD_T')}
    f.query_manifests = {}
    for direction, (folder, _) in exports.items():
        f.query_manifests[direction] = manifest(dl2 / f'query_{direction}.json', [
            {'export': changes / folder / 'review_rois.json', 'mask_derivation': changes / folder / 'mask_derivation.json'}])
    f.query_manifests['both'] = manifest(dl2 / 'query_both.json', [
        {'export': changes / folder / 'review_rois.json', 'mask_derivation': changes / folder / 'mask_derivation.json'}
        for folder, _ in exports.values()])
    for name, cohort in (('r2t', ['PROD_T', DB_PRODUCT]), ('t2r', ['PROD_R', DB_PRODUCT]), ('both', products)):
        write_json(dl2 / f'cohort_{name}.json', cohort)
    for direction, cohort in (('reference_to_target', 'r2t'), ('target_to_reference', 't2r')):
        out = dl2 / f'search_batch_{direction}.json'
        f.dl2(f'search {direction}', 'search', '--query-dataset', f.query_manifests[direction], '--all-ready', '--checkpoint',
              dl2 / 'model.pt', '--index', dl2 / 'index.json', '--cohort', dl2 / f'cohort_{cohort}.json', '--topk', TOPK, '--out', out,
              '--exclude-query-product', ok=(0, 3))
        f.batches[('template', direction)] = out
    both = dl2 / 'search_batch_both.json'
    f.dl2('search both directions', 'search', '--query-dataset', f.query_manifests['both'], '--all-ready', '--checkpoint',
          dl2 / 'model.pt', '--index', dl2 / 'index.json', '--cohort', dl2 / 'cohort_both.json', '--topk', TOPK, '--out', both,
          '--exclude-query-product', ok=(0, 3))
    f.batches[('template', 'both')] = both

    # 4 (database = compare's own *_index_export): grid patches of the two index exports, indexed with the same checkpoint
    index_sources = [{'export': changes / folder / 'review_rois.json', 'mask_derivation': changes / folder / 'mask_derivation.json'}
                     for folder in ('reference_index_export', 'target_index_export')]
    plain_b = manifest(dl2 / 'plain_index_exports.json', index_sources)
    grids_b = []
    for number, folder in enumerate(('reference_index_export', 'target_index_export')):
        grids_b.append(dl2 / f'grid_{folder}.json')
        f.dl2(f'patches grid {folder}', 'patches', '--dataset', plain_b, '--source', number, '--config', dl2 / 'config.json',
              '--mode', 'grid', '--grid-step-um', GRID_STEP_UM, '--out', grids_b[-1])
    index_b = manifest(dl2 / 'index_dataset_b.json', [{**s, 'patch_sets': [g]} for s, g in zip(index_sources, grids_b)])
    f.dl2('index (index exports)', 'index', '--dataset', index_b, '--checkpoint', dl2 / 'model.pt', '--out', dl2 / 'index_b.json')
    for direction, cohort in (('reference_to_target', 'r2t_b'), ('target_to_reference', 't2r_b')):
        write_json(dl2 / f'cohort_{cohort}.json', ['PROD_T'] if cohort == 'r2t_b' else ['PROD_R'])
        out = dl2 / f'search_batch_b_{direction}.json'
        f.dl2(f'search {direction} (index exports)', 'search', '--query-dataset', f.query_manifests[direction], '--all-ready',
              '--checkpoint', dl2 / 'model.pt', '--index', dl2 / 'index_b.json', '--cohort', dl2 / f'cohort_{cohort}.json',
              '--topk', TOPK, '--out', out, ok=(0, 3))
        f.batches[('index_export', direction)] = out
    return f


def compare(*argv):
    """compare CLI in-process; returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


DIRECTIONS = ('reference_to_target', 'target_to_reference')
# variant -> (where the batch envelopes come from, expected index-export grade of confirm-pairs)
VARIANTS = {
    'template_per_direction': (lambda f: {d: f.batches[('template', d)] for d in DIRECTIONS}, 'product_ids_only'),
    'template_one_envelope': (lambda f: {d: f.batches[('template', 'both')] for d in DIRECTIONS}, 'product_ids_only'),
    'index_export': (lambda f: {d: f.batches[('index_export', d)] for d in DIRECTIONS}, 'candidates_index_export'),
}
INDEX_FOLDER = {'reference_to_target': 'target_index_export', 'target_to_reference': 'reference_index_export'}
QUERY_FOLDER = {'reference_to_target': 'reference_export', 'target_to_reference': 'target_export'}
INDEX_PRODUCT = {'reference_to_target': 'PROD_T', 'target_to_reference': 'PROD_R'}


def match_args(matches):
    args = []
    for direction, path in matches.items():
        args += ['--match', f'{direction}={path}']
    return args


def list_candidates(f, matches):
    code, out, err = compare('confirm-pairs', '--list', '--config', f.config, '--candidates', f.changes, *match_args(matches))
    return code, (json.loads(out) if out.strip() else None), err


def first_usable(shown, direction):
    return sorted((c for c in shown['candidates'] if c['direction'] == direction), key=lambda c: (c['query_roi_id'], c['rank']))[0]


def write_decisions(path, ids):
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(('candidate_id', 'decision', 'note'))
        writer.writerows((i, 'accept', 'T12 first usable candidate') for i in ids)
    return path


def run_variant(f, name):
    """list -> decisions (first usable candidate per direction) -> confirm-pairs -> compare-windows. Cached on the flow."""
    cache = f.__dict__.setdefault('variants', {})
    if name in cache:
        return cache[name]
    pick, grade = VARIANTS[name]
    matches = pick(f)
    code, shown, err = list_candidates(f, matches)
    assert code == 0, err
    chosen = [first_usable(shown, d) for d in DIRECTIONS]
    base = f.tmp / f'confirm_{name}'
    base.mkdir()
    decisions = write_decisions(base / 'decisions.csv', [c['candidate_id'] for c in chosen])
    out = base / 'out'
    confirm = compare('confirm-pairs', '--config', f.config, '--candidates', f.changes, '--decisions', decisions, '--labelled-by', 'designer1',
                      '--mutual-tolerance-um', 0.5, '--out', out, *match_args(matches))
    windows = base / 'windows'
    window = compare('compare-windows', '--config', f.config, '--correspondence', out / 'roi_correspondence.json', '--window-sizes', 2,
                     '--out', windows) if confirm[0] == 0 else None
    cache[name] = SimpleNamespace(name=name, matches=matches, shown=shown, chosen=chosen, decisions=decisions, out=out, confirm=confirm,
                                  windows=windows, window=window, grade=grade)
    return cache[name]


@pytest.fixture(scope='module', params=sorted(VARIANTS))
def variant(request, flow):
    return run_variant(flow, request.param)


# ---- DL2 side: the real code accepted compare's files ----------------------------------------------------------------------

def test_dl2_ran_from_the_worktree_and_every_step_exited_as_expected(flow):
    src = str(Path(flow.settings[0]).resolve())
    assert flow.psdl2_file.startswith(src) and flow.psdl2_file.endswith('psdl2/__init__.py'), flow.psdl2_file
    assert flow.changes_code == 0
    names = [label for label, _, _ in flow.log]
    for needed in ('make_split', 'train', 'index', 'search reference_to_target', 'search target_to_reference', 'search both directions',
                   'index (index exports)'):
        assert needed in names, (needed, names)
    assert all(code in (0, 3) for _, code, _ in flow.log), flow.log


def test_dl2_accepted_the_mask_derivation_without_ignoring_it(flow):
    """No ``--ignore-mask-derivation``: DL2 compared compare's derivation (query) with the database's; none was rejected for it."""
    for (flavour, direction), path in flow.batches.items():
        batch = load_json(path)
        assert batch['schema_version'] == 'psdl2_search_batch_v1' and batch['arguments']['ignore_mask_derivation'] is False, (flavour, direction)
        rejected = [r['reason'] for result in batch['results'] for r in result['rejected']]
        assert not [r for r in rejected if r.startswith('mask_derivation')], (flavour, direction, rejected)
        assert all('mask_derivation_comparison' not in result for result in batch['results'])
        assert sum(len(result['primary']) for result in batch['results']) > 0, (flavour, direction)
        assert all(result['status'] == 'available' for result in batch['results'])


def test_negative_control_a_different_derivation_rule_is_rejected_by_dl2(flow):
    """The check above is not vacuous: the same query with another ``rule_version`` is excluded by DL2's derivation comparison."""
    folder = flow.changes / 'reference_export'
    derivation = load_json(folder / 'mask_derivation.json')
    derivation['rule_version'] = 'other_rule_v1'
    changed = write_json(flow.dl2_dir / 'other_rule' / 'mask_derivation.json', derivation)
    query = manifest(flow.dl2_dir / 'query_other_rule.json', [{'export': folder / 'review_rois.json', 'mask_derivation': changed}])
    out = flow.dl2_dir / 'search_other_rule.json'
    flow.dl2('search (other rule, negative control)', 'search', '--query-dataset', query, '--all-ready', '--checkpoint',
             flow.dl2_dir / 'model.pt', '--index', flow.dl2_dir / 'index.json', '--cohort', flow.dl2_dir / 'cohort_r2t.json', '--topk', TOPK,
             '--out', out, ok=(0, 3))
    batch = load_json(out)
    assert sum(len(r['primary']) for r in batch['results']) == 0
    assert {x['reason'] for r in batch['results'] for x in r['rejected']} >= {'mask_derivation_mismatch'}


def test_batch_rankings_carry_the_sha256_of_compare_query_export(flow):
    for (flavour, direction), path in flow.batches.items():
        batch = load_json(path)
        directions = DIRECTIONS if direction == 'both' else (direction,)
        want = {sha(flow.changes / QUERY_FOLDER[d] / 'review_rois.json') for d in directions}
        assert {r['query_export_sha256'] for r in batch['rankings']} == want, (flavour, direction)
        queries = {row['roi_id']: row for d in directions for row in load_json(flow.changes / QUERY_FOLDER[d] / 'review_rois.json')['rois']}
        assert {r['query_roi_id'] for r in batch['rankings']} == set(queries)
        assert all(queries[r['query_roi_id']]['product_id'] == r['query_product_id'] for r in batch['rankings'])


# ---- compare side: confirm-pairs, compare-windows ---------------------------------------------------------------------------

def test_confirm_pairs_list_reads_the_real_envelopes(variant, flow):
    shown = variant.shown
    assert shown['n_candidates'] > 0 and variant.chosen
    for direction in DIRECTIONS:
        info = shown['batch_directions'][direction]
        assert info['index_export_verification'] == variant.grade and info['index_sources_kind'] == 'sources' and info['warnings'] == []
        rows = [c for c in shown['candidates'] if c['direction'] == direction]
        assert rows and {c['index_export_verification'] for c in rows} == {variant.grade}
    skipped = {d: shown['batch_directions'][d]['rankings_skipped_other_direction'] for d in DIRECTIONS}
    assert skipped == ({d: 2 for d in DIRECTIONS} if variant.name == 'template_one_envelope' else {d: 0 for d in DIRECTIONS})
    for item in shown['unavailable_candidates']:                 # every unusable candidate has a reason; a DB-only product is the usual one
        assert item['reason'] in ('candidate_product_not_configured', 'candidate_same_product'), item
    assert 'score' not in json.dumps(shown)


def test_confirm_pairs_passes_the_hash_check_and_writes_the_correspondence(variant, flow):
    code, out, err = variant.confirm
    assert code == 0, err
    corr = load_json(variant.out / 'roi_correspondence.json')
    ready = [p for p in corr['pairs'] if p['status'] == 'ready']
    assert corr['schema_version'] == 'roi_correspondence_v1' and len(ready) >= 1
    sidecar = load_json(variant.out / 'confirmed_pairs_sidecar.json')
    for direction in DIRECTIONS:
        match = sidecar['inputs']['matches'][direction]
        assert match['match_schema'] == 'psdl2_search_batch_v1' and match['sha256'] == sha(variant.matches[direction])
        assert match['index_export_verification'] == variant.grade and match['warnings'] == []
        if variant.grade == 'candidates_index_export':
            used = {s['export_sha256'] for s in match['index_sources_used']}
            assert used == {sha(flow.changes / INDEX_FOLDER[direction] / 'review_rois.json')}
        else:
            assert {s['product_ids'][0] for s in match['index_sources_used']} == {INDEX_PRODUCT[direction]}
    for pair in ready:                                             # a = reference, b = target, producer manual
        assert (pair['a']['product_id'], pair['b']['product_id']) == ('PROD_R', 'PROD_T') and pair['source']['kind'] == 'manual'
    for chosen in variant.chosen:
        assert any(p['pair_id'] in sidecar['pairs'] for p in ready), chosen['candidate_id']


def test_compare_windows_runs_on_the_confirmed_pairs(variant, flow):
    code, out, err = variant.window
    assert code in (0, 3), err
    ready = {p['pair_id'] for p in load_json(variant.out / 'roi_correspondence.json')['pairs'] if p['status'] == 'ready'}
    document = load_json(variant.windows / 'local_delta.json')
    assert {r['pair_id'] for r in document['rows']} == ready and len(ready) >= 1
    assert_only_the_hotspot_diameter_is_available(document, code)


def assert_only_the_hotspot_diameter_is_available(document, code):
    """The synthetic hotspots exist for the 5 um diameter only: exit 3 / ``incomplete_local_delta`` with every other diameter
    ``source_coverage_incomplete``; the 5 um row of every ready pair is computed."""
    assert (code, document['status'], document['reasons']) == (3, 'partial', ['incomplete_local_delta'])
    by_status = {}
    for row in document['rows']:
        by_status.setdefault(row['status'], []).append(row)
    assert {row['diameter_um'] for row in by_status['available']} == {5.0}
    assert len(by_status['available']) == len({row['pair_id'] for row in document['rows']})
    for row in by_status['unavailable']:
        assert row['window']['a']['union']['reasons'] == ['missing_input', 'source_coverage_incomplete']


# ---- the NCC path (psdl2_baseline_ncc_v1) on the same exports: checklist item 6 -----------------------------------------------

def test_baseline_ncc_output_is_read_by_confirm_pairs_and_carries_the_current_export_hashes(flow):
    matches = {}
    for direction in DIRECTIONS:
        out = flow.dl2_dir / f'ncc_{direction}.json'
        flow.dl2(f'baseline_ncc {direction}', 'baseline_ncc', '--query-export', flow.changes / QUERY_FOLDER[direction] / 'review_rois.json',
                 '--index-export', flow.changes / INDEX_FOLDER[direction] / 'review_rois.json', '--all-ready', '--max-memory-mb', 2000,
                 '--window-um', 10, '--topk', 3, '--out', out)
        ncc = load_json(out)
        assert ncc['schema_version'] == 'psdl2_baseline_ncc_v1' and ncc['mode'] == 'all_ready'
        assert ncc['inputs']['query_export_sha256'] == sha(flow.changes / QUERY_FOLDER[direction] / 'review_rois.json')
        assert ncc['inputs']['index_export_sha256'] == sha(flow.changes / INDEX_FOLDER[direction] / 'review_rois.json')
        matches[direction] = out
    code, shown, err = list_candidates(flow, matches)
    assert code == 0, err
    assert shown['n_candidates'] > 0 and 'batch_directions' not in shown
    chosen = [first_usable(shown, d)['candidate_id'] for d in DIRECTIONS]
    out = flow.tmp / 'confirm_ncc'
    decisions = write_decisions(flow.tmp / 'decisions_ncc.csv', chosen)
    code, _, err = compare('confirm-pairs', '--config', flow.config, '--candidates', flow.changes, '--decisions', decisions, '--labelled-by',
                           'designer1', '--mutual-tolerance-um', 0.5, '--out', out, *match_args(matches))
    assert code == 0, err
    assert [p for p in load_json(out / 'roi_correspondence.json')['pairs'] if p['status'] == 'ready']


# ---- adv:3 — the DB's frame/coordinates are the frame/coordinates of compare's *_index_export -------------------------------

def test_template_database_candidates_share_frame_and_coordinates_with_the_index_exports(flow):
    keys = ('frame_id', 'origin_xy_um', 'pixel_pitch_um', 'shape_yx', 'axis_order', 'origin_convention')
    for direction in DIRECTIONS:
        product = INDEX_PRODUCT[direction]
        template = load_json(flow.templates[product] / 'roi/review_rois.json')['rois']
        index_rows = load_json(flow.changes / INDEX_FOLDER[direction] / 'review_rois.json')['rois']
        assert template and index_rows
        for row in template + index_rows:
            assert {k: row['frame'][k] for k in keys} == {k: template[0]['frame'][k] for k in keys}, (product, row['roi_id'])
        # the same data in both exports: per case and electrode the layout asset is byte-identical (full-frame, same coordinates)
        def layouts(rows):
            return {(r['case_id'], l['endpoint']['electrode_id']): l['asset']['sha256'] for r in rows for l in r['source_assets']['layouts']}
        shared = set(layouts(template)) & set(layouts(index_rows))
        assert shared and all(layouts(template)[k] == layouts(index_rows)[k] for k in shared), (product, shared)
        # candidates of the two DB flavours: same frame_id, same lattice (a grid point of one flavour is a grid point of the other)
        frames = {row['frame']['frame_id'] for row in index_rows}
        found = {}
        for flavour in ('template', 'index_export'):
            batch = load_json(flow.batches[(flavour, direction)])
            found[flavour] = {(tuple(c['center_xy_um']), c['frame_id']) for r in batch['rankings'] for c in r['candidates']
                              if c['product_id'] == product}
            assert {frame for _, frame in found[flavour]} == frames, (flavour, direction)
        pitch = template[0]['frame']['pixel_pitch_um']
        common = [(a, b) for a, _ in found['template'] for b, _ in found['index_export']
                  if abs(a[0] - b[0]) <= pitch / 2 and abs(a[1] - b[1]) <= pitch / 2]
        assert common, (direction, found)
        lattice = [(c[0] - flow.grid_origin[0], c[1] - flow.grid_origin[1]) for flavour in found for c, _ in found[flavour]]
        assert all(abs(x / GRID_STEP_UM - round(x / GRID_STEP_UM)) < 1e-6 and abs(y / GRID_STEP_UM - round(y / GRID_STEP_UM)) < 1e-6
                   for x, y in lattice)


# ---- the direct correspondence path (step 7) --------------------------------------------------------------------------------

@pytest.fixture(scope='module')
def direct(flow):
    path = flow.changes / 'direct/roi_correspondence.json'
    windows = flow.tmp / 'direct_windows'
    return path, windows, compare('compare-windows', '--config', flow.config, '--correspondence', path, '--window-sizes', 2, '--out', windows)


def test_compare_windows_on_the_direct_correspondence(flow, direct):
    path, windows, (code, out, err) = direct
    assert code in (0, 3), err
    ready = {p['pair_id'] for p in load_json(path)['pairs'] if p['status'] == 'ready'}
    document = load_json(windows / 'local_delta.json')
    assert ready and {r['pair_id'] for r in document['rows']} == ready
    assert_only_the_hotspot_diameter_is_available(document, code)


# ---- the connection checklist items this run can see ------------------------------------------------------------------------

def test_checklist_items_visible_in_this_run(flow):
    payload, changes = flow.payload, flow.changes
    # 1 match_files point at real files; 2 ready query rows are manual with role and actual_layer; 3 index coverage complete
    for direction in DIRECTIONS:
        for path in payload['match_files'][direction].values():
            assert (changes / path).is_file()
        rows = load_json(changes / QUERY_FOLDER[direction] / 'review_rois.json')['rois']
        assert rows and all(r['status'] == 'ready' and r['source_kind'] == 'manual' for r in rows)
        assert all(e['role'] and e['actual_layer'] for r in rows for e in r['endpoints'])
    assert 'index_coverage_incomplete' not in [w['code'] for w in payload['warnings']]
    # 4 the alignment is the same in the three places
    alignment = load_json(changes / 'alignment.json')
    direct_side = load_json(changes / 'direct/direct_pairs_sidecar.json')
    assert payload['alignment']['status'] == alignment['status'] == direct_side['alignment']['status'] == 'aligned'
    assert payload['alignment']['offset_xy_um'] == direct_side['alignment']['offset_xy_um'] == [0.7, -0.4]
    # 5 / 12 target-direction centre = reference-direction centre + offset (sign: p_target = p_reference + offset)
    r2t, t2r = flow.queries['reference_to_target']['center_xy_um'], flow.queries['target_to_reference']['center_xy_um']
    assert all(abs(t - (r + o)) <= 0.05 for t, r, o in zip(t2r, r2t, alignment['offset_xy_um']))
    # 11 the query rows are the rows of the direct correspondence
    corr = load_json(changes / 'direct/roi_correspondence.json')
    reference_rows = {r['roi_id']: r for r in load_json(changes / 'reference_export/review_rois.json')['rois']}
    target_rows = {r['roi_id']: r for r in load_json(changes / 'target_export/review_rois.json')['rois']}
    for pair in corr['pairs']:
        if pair['status'] != 'ready' or pair['a'].get('roi_id') is None:
            continue
        a, b = reference_rows[pair['a']['roi_id']], target_rows[pair['b']['roi_id']]
        assert pair['a']['center_xy_um'] == a['center_xy_um'] and pair['b']['center_xy_um'] == b['center_xy_um']
        assert all(abs(pb - (pa + o)) <= 0.05 for pa, pb, o in zip(a['center_xy_um'], b['center_xy_um'], alignment['offset_xy_um']))
        assert a['endpoints'] == b['endpoints']


def test_report_what_ran(flow, capsys):
    """Evidence for the final report (run with -s)."""
    with capsys.disabled():
        print('\n[T12] DL2 steps (label, exit, seconds):')
        for entry in flow.log:
            print('   ', entry)
        print(f'[T12] DL2 total {sum(s for _, _, s in flow.log):.1f}s; psdl2 from {flow.psdl2_file}')
        for name in sorted(VARIANTS):
            run = run_variant(flow, name)
            corr = load_json(run.out / 'roi_correspondence.json')
            reasons = {}
            for item in run.shown['unavailable_candidates']:
                reasons[item['reason']] = reasons.get(item['reason'], 0) + 1
            queries = {d: len({c['query_roi_id'] for c in run.shown['candidates'] if c['direction'] == d}) for d in DIRECTIONS}
            print(f'[T12] {name}: confirm-pairs {run.confirm[0]}, compare-windows {run.window[0]}, queries/direction {queries}, '
                  f'usable candidates {run.shown["n_candidates"]}, unusable {reasons}, ready pairs '
                  f'{sum(p["status"] == "ready" for p in corr["pairs"])}, grade {run.grade}')
    assert flow.log
