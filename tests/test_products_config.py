"""pasx/T3: products.yaml loading, names and paths, group compatibility check, generated settings, CLI."""
import copy
import dataclasses
import json
import os
from pathlib import Path

import pytest
import yaml

from simcompare import cli
from simcompare import products_config as pc
from simcompare.compare2_inputs import load_compare2_config
from simcompare.numeric_schema import NumericContractError
from simcompare.pipeline import load_numeric_config

TEMPLATE = Path(__file__).resolve().parents[1] / 'configs/etx/templates/products.yaml'
LAT_AB, LAT_AC = 'lateral_short-A-L1-B-L1', 'lateral_short-A-L1-C-L1'
VER_AB = 'vertical_short-A-L1-B-L2'
LEFT_OUT = ['lateral_short-GH-L1-B-L1',           # built-in rule (GH)
            'lateral_short-X-L1-Y-L1',            # pairs.csv `excluded` row
            'vertical_short-A-L1-B-SD2']          # SD2 vertical short (case exclusion; SD2 is not in the stack either)
PAIRS = ('pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n'
         'AB,A,B,line,v1\nAC,A,C,pixel,v1\nXY,X,Y,excluded,v1\n')
DENSITY = ',1,2\nP1,0.1,0.2\nP2,0.1,0.2\n'
STACK = [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'},
         {'actual_layer': 'L2', 'stack_order': 2, 'density_process_layer': 'P2'}]
THK = 'layers\tdistance\nL1L2\t3\n'


def _case(base, product, name):
    folder = base / 'data/input' / product / name
    folder.mkdir(parents=True)
    (folder / '1.png').write_bytes(b'')       # names only: no file is ever decoded


def _cno(base, product, name):
    (base / 'data/input' / product / name / 'result').mkdir(parents=True)


def _doc(base):
    assignment = {'layer_stack': copy.deepcopy(STACK), 'pair_table_path': 'data/input/pairs.csv',
                  'interlayer_distance_path': 'data/input/thk.txt', 'electrode_role_policy': 'electrode_id'}
    product = {'hotspot_root': None, 'model_group': 'mobile', 'resolution': [100, 200], 'dl1_model': None,
               'condition_assignment': assignment}
    return {
        'schema_version': 'simcompare_products_v1',
        'common': {'density_dir': 'data/input/density',
                   'model_groups': {'mobile': {'glass_area': {'value': 1000, 'unit': 'mm2'},
                                               'weight_line': 2.5, 'weight_pixel': 2.5}},
                   'report': {'dl1_root': 'data/input/dl1', 'map_window_um': 10, 'diameter_um': 3, 'top_n': 4},
                   'numeric': {'mode': 'real', 'map': {'diameter_um': 5}}},
        'groups': {'G': ['P1', 'P2'], 'H': ['P3']},
        'products': {
            'P1': dict(copy.deepcopy(product), note='first', hotspot_root='data/input/P1', dl1_model='P1',
                       unit_size_um=[45, 45]),
            'P2': dict(copy.deepcopy(product), hotspot_root='data/input/P2', dl1_model='P2'),
            'P3': dict(copy.deepcopy(product), hotspot_root='data/input/P3'),
        }}


def _world(tmp_path, mutate=None, extra_cases=()):
    """A products.yaml folder with three products: P1 {AB, left-out cases}, P2 {AC}, P3 (other group) {AB}."""
    base = tmp_path / 'base'
    for product, names in (('P1', [LAT_AB] + LEFT_OUT), ('P2', [LAT_AC]), ('P3', [LAT_AB])):
        for name in names:
            _case(base, product, name)
    (base / 'data/input/density').mkdir(parents=True)
    for kind in ('line', 'pixel'):
        (base / 'data/input/density' / ('mobile_%s.csv' % kind)).write_text(DENSITY, encoding='utf-8')
    (base / 'data/input/pairs.csv').write_text(PAIRS, encoding='utf-8')
    (base / 'data/input/thk.txt').write_text(THK, encoding='utf-8')
    for product, name in extra_cases:
        _case(base, product, name)
    doc = _doc(base)
    if mutate:
        mutate(doc, base)
    path = base / 'products.yaml'
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding='utf-8')
    return base, path


