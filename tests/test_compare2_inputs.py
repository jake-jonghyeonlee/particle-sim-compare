import hashlib
import math
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simcompare.compare2_inputs import (CNO_FACTOR, CRP_DEFAULT_OTHER, GroupConfig, ProductConfig, cno_lambda_factor,
                                        collect_cno, crp_for_group, lambda_factor, load_compare2_config,
                                        load_density)
from simcompare.numeric_schema import NumericContractError

EXAMPLE = Path(__file__).resolve().parents[1] / 'configs/etx/compare2.example.yaml'


def _config(tmp_path, mutate=None):
    cfg = yaml.safe_load(EXAMPLE.read_text(encoding='utf-8'))
    if mutate:
        mutate(cfg)
    path = tmp_path / 'compare2.yaml'
    path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    return path


def _code(info):
    return info.value.code


def test_example_config_loads(tmp_path):
    cfg = load_compare2_config(EXAMPLE)
    base = EXAMPLE.parent
    assert cfg.numeric_config == base / 'real.yaml' and cfg.density_dir == base / 'data/density'
    assert cfg.reference.hotspot_root == base / 'data/input/hotspot/QA4_R00'
    assert cfg.target.hotspot_root == base / 'data/input/hotspot/QA4_T00'
    assert (cfg.reference.dl1_model, cfg.target.dl1_model) == ('QA4_R00', 'QA4_T00')
    assert cfg.report.dl1_root == base / 'data/input/dl1' and cfg.output_dir == base / 'data/compare2'
    assert cfg.reference.resolution == (1500, 1850) and cfg.reference.unit_size_um == (45.0, 45.0)
    assert cfg.target.unit_size_um is None
    assert (cfg.report.map_window_um, cfg.report.diameter_um, cfg.report.top_n) == (30.0, 3.0, 4)
    assert cfg.model_groups['mobile'].glass_area_um2 is None
    assert cfg.model_groups['mobile'].glass_area_reason == 'glass_area_pending'
    assert cfg.config_sha256 == hashlib.sha256(EXAMPLE.read_bytes()).hexdigest()


def test_dl1_model_is_an_optional_product_key(tmp_path):
    cfg = load_compare2_config(_config(tmp_path, lambda c: [c['products']['reference'].pop('dl1_model', None),
                                                            c['products']['target'].update(dl1_model='ModelB')]))
    assert cfg.target.dl1_model == 'ModelB' and cfg.reference.dl1_model is None
    with pytest.raises(NumericContractError) as info:
        load_compare2_config(_config(tmp_path, lambda c: c['products']['target'].update(dl1_model='')))
    assert _code(info) == 'invalid_compare2_config'


def test_report_section_is_optional_with_defaults(tmp_path):
    cfg = load_compare2_config(_config(tmp_path, lambda c: c.pop('report')))
    assert cfg.report.dl1_root is None and cfg.report.top_n == 4 and cfg.report.map_window_um == 30.0


@pytest.mark.parametrize('mutate', [
    lambda c: c.update(extra=1),
    lambda c: c['products']['reference'].update(crp=1.0),
    lambda c: c['model_groups']['mobile'].update(crp=1.0),
    lambda c: c['report'].update(bogus=1),
    lambda c: c['model_groups']['mobile']['glass_area'].update(extra=1),
])
def test_unknown_keys_are_rejected(tmp_path, mutate):
    with pytest.raises(NumericContractError) as info:
        load_compare2_config(_config(tmp_path, mutate))
    assert _code(info) == 'invalid_compare2_config'


def test_lambda_diameters_is_optional_and_normalised(tmp_path):
    assert load_compare2_config(_config(tmp_path)).lambda_diameters_um is None
    assert load_compare2_config(_config(tmp_path, lambda c: c.update(lambda_diameters_um=None))).lambda_diameters_um is None
    cfg = load_compare2_config(_config(tmp_path, lambda c: c.update(lambda_diameters_um=[5, 3.0, 1])))
    assert cfg.lambda_diameters_um == (Decimal(1), Decimal(3), Decimal(5))


@pytest.mark.parametrize('value', [[0.5], [3, 3.0], ['3'], [], 3, [True], [-1], [0], [float('nan')], [float('inf')],
                                   [31], [3, None]])
def test_invalid_lambda_diameters(tmp_path, value):
    with pytest.raises(NumericContractError) as info:
        load_compare2_config(_config(tmp_path, lambda c: c.update(lambda_diameters_um=value)))
    assert _code(info) == 'invalid_compare2_config'


