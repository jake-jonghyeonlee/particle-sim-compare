"""T0b: export-template-rois — hotspot-free single-product template export + mask_derivation."""
import builtins
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

from simcompare.adapters.real_hotspot import HOTSPOT_RE
from simcompare.cli import main
from test_electrode_png import save

SHAPE = (1010, 1020)
DL2 = Path(os.environ.get('PSDL2_ROOT', '/Users/jonghyeonlee/git/particle-sim-dl2'))
LATERAL = 'lateral_short-A-L1-B-L1'
VERT_ONE = 'vertical_short-A-L1-C-L3'        # between: L2
VERT_NONE = 'vertical_short-A-L1-D-L2'       # between: none
VERT_MANY = 'vertical_short-A-L1-E-L5'       # between: L2, L3, L4
VERT_FIRST_UPPER = 'vertical_short-F-L4-A-L2'   # upper endpoint listed first; between: L3
CASES = [LATERAL, VERT_ONE, VERT_NONE, VERT_MANY, VERT_FIRST_UPPER]
STACK = [{'actual_layer': f'L{i}', 'stack_order': i} for i in range(1, 6)]
PAIRS = ('pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n'
         + ''.join(f'P{e},A,{e},line,pair-v1\n' for e in 'BCDEF'))


def mark(index):
    arr = np.zeros(SHAPE, dtype=bool)
    arr[500 + index % 9:502 + index % 9, 500 + (index * 3) % 18:502 + (index * 3) % 18] = True
    arr[10:12, 10:12] = True       # 테두리: crop 밖
    return arr


def make_tree(tmp_path, cases=CASES, *, name='tree'):
    root = tmp_path / name
    for i, case in enumerate(cases):
        save(root / 'images' / case / '1.png', mark(2 * i))
        save(root / 'images' / case / '2.png', mark(2 * i + 1))
    dl1 = tmp_path / ('dl1_' + name)
    save(dl1 / 'PRODUCT/cno/1_CNT.png', mark(15))
    save(dl1 / 'PRODUCT/cno/2_CNT.png', mark(16))
    return root, dl1


def write_config(tmp_path, root, dl1, *, cases=CASES, stack=STACK, edit=None, name='template.yaml'):
    (tmp_path / 'pairs.csv').write_text(PAIRS)
    cfg = {'schema_version': 'template_export_config_v1', 'data_dir': str(root), 'cnt_from_dl1_root': str(dl1),
           'product_id': 'PRODUCT', 'design_version': 'DESIGN', 'source_id': 'SOURCE', 'process_step_id': 'STEP',
           'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT', 'layer_stack': [dict(row) for row in stack],
           'electrode_role_policy': 'electrode_id', 'pair_table_path': 'pairs.csv', 'source_version': 'layout_v1',
           'max_asset_memory_mb': 256, 'mask_derivation_rule': 'between_stack_order_v1',
           'rois': [{'case_id': c, 'center_xy_um': [50.5, 50.5]} for c in cases]}
    if edit:
        edit(cfg)
    path = tmp_path / name
    path.write_text(yaml.safe_dump(cfg))
    return path


def run(config, out):
    return main(['export-template-rois', '--config', str(config), '--out', str(out)])


def load(out, rel):
    return json.loads((Path(out) / rel).read_text(encoding='utf-8'))


@pytest.fixture
def exported(tmp_path):
    root, dl1 = make_tree(tmp_path)
    out = tmp_path / 'out'
    assert run(write_config(tmp_path, root, dl1), out) == 0
    return tmp_path, root, dl1, out


def derivation_by_case(out):
    return {e['case']['endpoints'][0]['electrode_id'] + e['case']['endpoints'][1]['electrode_id']
            + e['case']['endpoints'][0]['actual_layer'] + e['case']['endpoints'][1]['actual_layer']: e
            for e in load(out, 'roi/mask_derivation.json')['cases']}


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(folder).rglob('*')) if p.is_file()}


# ---- structure and layout references ----

def test_output_layout_and_review_rois_reference_not_copy_assets(exported):
    _, _, _, out = exported
    assert sorted(p.name for p in out.iterdir()) == ['data', 'roi', 'template_export_execution.json']
    assert sorted(p.name for p in (out / 'roi').iterdir()) == ['mask_derivation.json', 'review_rois.json']
    payload = load(out, 'roi/review_rois.json')
    assert payload['schema_version'] == 'review_roi_v1' and payload['status'] == 'available'
    assert payload['assets_root'] == str((out / 'data').resolve())   # published path, not the staging path
    assert len(payload['rois']) == 5 and all(r['status'] == 'ready' and r['source_kind'] == 'manual' for r in payload['rois'])
    for name in ('manifest.json', 'review_assets.json', 'electrode_asset_import_record.json', 'pairs.csv'):
        assert (out / 'data' / name).is_file()
    assert not (out / 'roi' / 'assets').exists()
    assert not list(out.parent.glob('out.tmp-*'))