def _error(path, **kwargs):
    with pytest.raises(NumericContractError) as info:
        pc.run_products_config(path, **kwargs)
    return info.value


def _violations(path):
    exc = _error(path, check=True)
    assert isinstance(exc, pc.GroupCompatibilityError)
    return {(v['code'], v['product'], v['case']) for v in exc.violations}


def test_template_loads_and_names_ok():
    config = pc.load_products_config(TEMPLATE)
    assert config.groups == {'QA4': ['QA4_R00', 'QA4_T00']}
    assert config.products['QA4_T00']['condition_assignment']['product_id'] == 'QA4_T00'
    assert config.products['QA4_T00']['condition_assignment']['interlayer_distance_path'].endswith('QA4_T00.txt')


# ---- names ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('name', ['.', '..', '_set', '1abc', 'a__b', 'CON', 'con', 'Com1', 'lpt9', 'NUL', 'a' * 65, '',
                                  'a b', 'a.b', 'x/y'])
def test_bad_product_and_group_names_refused(name):
    with pytest.raises(NumericContractError) as info:
        pc.check_name(name, 'product')
    assert info.value.code == 'invalid_product_name'
    with pytest.raises(NumericContractError) as info:
        pc.check_name(name, 'group')
    assert info.value.code == 'invalid_group_name'


def test_good_names_accepted():
    for name in ('a', 'QA4_R00', 'x-y', 'A' * 64, 'COM10', 'CONN'):
        assert pc.check_name(name, 'product') == name


def test_bad_names_refused_at_load(tmp_path):
    def rename(doc, base):
        doc['products']['a__b'] = doc['products'].pop('P3')
        doc['groups']['H'] = ['a__b']
    assert _error(_world(tmp_path, rename)[1], check=True).code == 'invalid_product_name'


def test_names_differing_only_by_case_refused(tmp_path):
    def rename(doc, base):
        doc['products']['p3'] = doc['products'].pop('P3')
        doc['products']['p1'] = copy.deepcopy(doc['products']['P1'])
        doc['groups']['H'] = ['p3', 'p1']
    assert _error(_world(tmp_path, rename)[1], check=True).code == 'duplicate_product_name'

    def groups(doc, base):
        doc['groups']['g'] = doc['groups'].pop('H')
    assert _error(_world(tmp_path / 'w2', groups)[1], check=True).code == 'duplicate_group_name'


# ---- structure ------------------------------------------------------------------------------------------------------

def test_unknown_keys_refused_but_note_is_free(tmp_path):
    path = _world(tmp_path, lambda d, b: d['products']['P1'].update(note={'free': ['text', 1]}))[1]
    assert pc.run_products_config(path, check=True)['status'] == 'checked'
    for mutate in (lambda d, b: d['products']['P1'].update(colour='red'),
                   lambda d, b: d['common'].update(extra=1),
                   lambda d, b: d.update(extra=1),
                   lambda d, b: d['common']['numeric'].update(reference={})):
        assert _error(_world(tmp_path / 'u', mutate)[1], check=True).code == 'invalid_products_config'
        _rmtree(tmp_path / 'u')


def _rmtree(path):
    import shutil
    shutil.rmtree(str(path))


def test_group_membership_rules(tmp_path):
    cases = [
        (lambda d, b: d['groups']['H'].append('P1'), 'product_in_two_groups'),
        (lambda d, b: d['groups']['H'].append('NOPE'), 'unknown_product_in_group'),
        (lambda d, b: d['groups'].update(H=['P3', 'P3']), 'invalid_products_config'),
        (lambda d, b: d['groups'].pop('H'), 'product_without_group'),
        (lambda d, b: d['products']['P1'].update(model_group='nope'), 'invalid_products_config'),
        (lambda d, b: d['products']['P1']['condition_assignment'].update(product_id='P2'), 'invalid_products_config'),
        (lambda d, b: d['products']['P1']['condition_assignment'].pop('pair_table_path'), 'pair_table_missing'),
    ]
    for index, (mutate, code) in enumerate(cases):
        assert _error(_world(tmp_path / str(index), mutate)[1], check=True).code == code, index