@pytest.mark.parametrize('mutate', [
    lambda c: c.pop('density_dir'),
    lambda c: c['products']['target'].pop('hotspot_root'),
    lambda c: c['products']['target'].update(model_group='absent'),
    lambda c: c['products']['target'].update(resolution=[1500, 0]),
    lambda c: c['products']['target'].update(resolution=[1500.5, 1850]),
    lambda c: c['products']['target'].update(unit_size_um=[45, -1]),
    lambda c: c['model_groups']['mobile'].update(weight_line=0),
    lambda c: c['model_groups']['mobile'].update(weight_pixel=float('inf')),
    lambda c: c['model_groups']['mobile'].pop('weight_pixel'),
    lambda c: c['report'].update(top_n=0),
])
def test_invalid_config_values(tmp_path, mutate):
    with pytest.raises(NumericContractError) as info:
        load_compare2_config(_config(tmp_path, mutate))
    assert _code(info) == 'invalid_compare2_config'


def test_unsupported_schema_version(tmp_path):
    with pytest.raises(NumericContractError) as info:
        load_compare2_config(_config(tmp_path, lambda c: c.update(schema_version='other')))
    assert _code(info) == 'unsupported_compare2_config'


def test_glass_area_key_missing_is_error(tmp_path):
    with pytest.raises(NumericContractError) as info:
        load_compare2_config(_config(tmp_path, lambda c: c['model_groups']['mobile'].pop('glass_area')))
    assert _code(info) == 'invalid_compare2_config'


@pytest.mark.parametrize('value', [0, -5, float('inf'), float('nan'), True, '10'])
def test_glass_area_bad_values(tmp_path, value):
    def mutate(cfg):
        cfg['model_groups']['mobile']['glass_area'] = {'value': value, 'unit': 'mm2'}
    with pytest.raises(NumericContractError) as info:
        load_compare2_config(_config(tmp_path, mutate))
    assert _code(info) == 'invalid_glass_area'


def test_glass_area_bad_unit(tmp_path):
    def mutate(cfg):
        cfg['model_groups']['mobile']['glass_area'] = {'value': 5, 'unit': 'cm2'}
    with pytest.raises(NumericContractError) as info:
        load_compare2_config(_config(tmp_path, mutate))
    assert _code(info) == 'invalid_glass_area'


@pytest.mark.parametrize('unit,value,expected', [('mm2', 2.0, 2e6), ('um2', 7.5, 7.5), ('m2', 0.5, 5e11)])
def test_glass_area_unit_conversion(tmp_path, unit, value, expected):
    def mutate(cfg):
        cfg['model_groups']['mobile']['glass_area'] = {'value': value, 'unit': unit}
    group = load_compare2_config(_config(tmp_path, mutate)).model_groups['mobile']
    assert group.glass_area_um2 == pytest.approx(expected, rel=1e-12) and group.glass_area_reason is None


def test_crp_by_group_name():
    assert crp_for_group('mobile') == 1.0 and crp_for_group('Watch') == 1.0 and crp_for_group('MOBILE') == 1.0
    assert crp_for_group('auto') == CRP_DEFAULT_OTHER == 2.92 and crp_for_group('foo') == 2.92
    assert GroupConfig('Watch', None, 1.0, 1.0).crp == 1.0


def _glass_config(tmp_path, weight_pixel=0.8):
    def mutate(cfg):
        cfg['model_groups']['mobile'].update(glass_area={'value': 1500 * 1850, 'unit': 'mm2'},
                                             weight_line=0.5, weight_pixel=weight_pixel)
    return load_compare2_config(_config(tmp_path, mutate))


def test_lambda_factor_hand_calculation(tmp_path):
    cfg = _glass_config(tmp_path)
    group = cfg.model_groups['mobile']
    assert group.glass_area_um2 == pytest.approx(2.775e12, rel=1e-12)
    expected_pixel = 1500 * 1850 * 0.8 / (4 * 2.775e12)
    assert lambda_factor(cfg.reference, group, 'pixel') == pytest.approx(expected_pixel, rel=1e-12, abs=0)
    assert lambda_factor(cfg.reference, group, 'line') == pytest.approx(1500 * 1850 * 0.5 / (4 * 2.775e12),
                                                                        rel=1e-12, abs=0)
    assert cno_lambda_factor(cfg.reference, group) == pytest.approx(expected_pixel * math.pi / 4, rel=1e-12, abs=0)
    assert CNO_FACTOR == math.pi / 4


def test_lambda_factor_none_while_glass_pending():
    cfg = load_compare2_config(EXAMPLE)
    group = cfg.model_groups['mobile']
    assert lambda_factor(cfg.reference, group, 'line') is None
    assert cno_lambda_factor(cfg.reference, group) is None
    with pytest.raises(NumericContractError):
        lambda_factor(cfg.reference, group, 'cno')