def test_layouts_are_per_case_1_2_png_cnt_shared_via_empty(exported):
    _, root, dl1, out = exported
    payload = load(out, 'roi/review_rois.json')

    def zero(path):
        return np.asarray(Image.open(path)) == 0

    cnt_refs = set()
    for row in payload['rois']:
        folder = root / 'images' / row['case_id']
        layouts = row['source_assets']['layouts']
        assert [e['endpoint']['electrode_id'] for e in layouts] == [e['electrode_id'] for e in row['endpoints']]
        for ref, name in zip(layouts, ('1.png', '2.png')):
            assert ref['asset']['value_semantics'] == 'binary_zero_is_marked'
            assert np.array_equal(zero(out / 'data' / ref['asset']['path']), zero(folder / name)[500:-500, 500:-500])
        assert layouts[0]['asset']['path'] != layouts[1]['asset']['path']
        assert row['source_assets']['via'] == []
        cnt_refs.add(row['source_assets']['cnt']['path'])
    assert len({r['source_assets']['layouts'][0]['asset']['path'] for r in payload['rois']}) == 5   # per case
    assert len(cnt_refs) == 1                                                                      # shared per product
    union = zero(dl1 / 'PRODUCT/cno/1_CNT.png')[500:-500, 500:-500] | zero(dl1 / 'PRODUCT/cno/2_CNT.png')[500:-500, 500:-500]
    assert np.array_equal(zero(out / 'data' / cnt_refs.pop()), union)


def test_density_sentinel_and_input_tree_never_leak_or_change(tmp_path):
    root, dl1 = make_tree(tmp_path)
    before = snapshot(root), snapshot(dl1)
    out = tmp_path / 'out'
    assert run(write_config(tmp_path, root, dl1), out) == 0
    assert (snapshot(root), snapshot(dl1)) == before
    assert not [p for p in out.rglob('*.json') if 'template_export_unused' in p.read_text(encoding='utf-8')]


# ---- mask_derivation ----

def test_mask_derivation_vertical_between_layers_and_lateral(exported):
    _, _, _, out = exported
    doc = load(out, 'roi/mask_derivation.json')
    assert doc['schema_version'] == 'psdl2_mask_derivation_v1' and doc['rule_version'] == 'between_stack_order_v1'
    assert set(doc) == {'schema_version', 'rule_version', 'cases'}
    entries = derivation_by_case(out)
    assert len(entries) == 5
    assert all(set(e) == {'case', 'processed_endpoint', 'removed_layers'} for e in entries.values())
    lateral = entries['ABL1L1']
    assert lateral['case']['failure_type'] == 'lateral_short'
    assert lateral['processed_endpoint'] is None and lateral['removed_layers'] == []
    one = entries['ACL1L3']
    assert (one['processed_endpoint'], one['removed_layers']) == ('C', ['L2'])
    assert one['case'] == {'failure_type': 'vertical_short', 'endpoints': [
        {'electrode_id': 'A', 'role': 'A', 'actual_layer': 'L1'}, {'electrode_id': 'C', 'role': 'C', 'actual_layer': 'L3'}]}
    none = entries['ADL1L2']
    assert (none['processed_endpoint'], none['removed_layers']) == ('D', [])           # adjacent layers: nothing between
    many = entries['AEL1L5']
    assert (many['processed_endpoint'], many['removed_layers']) == ('E', ['L2', 'L3', 'L4'])
    first = entries['AFL2L4']
    assert (first['processed_endpoint'], first['removed_layers']) == ('F', ['L3'])     # upper endpoint listed first


def test_mask_derivation_follows_stack_order_not_layer_name_order(tmp_path):
    root, dl1 = make_tree(tmp_path, [VERT_ONE])
    stack = [{'actual_layer': 'L1', 'stack_order': 10}, {'actual_layer': 'L2', 'stack_order': 99},
             {'actual_layer': 'L3', 'stack_order': 20}, {'actual_layer': 'L9', 'stack_order': 15}]
    out = tmp_path / 'out'
    assert run(write_config(tmp_path, root, dl1, cases=[VERT_ONE], stack=stack), out) == 0
    entry = load(out, 'roi/mask_derivation.json')['cases'][0]
    assert (entry['processed_endpoint'], entry['removed_layers']) == ('C', ['L9'])      # L2 (order 99) is not between 10 and 20