# ---- paths ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('value, code', [('/abs/path', 'absolute_or_nonportable_path'),
                                         ('C:/abs', 'absolute_or_nonportable_path'),
                                         ('data\\input\\P1', 'absolute_or_nonportable_path'),
                                         ('../outside', 'relative_path_escape'),
                                         ('data/../x', 'relative_path_escape')])
def test_bad_input_paths_refused(tmp_path, value, code):
    for where in ('hotspot', 'density', 'pairs'):
        def mutate(doc, base, where=where):
            if where == 'hotspot':
                doc['products']['P1']['hotspot_root'] = value
            elif where == 'density':
                doc['common']['density_dir'] = value
            else:
                doc['products']['P1']['condition_assignment']['pair_table_path'] = value
        path = _world(tmp_path / where, mutate)[1]
        assert _error(path, check=True).code == code


@pytest.mark.parametrize('inside', ['data/products', 'data/products/P1/hotspots/current', 'data/compare/x', 'data/_runs',
                                    'data'])
def test_input_overlapping_an_output_root_refused(tmp_path, inside):
    path = _world(tmp_path, lambda d, b: d['products']['P1'].update(hotspot_root=inside))[1]
    assert _error(path, check=True).code == 'input_path_overlaps_output'


def test_output_component_symlink_refused(tmp_path):
    base, path = _world(tmp_path)
    (base / 'elsewhere').mkdir()
    (base / 'data/products').symlink_to(base / 'elsewhere', target_is_directory=True)
    assert _error(path).code == 'output_path_symlink'
    assert not any((base / 'elsewhere').iterdir())


def test_gen_file_without_marker_is_not_overwritten(tmp_path):
    base, path = _world(tmp_path)
    mine = base / 'gen.pair.P1__P2.real.yaml'
    mine.write_text('# my own file\n', encoding='utf-8')
    exc = _error(path, pairs=[('P1', 'P2')], data_dirs={'P1': 'd1', 'P2': 'd2'})
    assert exc.code == 'gen_file_not_owned'
    assert mine.read_text(encoding='utf-8') == '# my own file\n'
    assert not (base / 'data/products/_groups').exists()      # nothing was written


# ---- group compatibility ---------------------------------------------------------------------------------------------

def test_compatible_group_union_and_left_out_cases(tmp_path):
    base, path = _world(tmp_path)
    summary = pc.run_products_config(path)
    names = sorted([LAT_AB, LAT_AC] + LEFT_OUT)
    assert summary['groups']['G']['union_count'] == len(names)
    text = (base / 'data/products/_groups/G/expected_cases.txt').read_text(encoding='utf-8')
    lines = text.splitlines()
    assert lines[0].startswith('# union_sha256: ') and lines[0].split(': ')[1] == summary['groups']['G']['union_sha256']
    assert lines[1:] == names
    info = json.loads((base / 'data/products/_groups/G/group.json').read_text(encoding='utf-8'))
    assert info['products'] == ['P1', 'P2'] and info['union_count'] == len(names)
    assert info['checks']['status'] == 'ok' and len(info['pair_table_sha256']) == 64
    assert info['per_product']['P2']['left_out_by_rule'] == len(LEFT_OUT)


def test_pair_table_mismatch(tmp_path):
    def mutate(doc, base):
        (base / 'data/input/pairs2.csv').write_text(PAIRS + 'AD,A,D,line,v1\n', encoding='utf-8')
        doc['products']['P2']['condition_assignment']['pair_table_path'] = 'data/input/pairs2.csv'
    found = _violations(_world(tmp_path, mutate)[1])
    assert {('group_pair_table_mismatch', 'P1', None), ('group_pair_table_mismatch', 'P2', None)} <= found