# ---- density CSV ------------------------------------------------------------------------------

def _density(tmp_path, line, pixel='layer,1,2\nA,1,2\n'):
    folder = tmp_path / 'density'
    folder.mkdir(exist_ok=True)
    (folder / 'mobile_line.csv').write_text(line, encoding='utf-8-sig')
    (folder / 'mobile_pixel.csv').write_text(pixel, encoding='utf-8')
    return folder


def test_density_loads_and_normalizes_headers(tmp_path):
    folder = _density(tmp_path, 'name,1um,2 µm,3.0,4 μm\nGAT, 0.5 ,1,2,3\n M1 ,0,0.25,,7\n')
    tables = load_density(folder, 'mobile')
    line = tables['line']
    assert line.sizes == (Decimal(1), Decimal(2), Decimal(3), Decimal(4))
    assert line.layers == ('GAT', 'M1')
    assert line.get('GAT', 1) == 0.5 and line.get('GAT', 3) == 2.0 and line.get('GAT', 3.0) == 2.0
    assert line.get('M1', 2) == 0.25 and line.get('M1', 4) == 7.0
    assert line.sha256 == hashlib.sha256((folder / 'mobile_line.csv').read_bytes()).hexdigest()
    assert tables['pixel'].get('A', 2) == 2.0


def test_density_missing_reasons(tmp_path):
    line = load_density(_density(tmp_path, 'n,1,2\nGAT,1,\n'), 'mobile')['line']
    assert line.lookup('GAT', 1) == (1.0, None)
    assert line.lookup('GAT', 2) == (None, 'density_value_missing') and line.get('GAT', 2) is None
    assert line.lookup('GAT', 5) == (None, 'density_size_missing')
    assert line.lookup('nope', 1) == (None, 'density_layer_missing')
    assert line.lookup('gat', 1) == (None, 'density_layer_missing')  # layer names keep their case


@pytest.mark.parametrize('text,code', [
    ('n,1,1.0\nA,1,2\n', 'duplicate_density_size'),
    ('n,1um,1 µm\nA,1,2\n', 'duplicate_density_size'),
    ('n,1,2\nA,1,2\nA,3,4\n', 'duplicate_density_layer'),
    ('n,1,2\nA,-1,2\n', 'invalid_density_value'),
    ('n,1,2\nA,abc,2\n', 'invalid_density_value'),
    ('n,1,2\nA,inf,2\n', 'invalid_density_value'),
    ('n,1,2\nA,1\n', 'invalid_density_csv'),
    ('n,1,2\nA,1,2,3\n', 'invalid_density_csv'),
    ('n,1,x\nA,1,2\n', 'invalid_density_csv'),
    ('n,0,2\nA,1,2\n', 'invalid_density_csv'),
    ('n\nA\n', 'invalid_density_csv'),
    ('n,1,2\n,1,2\n', 'invalid_density_csv'),
])
def test_density_errors(tmp_path, text, code):
    with pytest.raises(NumericContractError) as info:
        load_density(_density(tmp_path, text), 'mobile')
    assert _code(info) == code


def test_density_file_missing(tmp_path):
    folder = _density(tmp_path, 'n,1\nA,1\n')
    (folder / 'mobile_pixel.csv').unlink()
    with pytest.raises(NumericContractError) as info:
        load_density(folder, 'mobile')
    assert _code(info) == 'density_file_missing'
    with pytest.raises(NumericContractError) as info:
        load_density(folder, 'other')
    assert _code(info) == 'density_file_missing'


# ---- CNO collection ---------------------------------------------------------------------------

def _cno_folder(root, name, text='defect_size(um),Area(um2)\n1,0.5\n3,1.5\n'):
    folder = Path(root) / name / 'result'
    folder.mkdir(parents=True)
    if text is not None:
        (folder / 'lateral_open.csv').write_text(text, encoding='utf-8')