def test_order_swapped_case_folders_yield_one_entry(tmp_path):
    swapped = 'vertical_short-C-L3-A-L1'
    root, dl1 = make_tree(tmp_path, [VERT_ONE, swapped])
    out = tmp_path / 'out'
    assert run(write_config(tmp_path, root, dl1, cases=[VERT_ONE, swapped]), out) == 0
    cases = load(out, 'roi/mask_derivation.json')['cases']
    assert len(cases) == 1
    assert [e['electrode_id'] for e in cases[0]['case']['endpoints']] == ['A', 'C']
    assert (cases[0]['processed_endpoint'], cases[0]['removed_layers']) == ('C', ['L2'])


def test_removed_layers_sorted_by_name_not_stack_order(tmp_path):
    root, dl1 = make_tree(tmp_path, [VERT_MANY])
    stack = [{'actual_layer': 'L1', 'stack_order': 1}, {'actual_layer': 'L4', 'stack_order': 2},
             {'actual_layer': 'L2', 'stack_order': 3}, {'actual_layer': 'L3', 'stack_order': 4},
             {'actual_layer': 'L5', 'stack_order': 5}]
    out = tmp_path / 'out'
    assert run(write_config(tmp_path, root, dl1, cases=[VERT_MANY], stack=stack), out) == 0
    assert load(out, 'roi/mask_derivation.json')['cases'][0]['removed_layers'] == ['L2', 'L3', 'L4']


def test_one_entry_per_case_even_with_several_rois_for_it(tmp_path):
    root, dl1 = make_tree(tmp_path, [VERT_ONE])
    def edit(cfg):
        cfg['rois'] = [{'case_id': VERT_ONE, 'center_xy_um': [50.5, 50.5]}, {'case_id': VERT_ONE, 'center_xy_um': [50.7, 50.3]}]
    out = tmp_path / 'out'
    assert run(write_config(tmp_path, root, dl1, cases=[VERT_ONE], edit=edit), out) == 0
    assert len(load(out, 'roi/review_rois.json')['rois']) == 2
    assert len(load(out, 'roi/mask_derivation.json')['cases']) == 1


def test_blocked_roi_exits_3_and_derivation_covers_ready_rois_only(tmp_path, capsys):
    root, dl1 = make_tree(tmp_path)
    def edit(cfg):
        cfg['rois'][1]['center_xy_um'] = [5000.0, 5.0]          # outside the frame
    out = tmp_path / 'out'
    assert run(write_config(tmp_path, root, dl1, edit=edit), out) == 3
    payload = load(out, 'roi/review_rois.json')
    blocked = [r for r in payload['rois'] if r['status'] == 'blocked']
    assert [r['case_id'] for r in blocked] == [VERT_ONE] and 'position_outside_source_frame' in blocked[0]['reasons']
    cases = load(out, 'roi/mask_derivation.json')['cases']
    assert len(cases) == 4
    assert all(e['case']['endpoints'][1]['electrode_id'] != 'C' for e in cases)
    assert load(out, 'template_export_execution.json')['exit_code'] == 3


def test_unknown_case_in_rois_is_a_blocked_roi_not_a_crash(tmp_path):
    root, dl1 = make_tree(tmp_path)
    def edit(cfg):
        cfg['rois'].append({'case_id': 'lateral_short-Z-L1-Y-L1', 'center_xy_um': [50.5, 50.5]})
    out = tmp_path / 'out'
    assert run(write_config(tmp_path, root, dl1, edit=edit), out) == 3
    assert len(load(out, 'roi/mask_derivation.json')['cases']) == 5


# ---- input / config errors (exit 2, nothing published) ----

def _error(tmp_path, capsys, config, code):
    out = tmp_path / 'out'
    assert run(config, out) == 2
    assert code in capsys.readouterr().err
    assert not out.exists() and not list(tmp_path.glob('out.tmp-*'))


def test_missing_or_unknown_mask_derivation_rule_is_exit_2(tmp_path, capsys):
    root, dl1 = make_tree(tmp_path)
    _error(tmp_path, capsys, write_config(tmp_path, root, dl1, edit=lambda c: c.pop('mask_derivation_rule')),
           'mask_derivation_rule_required')
    for value in ('explicit', 'between_stack_order', 'BETWEEN_STACK_ORDER_V1'):
        _error(tmp_path, capsys, write_config(tmp_path, root, dl1, edit=lambda c, v=value: c.update(mask_derivation_rule=v)),
               'mask_derivation_rule_unknown')