def test_layer_unassigned_and_vertical_needs_distance_table_in_one_run(tmp_path):
    def mutate(doc, base):
        doc['products']['P2']['condition_assignment']['layer_stack'] = STACK[:1]
        doc['products']['P2']['condition_assignment'].pop('interlayer_distance_path')
    found = _violations(_world(tmp_path, mutate, extra_cases=[('P1', VER_AB)])[1])
    # P2 has no L2: unassigned (reported for the vertical case); the distance rule only sees cases that passed the layer check
    assert ('group_case_layer_unassigned', 'P2', VER_AB) in found
    assert not any(code == 'group_case_layer_unassigned' and product == 'P1' for code, product, _ in found)


def test_distance_table_required(tmp_path):
    def mutate(doc, base):
        doc['products']['P2']['condition_assignment'].pop('interlayer_distance_path')
    found = _violations(_world(tmp_path, mutate, extra_cases=[('P1', VER_AB)])[1])
    assert found == {('group_distance_table_required', 'P2', VER_AB)}


def test_layer_mismatch_and_vertical_same_layer(tmp_path):
    bad_lateral, bad_vertical = 'lateral_short-A-L1-B-L2', 'vertical_short-A-L1-B-L1'
    found = _violations(_world(tmp_path, extra_cases=[('P1', bad_lateral), ('P2', bad_vertical)])[1])
    for product in ('P1', 'P2'):                   # the union is checked on every product of the group
        assert ('group_case_layer_mismatch', product, bad_lateral) in found
        assert ('group_case_vertical_same_layer', product, bad_vertical) in found


def test_density_layer_missing(tmp_path):
    def mutate(doc, base):
        doc['products']['P2']['condition_assignment']['layer_stack'] = [
            STACK[0], dict(STACK[1], density_process_layer='NOPE')]
    found = _violations(_world(tmp_path, mutate, extra_cases=[('P1', VER_AB)])[1])
    assert found == {('group_case_density_layer_missing', 'P2', VER_AB)}


def test_pair_unmapped(tmp_path):
    unmapped = 'lateral_short-A-L1-D-L1'
    found = _violations(_world(tmp_path, extra_cases=[('P2', unmapped)])[1])
    assert found == {('group_case_pair_unmapped', 'P1', unmapped), ('group_case_pair_unmapped', 'P2', unmapped)}


def test_cno_layer_unassigned_only_for_include_cno_products(tmp_path):
    def mutate(doc, base):
        doc['products']['P1']['include_cno'] = True
        _cno(base, 'P1', 'lateralopen-A-L9')
        _cno(base, 'P2', 'lateralopen-A-L8')       # P2 does not include CNO: not checked
    found = _violations(_world(tmp_path, mutate)[1])
    assert found == {('group_cno_layer_unassigned', 'P1', 'lateralopen-A-L9')}


def test_left_out_cases_are_not_checked(tmp_path):
    # LEFT_OUT cases sit in P1 only; P2 has neither their layers (SD2) nor pairs for them, and nothing is reported
    path = _world(tmp_path)[1]
    assert pc.run_products_config(path, check=True)['status'] == 'checked'


# ---- R3 follow-ups (pasx/T3b) -----------------------------------------------------------------------------------------

AA_L1, AA_L2 = 'lateral_short-A-L1-A-L1', 'lateral_short-A-L2-A-L2'