def test_collect_cno_mixed_root(tmp_path):
    root = tmp_path / 'hot'
    (root / 'lateral_short-A-L1-B-L1/result').mkdir(parents=True)  # a normal case folder is not a CNO
    _cno_folder(root, 'lateralopen-CNO2-gcnt')
    _cno_folder(root, 'lateralopen-CNO1-gcnt', text=None)
    _cno_folder(root / 'sub', 'lateralopen-CNO3-gcnt', text='wrong,header\n1,2\n')
    cases = collect_cno(root)
    assert [c.case_name for c in cases] == ['lateralopen-CNO1-gcnt', 'lateralopen-CNO2-gcnt', 'lateralopen-CNO3-gcnt']
    missing, observed, invalid = cases
    assert (missing.status, missing.reason, missing.rows, missing.csv_sha256) == ('missing', 'case_local_csv_missing', {}, None)
    assert missing.electrode_id == 'CNO1' and missing.density_layer == 'gcnt'
    assert observed.status == 'observed' and observed.rows == {1.0: 0.5, 3.0: 1.5}
    assert observed.csv_relative_path == 'lateralopen-CNO2-gcnt/result/lateral_open.csv'
    assert observed.source_relative_path == 'lateralopen-CNO2-gcnt'
    assert observed.csv_sha256 == hashlib.sha256(
        (root / 'lateralopen-CNO2-gcnt/result/lateral_open.csv').read_bytes()).hexdigest()
    assert invalid.status == 'invalid' and invalid.reason == 'invalid_real_csv_header' and invalid.rows == {}
    assert invalid.source_relative_path == 'sub/lateralopen-CNO3-gcnt'


def test_collect_cno_empty_root(tmp_path):
    (tmp_path / 'hot').mkdir()
    assert collect_cno(tmp_path / 'hot') == []


# ---- compare2/T3 R2 follow-ups ----------------------------------------------------------------

def test_density_accepts_decimal_sizes_and_keeps_canonical_keys(tmp_path):
    line = load_density(_density(tmp_path, 'n,1um,2.5 µm,30.0,50\nGAT,1,2,3,4\n'), 'mobile')['line']
    assert line.sizes == (Decimal('1'), Decimal('2.5'), Decimal('30'), Decimal('50'))
    assert [str(size) for size in line.sizes] == ['1', '2.5', '30', '50']  # no '1.0' / '3E+1' spellings
    for size in line.sizes:  # a caller may iterate table.sizes
        assert line.lookup('GAT', size)[1] is None
    assert line.get('GAT', Decimal('30.0')) == 3.0 and line.get('GAT', Decimal('2.50')) == 2.0
    assert line.lookup('GAT', Decimal('4')) == (None, 'density_size_missing')


def test_lambda_factor_documents_ca_usage():
    doc = lambda_factor.__doc__
    assert 'CA(um^2' in doc and 'never divide CA' in doc


def test_density_decode_error_hints_utf8(tmp_path):
    folder = _density(tmp_path, 'n,1\nA,1\n')
    (folder / 'mobile_line.csv').write_bytes('n,1\n전극,1\n'.encode('cp949'))
    with pytest.raises(NumericContractError) as info:
        load_density(folder, 'mobile')
    assert _code(info) == 'invalid_density_csv' and 'UTF-8' in str(info.value)


def test_collect_cno_invalid_keeps_detail_and_csv_path(tmp_path):
    root = tmp_path / 'hot'
    _cno_folder(root, 'lateralopen-CNO3-gcnt', text='wrong,header\n1,2\n')
    (case,) = collect_cno(root)
    assert case.status == 'invalid' and case.reason == 'invalid_real_csv_header'
    assert case.csv_relative_path == 'lateralopen-CNO3-gcnt/result/lateral_open.csv'
    assert 'lateral_open.csv' in case.detail
    ok_root = tmp_path / 'ok'
    _cno_folder(ok_root, 'lateralopen-CNO1-gcnt')
    assert collect_cno(ok_root)[0].detail is None


def test_density_file_is_found_ignoring_case_when_unique(tmp_path):
    folder = tmp_path / 'density'
    folder.mkdir()
    (folder / 'Mobile_LINE.csv').write_text('layer,1\nA,1\n', encoding='utf-8')
    (folder / 'MOBILE_pixel.csv').write_text('layer,1\nA,2\n', encoding='utf-8')
    tables = load_density(folder, 'mobile')
    assert tables['line'].path.name == 'Mobile_LINE.csv' and tables['pixel'].path.name == 'MOBILE_pixel.csv'
    assert tables['line'].get('A', 1) == 1.0


def test_density_file_exact_name_wins_over_case_variants(tmp_path):
    folder = _density(tmp_path, 'layer,1\nA,1\n', pixel='layer,1\nA,2\n')
    assert load_density(folder, 'mobile')['line'].path.name == 'mobile_line.csv'


def test_density_file_with_several_case_variants_is_ambiguous(tmp_path, monkeypatch):
    # a case-insensitive file system cannot hold two names, so the listing is faked
    names = [tmp_path / 'MOBILE_line.csv', tmp_path / 'Mobile_Line.csv']
    monkeypatch.setattr(Path, 'iterdir', lambda self: iter(names))
    monkeypatch.setattr(Path, 'is_file', lambda self: self in names)
    with pytest.raises(NumericContractError) as info:
        load_density(tmp_path, 'mobile')
    assert _code(info) == 'ambiguous_density_file'