def test_endpoint_layer_missing_from_layer_stack_is_exit_2(tmp_path, capsys):
    root, dl1 = make_tree(tmp_path)
    stack = [row for row in STACK if row['actual_layer'] != 'L3']
    _error(tmp_path, capsys, write_config(tmp_path, root, dl1, stack=stack), 'template_layer_missing_from_stack')


@pytest.mark.parametrize('edit', [lambda c: c.update(density_process_layer='P'), lambda c: c.pop('source_version'),
                                  lambda c: c.pop('max_asset_memory_mb'), lambda c: c.pop('rois'),
                                  lambda c: c.update(rois=[{'case_id': LATERAL}]),
                                  lambda c: c['layer_stack'][0].update(density_process_layer='P1'),
                                  lambda c: c.pop('electrode_role_policy')])
def test_invalid_config_is_exit_2(tmp_path, capsys, edit):
    root, dl1 = make_tree(tmp_path)
    _error(tmp_path, capsys, write_config(tmp_path, root, dl1, edit=edit), 'invalid_template_config')


def test_missing_config_file_is_exit_2(tmp_path, capsys):
    assert run(tmp_path / 'nope.yaml', tmp_path / 'out') == 2
    assert '오류' in capsys.readouterr().err and not (tmp_path / 'out').exists()


def test_existing_output_is_rejected_and_left_untouched(tmp_path, capsys):
    root, dl1 = make_tree(tmp_path)
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'keep.txt').write_text('mine')
    assert run(write_config(tmp_path, root, dl1), out) == 2
    assert 'template_output_exists' in capsys.readouterr().err
    assert snapshot(out) == {'keep.txt': hashlib.sha256(b'mine').hexdigest()}


def test_dangling_symlink_output_is_refused(tmp_path, capsys):
    root, dl1 = make_tree(tmp_path)
    out = tmp_path / 'out'
    target = tmp_path / 'nowhere'
    out.symlink_to(target)
    assert run(write_config(tmp_path, root, dl1), out) == 2
    assert 'template_output_exists' in capsys.readouterr().err
    assert out.is_symlink() and not target.exists()


def test_failure_after_staging_removes_only_what_this_run_created(tmp_path, capsys, monkeypatch):
    root, dl1 = make_tree(tmp_path)
    sibling = tmp_path / 'deep' / 'sibling.txt'
    sibling.parent.mkdir()
    sibling.write_text('keep')
    import simcompare.adapters.electrode_png as electrode_png

    def boom(*args, **kwargs):
        raise OSError('disk full (simulated)')
    monkeypatch.setattr(electrode_png, 'import_electrode_assets', boom)
    out = tmp_path / 'deep' / 'new' / 'out'
    assert run(write_config(tmp_path, root, dl1), out) == 2
    assert 'disk full' in capsys.readouterr().err
    assert sorted(p.name for p in (tmp_path / 'deep').iterdir()) == ['sibling.txt']     # staging and new parents gone
    assert sibling.read_text() == 'keep'


# ---- hotspot never touched ----

class Reads:
    """Record every path opened through the usual read APIs."""

    def __init__(self, monkeypatch):
        self.paths = []
        real_open, real_load, real_image = builtins.open, np.load, Image.open
        real_bytes, real_text = Path.read_bytes, Path.read_text
        real_io_open, real_path_open = io.open, Path.open

        def note(target):
            if isinstance(target, (str, os.PathLike)):
                self.paths.append(os.fspath(target))

        monkeypatch.setattr(builtins, 'open', lambda f, *a, **k: (note(f), real_open(f, *a, **k))[1])
        monkeypatch.setattr(io, 'open', lambda f, *a, **k: (note(f), real_io_open(f, *a, **k))[1])
        monkeypatch.setattr(Path, 'open', lambda self_, *a, **k: (note(self_), real_path_open(self_, *a, **k))[1])
        monkeypatch.setattr(np, 'load', lambda f, *a, **k: (note(f), real_load(f, *a, **k))[1])
        monkeypatch.setattr(Image, 'open', lambda f, *a, **k: (note(f), real_image(f, *a, **k))[1])
        monkeypatch.setattr(Path, 'read_bytes', lambda self_: (note(self_), real_bytes(self_))[1])
        monkeypatch.setattr(Path, 'read_text', lambda self_, *a, **k: (note(self_), real_text(self_, *a, **k))[1])