def test_same_name_case_skips_pair_check_but_keeps_layer_and_density_checks(tmp_path):
    # R3:1: an A/A case cannot be in pairs.csv; the group passes. (Before T3b: group_case_pair_unmapped.)
    path = _world(tmp_path / 'ok', extra_cases=[('P1', AA_L1)])[1]
    assert pc.run_products_config(path, check=True)['status'] == 'checked'
    # layer not in the stack of P2 -> still refused
    def one_layer(doc, base):
        doc['products']['P2']['condition_assignment']['layer_stack'] = STACK[:1]
    found = _violations(_world(tmp_path / 'layer', one_layer, extra_cases=[('P1', AA_L2)])[1])
    assert found == {('group_case_layer_unassigned', 'P2', AA_L2)}
    # empty density layer -> still refused
    def empty_density(doc, base):
        for product in ('P1', 'P2'):
            doc['products'][product]['condition_assignment']['layer_stack'] = [
                STACK[0], dict(STACK[1], density_process_layer='')]
    found = _violations(_world(tmp_path / 'dens', empty_density, extra_cases=[('P1', AA_L2)])[1])
    assert found == {('group_case_density_layer_missing', 'P1', AA_L2), ('group_case_density_layer_missing', 'P2', AA_L2)}
    # direction is still checked
    found = _violations(_world(tmp_path / 'dir', extra_cases=[('P1', 'lateral_short-A-L1-A-L2')])[1])
    assert ('group_case_layer_mismatch', 'P1', 'lateral_short-A-L1-A-L2') in found


def test_empty_density_layer_is_reported_together_with_an_unmapped_pair(tmp_path):
    # R3b: the empty-density check no longer sits behind the pair check: both violations come out in one run
    unmapped = 'lateral_short-A-L2-D-L2'

    def blank_l2(doc, base):
        for product in ('P1', 'P2'):
            doc['products'][product]['condition_assignment']['layer_stack'] = [
                STACK[0], dict(STACK[1], density_process_layer='')]
    found = _violations(_world(tmp_path, blank_l2, extra_cases=[('P1', unmapped)])[1])
    assert found == {('group_case_density_layer_missing', 'P1', unmapped), ('group_case_pair_unmapped', 'P1', unmapped),
                     ('group_case_density_layer_missing', 'P2', unmapped), ('group_case_pair_unmapped', 'P2', unmapped)}


VER_AA = 'vertical_short-A-L1-A-L2'


def test_vertical_same_electrode_case_skips_the_pair_check_and_uses_the_top_layer(tmp_path):
    # R3b: vertical A/A: no pairs.csv row needed; its density layer is the one of the higher stack_order (L2)
    path = _world(tmp_path / 'ok', extra_cases=[('P1', VER_AA)])[1]
    assert pc.run_products_config(path, check=True)['status'] == 'checked'

    def blank(layer_index):
        def mutate(doc, base):
            for product in ('P1', 'P2'):
                stack = copy.deepcopy(STACK)
                stack[layer_index]['density_process_layer'] = ''
                doc['products'][product]['condition_assignment']['layer_stack'] = stack
        return mutate
    found = _violations(_world(tmp_path / 'top', blank(1), extra_cases=[('P1', VER_AA)])[1])
    assert found == {('group_case_density_layer_missing', 'P1', VER_AA), ('group_case_density_layer_missing', 'P2', VER_AA)}
    # a blank lower layer (L1) does not matter for this case
    path = _world(tmp_path / 'low', blank(0), extra_cases=[('P1', VER_AA)])[1]
    assert not any(v for v in _violations_or_none(path) if v[2] == VER_AA)
    # the case in its own root is checked against the distance table (a table without L1L2 refuses it)

    def no_pair_in_table(doc, base):
        (base / 'data/input/thk.txt').write_text('layers\tdistance\nL2L3\t3\n', encoding='utf-8')
    found = _violations(_world(tmp_path / 'dist', no_pair_in_table, extra_cases=[('P1', VER_AA)])[1])
    assert ('group_distance_pair_missing', 'P1', VER_AA) in found
    assert ('group_distance_pair_missing', 'P2', VER_AA) not in found       # P2 only has it through the union


def _violations_or_none(path):
    try:
        pc.run_products_config(path, check=True)
    except pc.GroupCompatibilityError as exc:
        return {(v['code'], v['product'], v['case']) for v in exc.violations}
    return set()


def _three_layers(doc, base):
    stack = STACK + [{'actual_layer': 'L3', 'stack_order': 3, 'density_process_layer': 'P3'}]
    for product in ('P1', 'P2'):
        doc['products'][product]['condition_assignment']['layer_stack'] = copy.deepcopy(stack)
    for kind in ('line', 'pixel'):
        (base / 'data/input/density' / ('mobile_%s.csv' % kind)).write_text(DENSITY + 'P3,0.1,0.2\n', encoding='utf-8')


def test_distance_pair_missing_only_for_own_root_cases(tmp_path):
    case = 'vertical_short-A-L1-B-L3'            # the distance table has L1L2 only
    found = _violations(_world(tmp_path / 'p1', _three_layers, extra_cases=[('P1', case)])[1])
    assert found == {('group_distance_pair_missing', 'P1', case)}       # P2 only has it through the union: allowed
    found = _violations(_world(tmp_path / 'p2', _three_layers, extra_cases=[('P2', case)])[1])
    assert found == {('group_distance_pair_missing', 'P2', case)}
    assert pc.run_products_config(_world(tmp_path / 'ok', _three_layers, extra_cases=[('P1', VER_AB)])[1],
                                  check=True)['status'] == 'checked'


def test_cno_density_layer_missing(tmp_path):
    def mutate(doc, base):
        doc['products']['P1']['include_cno'] = True
        doc['products']['P1']['condition_assignment']['layer_stack'] = [
            STACK[0], dict(STACK[1], density_process_layer='')]
        _cno(base, 'P1', 'lateralopen-A-L2')
    found = _violations(_world(tmp_path, mutate)[1])
    assert found == {('group_cno_density_layer_missing', 'P1', 'lateralopen-A-L2')}


@pytest.mark.parametrize('name', ['CON\n', 'P1\n', 'P1\r', ' P1'])
def test_trailing_newline_names_refused(name):
    for kind in ('product', 'group'):
        with pytest.raises(NumericContractError) as info:
            pc.check_name(name, kind)
        assert info.value.code == 'invalid_%s_name' % kind


@pytest.mark.parametrize('value, code', [('/abs/path', 'absolute_or_nonportable_path'),
                                         ('data\\out', 'absolute_or_nonportable_path'),
                                         ('C:/abs', 'absolute_or_nonportable_path'),
                                         ('../outside', 'relative_path_escape')])
def test_bad_data_dir_refused(tmp_path, value, code):
    path = _world(tmp_path)[1]
    assert _error(path, check=True, data_dirs={'P1': value}).code == code
    assert _error(path, pairs=[('P1', 'P2')], data_dirs={'P1': 'ok', 'P2': value}).code == code
    assert not (path.parent / 'data/products/_groups').exists()


def test_overlap_with_output_root_ignores_case(tmp_path):
    path = _world(tmp_path, lambda d, b: d['products']['P1'].update(hotspot_root='Data/Products/P1/hotspots'))[1]
    assert _error(path, check=True).code == 'input_path_overlaps_output'


def test_check_writes_nothing(tmp_path):
    base, path = _world(tmp_path)
    before = sorted(p.relative_to(base).as_posix() for p in base.rglob('*'))
    pc.run_products_config(path, check=True, pairs=[('P1', 'P2')], data_dirs={'P1': 'a', 'P2': 'b'})
    assert sorted(p.relative_to(base).as_posix() for p in base.rglob('*')) == before


# ---- generated files ------------------------------------------------------------------------------------------------

def _hand_numeric(base, first, second):
    """The two-sided real.yaml written by hand the way the template does (no asset keys, product mode)."""
    def side(name):
        return {'product_id': name, 'data_dir': 'data/imported/%s' % name,
                'condition_assignment': {
                    'product_id': name, 'design_version': 'UNSPECIFIED', 'source_id': '%s_hotspot' % name,
                    'process_step_id': 'UNSPECIFIED', 'exposure_id': 'UNSPECIFIED', 'unit_id': 'UNSPECIFIED',
                    'layer_stack': copy.deepcopy(STACK), 'pair_table_path': 'data/input/pairs.csv',
                    'interlayer_distance_path': 'data/input/thk.txt', 'electrode_role_policy': 'electrode_id'}}
    doc = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'outputs/numeric_real',
           'reference': side(first), 'target': side(second),
           'map': {'input': side(first), 'diameter_um': 5},
           'review_rois': {'map_region_ids': [], 'manual': []}}
    (base / 'real.yaml').write_text(yaml.safe_dump(doc, sort_keys=False), encoding='utf-8')
    return base / 'real.yaml'