def is_hotspot_like(path):
    parts = Path(path).parts
    return bool(HOTSPOT_RE.fullmatch(Path(path).name)) or 'result' in parts or path.endswith('.npy')


def test_hotspot_free_run_opens_only_pngs_and_generated_files(tmp_path, monkeypatch):
    root, dl1 = make_tree(tmp_path)
    config = write_config(tmp_path, root, dl1)
    reads = Reads(monkeypatch)
    out = tmp_path / 'out'
    assert run(config, out) == 0
    in_tree = [p for p in reads.paths if str(root) in p]
    assert in_tree and all(Path(p).name in ('1.png', '2.png') for p in in_tree)
    assert not [p for p in reads.paths if is_hotspot_like(p)]
    declared = load(out, 'template_export_execution.json')['files_read']
    assert sorted(declared['input_tree']) == sorted({os.path.relpath(p, root) for p in in_tree})
    assert all(('cno/' in p) for p in [p for p in reads.paths if str(dl1) in p])
    assert not any(p.endswith('.npy') for p in (str(q) for q in out.rglob('*')))
    assert load(out, 'template_export_execution.json')['hotspot_inputs'] == {
        'scanned_names_only': True, 'found': 0, 'hotspot_or_ca_arrays_opened': False}


@pytest.mark.parametrize('kind,png_name,result_name', [
    ('hotspot_png', 'hotspot_lateral_short05.png', 'result'), ('result_dir', 'hotspot_lateral_short05.png', 'result'),
    ('both', 'hotspot_lateral_short05.png', 'result'),
    ('hotspot_png', 'HOTSPOT_LATERAL_SHORT05.PNG', 'result'), ('result_dir', 'x.png', 'Result'),
    ('both', 'HOTSPOT_LATERAL_SHORT05.PNG', 'Result')])
def test_tree_with_hotspot_files_is_rejected_without_opening_them(tmp_path, monkeypatch, capsys, kind, png_name, result_name):
    root, dl1 = make_tree(tmp_path)
    case = root / 'images' / LATERAL
    planted = []
    if kind in ('hotspot_png', 'both'):
        Image.fromarray(np.zeros((10, 20), dtype=np.uint8)).save(case / png_name, format='PNG')
        planted.append(case / png_name)
    if kind in ('result_dir', 'both'):
        (case / result_name).mkdir()
        (case / result_name / 'lateral_short.csv').write_text('defect_size(um),Area(um2)\n1,0.5\n')
        planted.append(case / result_name / 'lateral_short.csv')
    config = write_config(tmp_path, root, dl1)
    reads = Reads(monkeypatch)
    out = tmp_path / 'out'
    assert run(config, out) == 2
    assert 'template_hotspot_input_present' in capsys.readouterr().err
    assert not out.exists() and not list(tmp_path.glob('out.tmp-*'))
    assert not [p for p in reads.paths if str(root) in p]             # nothing in the input tree was opened at all
    assert all(p.exists() for p in planted)


# ---- DL2 validation ----

def test_review_rois_pass_dl2_load_export(exported):
    _, _, _, out = exported
    python = DL2 / '.venv/bin/python'
    if not python.is_file():
        pytest.skip('PSDL2_ROOT (particle-sim-dl2 checkout with .venv) not available at ' + str(DL2))
    code = ("import json,sys\nsys.path.insert(0, sys.argv[1] + '/src')\nfrom psdl2.data import load_export\n"
            "e = load_export(sys.argv[2], allow_unversioned_producer=True)\n"
            "print(json.dumps({'ready': len(e.ready), 'blocked': [dict(r)['reasons'] for r in e.blocked]}))\n")
    result = subprocess.run([str(python), '-I', '-c', code, str(DL2), str(out / 'roi/review_rois.json')],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'ready': 5, 'blocked': []}


def test_output_is_deterministic_for_the_same_input(tmp_path):
    root, dl1 = make_tree(tmp_path)
    config = write_config(tmp_path, root, dl1)
    first, second = tmp_path / 'one', tmp_path / 'two'
    assert run(config, first) == 0 and run(config, second) == 0
    for rel in ('roi/mask_derivation.json', 'data/review_assets.json', 'data/manifest.json'):
        assert (first / rel).read_bytes() == (second / rel).read_bytes()
    a, b = load(first, 'roi/review_rois.json'), load(second, 'roi/review_rois.json')
    assert a.pop('assets_root') != b.pop('assets_root') and a == b