def _hand_compare2(base, doc, first, second):
    products = doc['products']
    out = {'schema_version': 'compare2_config_v1', 'numeric_config': 'real.yaml', 'output_dir': 'data/compare2',
           'density_dir': 'data/input/density', 'model_groups': doc['common']['model_groups'],
           'products': {'reference': {k: v for k, v in products[first].items()
                                      if k in ('model_group', 'resolution', 'hotspot_root', 'unit_size_um', 'dl1_model')},
                        'target': {k: v for k, v in products[second].items()
                                   if k in ('model_group', 'resolution', 'hotspot_root', 'unit_size_um', 'dl1_model')}},
           'report': doc['common']['report']}
    (base / 'compare2.yaml').write_text(yaml.safe_dump(out, sort_keys=False), encoding='utf-8')
    return base / 'compare2.yaml'


# The fields that differ by design between a generated pair setting and the hand-written template one: the paths of the
# output and of the numeric config, the file identity (path, sha256). Everything else must be equal after loading.
NUMERIC_IGNORED = {'output_dir', '_config_path', '_config_sha256', '_config_dir'}
COMPARE2_IGNORED = {'output_dir', 'numeric_config', 'config_path', 'config_sha256'}


def _numeric_view(cfg):
    view = {k: v for k, v in cfg.items() if k not in NUMERIC_IGNORED}
    view = json.loads(json.dumps(view, sort_keys=True, default=str))
    return view


def _compare2_view(cfg):
    return {k: v for k, v in dataclasses.asdict(cfg).items() if k not in COMPARE2_IGNORED}


def test_pair_settings_load_like_the_hand_written_ones(tmp_path):
    base, path = _world(tmp_path)
    doc = yaml.safe_load(path.read_text(encoding='utf-8'))
    dirs = {'P1': 'data/imported/P1', 'P2': 'data/imported/P2'}
    summary = pc.run_products_config(path, pairs=[('P1', 'P2')], data_dirs=dirs)
    written = {item['path'] for item in summary['files']}
    assert {'gen.pair.P1__P2.real.yaml', 'gen.pair.P1__P2.compare2.yaml', 'gen.product.P1.real.yaml',
            'gen.product.P2.real.yaml'} <= written
    for name in ('gen.pair.P1__P2.real.yaml', 'gen.pair.P1__P2.compare2.yaml', 'gen.product.P1.real.yaml'):
        assert (base / name).read_text(encoding='utf-8').startswith(pc.GEN_MARKER + '\n')
    generated = load_numeric_config(base / 'gen.pair.P1__P2.real.yaml')
    by_hand = load_numeric_config(_hand_numeric(base, 'P1', 'P2'))
    assert _numeric_view(generated) == _numeric_view(by_hand)
    assert generated['output_dir'] == 'data/compare/P1__P2/numeric'
    assert 'asset_manifest' not in generated['reference'] and 'electrode_import_record' not in generated['target']
    gen2 = load_compare2_config(base / 'gen.pair.P1__P2.compare2.yaml')
    hand2 = load_compare2_config(_hand_compare2(base, doc, 'P1', 'P2'))
    assert _compare2_view(gen2) == _compare2_view(hand2)
    assert gen2.output_dir == base / 'data/compare/P1__P2/compare2'
    assert gen2.numeric_config == base / 'gen.pair.P1__P2.real.yaml'


def test_product_setting_uses_the_same_section_for_both_sides(tmp_path):
    base, path = _world(tmp_path)
    pc.run_products_config(path, data_dirs={'P1': 'data/products/P1/hotspots/g0001-abcd1234'})
    cfg = load_numeric_config(base / 'gen.product.P1.real.yaml')
    assert cfg['reference'] == cfg['target'] and cfg['map']['input'] == cfg['reference']
    assert cfg['reference']['data_dir'] == 'data/products/P1/hotspots/g0001-abcd1234'
    assert cfg['reference']['condition_assignment']['product_id'] == 'P1'


def test_regeneration_is_byte_identical_and_does_not_touch_files(tmp_path):
    base, path = _world(tmp_path)
    args = dict(pairs=[('P1', 'P2')], data_dirs={'P1': 'a', 'P2': 'b'})
    first = pc.run_products_config(path, **args)
    assert all(item['written'] for item in first['files'])
    files = [base / item['path'] for item in first['files']]
    state = {f: (f.read_bytes(), f.stat().st_mtime_ns) for f in files}
    second = pc.run_products_config(path, **args)
    assert not any(item['written'] for item in second['files'])
    assert {f: (f.read_bytes(), f.stat().st_mtime_ns) for f in files} == state
    assert not list(base.rglob('*.tmp-*'))


def test_cross_group_and_bad_pairs_refused(tmp_path):
    base, path = _world(tmp_path)
    dirs = {'P1': 'a', 'P3': 'c', 'P2': 'b'}
    assert _error(path, pairs=[('P1', 'P3')], data_dirs=dirs).code == 'compare_cross_group'
    assert _error(path, pairs=[('P1', 'P1')], data_dirs=dirs).code == 'pair_same_product'
    assert _error(path, pairs=[('P1', 'NOPE')], data_dirs=dirs).code == 'pair_unknown_product'
    assert _error(path, pairs=[('P1', 'P2')], data_dirs={'P1': 'a'}).code == 'data_dir_missing'
    assert not (base / 'data/products/_groups').exists()


def test_expected_cases_file_is_accepted_by_import_hotspots(tmp_path):
    from simcompare.adapters.real_hotspot import import_hotspots, load_real_manifest
    from test_real_hotspot import _source
    base, path = _world(tmp_path / 'w')
    pc.run_products_config(path)
    expected = base / 'data/products/_groups/G/expected_cases.txt'
    root = tmp_path / 'root'
    _source(root, LAT_AB, shape=(1002, 1002))
    out = tmp_path / 'imported'
    import_hotspots(root, out, expected_cases=expected)
    manifest = load_real_manifest(out, 'manifest.json')
    from simcompare.adapters.real_hotspot import is_excluded_case_name
    wanted = [line for line in expected.read_text(encoding='utf-8').splitlines()
              if not line.startswith('#') and not is_excluded_case_name(line)]     # the importer drops SD2/SD3/PXL verticals
    assert manifest['expected_cases'] == wanted and len(wanted) > 1


# ---- CLI ------------------------------------------------------------------------------------------------------------

def test_cli_check_and_pair(tmp_path, capsys):
    base, path = _world(tmp_path)
    assert cli.main(['products-config', '--config', str(path), '--check']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'checked'
    assert not (base / 'data/products').exists()
    assert cli.main(['products-config', '--config', str(path), '--pair', 'P1', 'P2',
                     '--data-dir', 'P1=a', '--data-dir', 'P2=b']) == 0
    assert (base / 'gen.pair.P1__P2.compare2.yaml').is_file()
    capsys.readouterr()


def test_cli_incompatible_group_exits_2_with_codes(tmp_path, capsys):
    base, path = _world(tmp_path, extra_cases=[('P2', 'lateral_short-A-L1-D-L1')])
    assert cli.main(['products-config', '--config', str(path), '--check']) == 2
    captured = capsys.readouterr()
    assert 'group_case_pair_unmapped' in captured.err
    assert {v['product'] for v in json.loads(captured.out)['violations']} == {'P1', 'P2'}
    assert cli.main(['products-config', '--config', str(path), '--pair', 'P1', 'P3']) == 2
    assert 'compare_cross_group' in capsys.readouterr().err
