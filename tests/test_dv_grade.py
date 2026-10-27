import csv
import hashlib
import json
import os
from decimal import Decimal as D
from pathlib import Path

import pytest

from simcompare import dv_grade
from simcompare.cli import main

OUT_FILES = ('dv_grades.csv', 'dv_grades_by_case.csv', 'dv_pair_timings.csv')


def _r(low, high):
    return (D(low), D(high))


def _header(n):
    cols = ['electrode_id', 'impedance', 'layers']
    for i in range(1, n + 1):
        cols += [f'T{i}_min', f'T{i}_max']
    return cols


def _write_sheet(path, rows, n=2, header=None, raw=None):
    if raw is not None:
        Path(path).write_bytes(raw)
        return path
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(header or _header(n))
        writer.writerows(rows)
    return path


def _row(electrode, impedance, *values, layers='M1'):
    """values: (min, max) 쌍을 타이밍 순서로."""
    flat = []
    for low, high in values:
        flat += [low, high]
    return [electrode, impedance, layers] + flat


def _case(root, name, images=False):
    folder = Path(root) / name
    if images:
        folder = folder / 'images'
    folder.mkdir(parents=True)
    (folder / '1.png').write_bytes(b'')


def _tree(tmp_path):
    root = tmp_path / 'product'
    for name in ('lateral_short-A-M1-B-M1', 'vertical_short-B-M2-A-M1', 'lateral_short-A-M1-C-M1',
                 'lateral_open-D-M1-E-M1', 'lateral_short-F-M1-F-M2', 'lateral_open-G-M1-G-M1'):
        _case(root, name)
    return root


def _sheet(tmp_path, extra=(), name='sheet.csv'):
    rows = [_row('A', 'low', ('0', '0'), ('0', '0')),
            _row('B', 'high', ('1', '2'), ('0', '0'), layers='M1;M2'),
            _row('C', 'low', ('0', '1'), ('0', '1')),
            _row('Z', 'low', ('', ''), ('', ''))] + list(extra)
    return _write_sheet(tmp_path / name, rows)


def _run(*args):
    return main(['dv-grade', *map(str, args)])


def _grade(tmp_path, out='out', threshold=None, sheet=None, root=None, product='P1'):
    args = ['--sheet', sheet or _sheet(tmp_path), '--root', root or _tree(tmp_path), '--product-id', product,
            '--out', tmp_path / out]
    if threshold is not None:
        args += ['--low-low-threshold-v', threshold]
    return _run(*args)


def _rows(path):
    with open(path, encoding='utf-8-sig', newline='') as handle:
        return list(csv.reader(handle))


def _dicts(path):
    rows = _rows(path)
    return [dict(zip(rows[0], row)) for row in rows[1:]]


def _manifest(out):
    return json.loads((Path(out) / 'dv_grade_manifest.json').read_text(encoding='utf-8'))


# ---- 순수 계산 -------------------------------------------------------------------------------

def test_dv_takes_the_larger_direction():
    # a [1,3], b [2,4]: max(3-2, 4-1) = 3 (L2)
    result = dv_grade.grade_pair([_r('1', '3')], [_r('2', '4')], 'low', 'low', None)
    assert result['dv_v'] == D('3')
    # 반대 방향이 큰 경우도 같은 값
    assert dv_grade.grade_pair([_r('2', '4')], [_r('1', '3')], 'low', 'low', None)['dv_v'] == D('3')


def test_same_point_value_is_zero_and_grade_none():
    result = dv_grade.grade_pair([_r('4.5', '4.5'), _r('1', '1')], [_r('4.5', '4.5'), _r('1.0', '1.0')],
                                 'low', 'high', D('1'))
    assert result['dv_v'] == 0 and result['grade'] == 'None' and result['timings_tied'] == []


def test_same_nondegenerate_range_is_width_not_zero():
    # L1: 범위 안에서 독립으로 움직인다고 보는 최악 가정이라 같은 범위 [1,3],[1,3] 은 ΔV = 2
    assert dv_grade.grade_pair([_r('1', '3')], [_r('1', '3')], 'low', 'low', None)['dv_v'] == D('2')


def test_contained_range_is_positive():
    # [0,10] 안의 [4,5]: max(10-4, 5-0) = 6
    assert dv_grade.grade_pair([_r('0', '10')], [_r('4', '5')], 'low', 'low', None)['dv_v'] == D('6')


def test_max_over_timings_and_one_timing_differs():
    a = [_r('1', '1'), _r('1', '1'), _r('1', '1')]
    b = [_r('1', '1'), _r('1', '1'), _r('3.5', '3.5')]
    result = dv_grade.grade_pair(a, b, 'low', 'low', None)
    assert result['dv_v'] == D('2.5') and result['timings_tied'] == [3]


def test_ties_are_ordered_by_timing_number_not_text():
    # N=10: T2 와 T10 이 같은 최대. 글자순이면 T10 이 먼저 온다.
    a = [_r('0', '0')] * 10
    b = [_r('0', '0')] * 10
    b = list(b)
    b[1], b[9] = _r('2', '2'), _r('2', '2')
    result = dv_grade.grade_pair(a, b, 'low', 'low', None)
    assert result['timings_tied'] == [2, 10] and result['dv_v'] == D('2')


def test_threshold_exactly_equal_is_high():
    # 3.3 - 0.3 = 3.0 (float 이면 2.9999999999999996)
    result = dv_grade.grade_pair([_r('3.3', '3.3')], [_r('0.3', '0.3')], 'low', 'low', D('3.0'))
    assert result['dv_v'] == D('3.0') and result['grade'] == 'High'


def test_just_below_threshold_is_low():
    result = dv_grade.grade_pair([_r('2.9999', '2.9999')], [_r('0', '0')], 'low', 'low', D('3.0'))
    assert result['grade'] == 'Low' and result['impedance_class'] == 'low_low'


def test_high_impedance_tiny_dv_is_high_regardless_of_threshold():
    result = dv_grade.grade_pair([_r('0.0001', '0.0001')], [_r('0', '0')], 'high', 'low', D('100'))
    assert result['grade'] == 'High' and result['impedance_class'] == 'high_any'
    result = dv_grade.grade_pair([_r('0.0001', '0.0001')], [_r('0', '0')], 'low', 'high', D('100'))
    assert result['grade'] == 'High'


def test_decimal_exactness_0_3_minus_0_1():
    result = dv_grade.grade_pair([_r('0.3', '0.3')], [_r('0.1', '0.1')], 'low', 'low', D('0.2'))
    assert result['dv_v'] == D('0.2') and result['grade'] == 'High'


def test_low_low_without_threshold_is_high_if_positive():
    assert dv_grade.grade_pair([_r('0', '0')], [_r('0.0001', '0.0001')], 'low', 'low', None)['grade'] == 'High'
    assert dv_grade.grade_pair([_r('1', '1')], [_r('1', '1')], 'low', 'low', None)['grade'] == 'None'


def test_zero_dv_high_impedance_is_none():
    assert dv_grade.grade_pair([_r('1', '1')], [_r('1', '1')], 'high', 'high', None)['grade'] == 'None'


# ---- 쌍 선정 -----------------------------------------------------------------------------------

def test_collect_short_pairs_order_open_and_same_name(tmp_path):
    found = dv_grade.collect_short_pairs(_tree(tmp_path))
    assert found['case_count_found'] == 6 and found['short_case_count'] == 3
    assert found['ignored_same_name_cases'] == ['lateral_open-G-M1-G-M1', 'lateral_short-F-M1-F-M2']
    assert found['excluded_open_cases'] == ['lateral_open-D-M1-E-M1']
    assert sorted(found['pairs']) == [('A', 'B'), ('A', 'C')]
    names = [case['case_name'] for case in found['pairs'][('A', 'B')]]
    assert names == ['lateral_short-A-M1-B-M1', 'vertical_short-B-M2-A-M1']
    reverse = found['pairs'][('A', 'B')][1]
    assert (reverse['electrode_a'], reverse['layer_a'], reverse['electrode_b'], reverse['layer_b']) == ('B', 'M2', 'A', 'M1')
    assert reverse['defect'] == 'vertical_short'


def test_pair_key_with_pipe_names_does_not_collide(tmp_path):
    root = tmp_path / 'product'
    _case(root, 'lateral_short-A-M1-B|C-M1')
    _case(root, 'lateral_short-A|B-M1-C-M1')
    sheet = _write_sheet(tmp_path / 's.csv', [_row('A', 'low', ('0', '1'), ('0', '1')), _row('B|C', 'low', ('2', '2'), ('2', '2')),
                                              _row('A|B', 'low', ('0', '0'), ('0', '0')), _row('C', 'low', ('5', '5'), ('5', '5'))])
    assert _grade(tmp_path, sheet=sheet, root=root) == 0
    keys = sorted(row['pair_key'] for row in _dicts(tmp_path / 'out' / 'dv_grades.csv'))
    assert keys == ['A-B|C', 'A|B-C']


def test_reversed_endpoint_cases_share_one_pair_key(tmp_path):
    assert _grade(tmp_path) == 0
    pairs = {row['pair_key']: row for row in _dicts(tmp_path / 'out' / 'dv_grades.csv')}
    assert sorted(pairs) == ['A-B', 'A-C']
    ab = pairs['A-B']
    assert ab['electrode_a'] == 'A' and ab['electrode_b'] == 'B' and ab['case_count'] == '2'
    assert ab['cases'] == 'lateral_short-A-M1-B-M1;vertical_short-B-M2-A-M1'
    assert ab['defects'] == 'lateral_short;vertical_short'
    by_case = {row['case_name']: row for row in _dicts(tmp_path / 'out' / 'dv_grades_by_case.csv')}
    assert len(by_case) == 3
    rev = by_case['vertical_short-B-M2-A-M1']
    assert (rev['electrode_a'], rev['layer_a'], rev['electrode_b'], rev['layer_b'], rev['pair_key']) == ('B', 'M2', 'A', 'M1', 'A-B')
    assert rev['defect'] == 'vertical_short'


def test_no_short_pairs_exits_2(tmp_path, capsys):
    root = tmp_path / 'product'
    _case(root, 'lateral_open-D-M1-E-M1')
    _case(root, 'lateral_short-F-M1-F-M2')
    assert _grade(tmp_path, root=root) == 2
    assert 'dv_no_short_pairs' in capsys.readouterr().err
    assert not (tmp_path / 'out').exists()


def test_same_name_open_is_only_in_ignored_list(tmp_path):
    assert _grade(tmp_path) == 0
    manifest = _manifest(tmp_path / 'out')
    assert 'lateral_open-G-M1-G-M1' in manifest['ignored_same_name_cases']
    assert manifest['excluded_open_cases'] == ['lateral_open-D-M1-E-M1']


# ---- CLI 정상 흐름 -----------------------------------------------------------------------------

def test_cli_outputs_and_values(tmp_path, capsys):
    assert _grade(tmp_path, threshold='1.5') == 0
    out = tmp_path / 'out'
    assert sorted(path.name for path in out.iterdir()) == sorted(OUT_FILES + ('dv_grade_manifest.json',))
    pairs = {row['pair_key']: row for row in _dicts(out / 'dv_grades.csv')}
    ab, ac = pairs['A-B'], pairs['A-C']
    # A low, B high: T1 max(0-1, 2-0)=2, T2 0
    assert (ab['dv_v'], ab['dv_timing'], ab['dv_timings_tied'], ab['dv_timing_tie_count']) == ('2', 'T1', 'T1', '1')
    assert (ab['impedance_class'], ab['grade'], ab['impedance_a'], ab['impedance_b']) == ('high_any', 'High', 'low', 'high')
    # A low, C low: 두 타이밍 모두 1 -> 동점, 경계 1.5 보다 작아 Low
    assert (ac['dv_v'], ac['dv_timing'], ac['dv_timings_tied'], ac['dv_timing_tie_count']) == ('1', 'T1', 'T1;T2', '2')
    assert (ac['impedance_class'], ac['grade'], ac['threshold_v']) == ('low_low', 'Low', '1.5')
    timings = _dicts(out / 'dv_pair_timings.csv')
    assert [(row['pair_key'], row['timing']) for row in timings] == [('A-B', 'T1'), ('A-B', 'T2'), ('A-C', 'T1'), ('A-C', 'T2')]
    assert timings[0]['dv_ab'] == '-1' and timings[0]['dv_ba'] == '2' and timings[0]['dv_t'] == '2'
    manifest = _manifest(out)
    assert manifest['schema_version'] == 'dv_grade_v1' and manifest['grade_rule_version'] == 'dv_grade_rule_v1'
    assert manifest['product_id'] == 'P1' and manifest['threshold_v'] == '1.5' and manifest['timings'] == 2
    assert manifest['pair_count'] == 2 and manifest['short_case_count'] == 3 and manifest['case_count_found'] == 6
    assert manifest['grade_counts'] == {'None': 0, 'Low': 1, 'High': 1}
    assert manifest['unused_sheet_electrodes'] == ['Z']
    assert set(manifest['outputs']) == set(OUT_FILES)
    for name, digest in manifest['outputs'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest
    assert manifest['sheet']['sha256'] == hashlib.sha256((tmp_path / 'sheet.csv').read_bytes()).hexdigest()
    text = capsys.readouterr().out
    assert 'High' in text and str(out) in text


def test_no_threshold_makes_low_low_high_and_threshold_blank(tmp_path):
    assert _grade(tmp_path) == 0
    pairs = {row['pair_key']: row for row in _dicts(tmp_path / 'out' / 'dv_grades.csv')}
    assert pairs['A-C']['grade'] == 'High' and pairs['A-C']['threshold_v'] == ''
    assert _manifest(tmp_path / 'out')['threshold_v'] is None


def test_zero_dv_pair_has_blank_timing_columns(tmp_path):
    rows = [_row('A', 'low', ('1', '1'), ('1', '1')), _row('B', 'low', ('1', '1'), ('1', '1')),
            _row('C', 'low', ('0', '0'), ('0', '0'))]
    assert _grade(tmp_path, sheet=_write_sheet(tmp_path / 's.csv', rows)) == 0
    ab = {row['pair_key']: row for row in _dicts(tmp_path / 'out' / 'dv_grades.csv')}['A-B']
    assert (ab['grade'], ab['dv_timing'], ab['dv_timings_tied'], ab['dv_timing_tie_count']) == ('None', '', '', '')
    assert _manifest(tmp_path / 'out')['grade_counts']['None'] == 1


def test_images_layout_is_recognized(tmp_path):
    root = tmp_path / 'product'
    _case(root, 'lateral_short-A-M1-B-M1', images=True)
    sheet = _write_sheet(tmp_path / 's.csv', [_row('A', 'low', ('0', '1'), ('0', '1')), _row('B', 'low', ('0', '0'), ('0', '0'))])
    assert _grade(tmp_path, sheet=sheet, root=root) == 0


def test_output_folder_follows_umask(tmp_path):
    import stat
    old = os.umask(0o022)
    try:
        assert _grade(tmp_path) == 0
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / 'out').stat().st_mode) == 0o755


# ---- 입력 오류 ---------------------------------------------------------------------------------

def _fails(tmp_path, capsys, **kwargs):
    code = _grade(tmp_path, **kwargs)
    err = capsys.readouterr().err
    assert code == 2
    assert not (tmp_path / 'out').exists() and not list(tmp_path.glob('.out.staging-*'))
    return err


def test_existing_out_and_missing_root_and_no_cases(tmp_path, capsys):
    (tmp_path / 'out').mkdir()
    assert _grade(tmp_path) == 2
    assert list((tmp_path / 'out').iterdir()) == []
    capsys.readouterr()
    assert _run('--sheet', _sheet(tmp_path), '--root', tmp_path / 'nope', '--product-id', 'P', '--out', tmp_path / 'o2') == 2
    assert not (tmp_path / 'o2').exists()
    empty = tmp_path / 'empty'
    empty.mkdir()
    assert _run('--sheet', _sheet(tmp_path), '--root', empty, '--product-id', 'P', '--out', tmp_path / 'o3') == 2
    assert not (tmp_path / 'o3').exists()


def test_product_id_missing_or_empty(tmp_path, capsys):
    base = ['--sheet', _sheet(tmp_path), '--root', _tree(tmp_path), '--out', tmp_path / 'out']
    with pytest.raises(SystemExit) as info:
        _run(*base)
    assert info.value.code == 2
    assert _run(*base, '--product-id', '') == 2
    assert _run(*base, '--product-id', '   ') == 2
    assert 'dv_product_id_empty' in capsys.readouterr().err
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('value', ['1e-3', '1E2', '1234567', '0.1234567', '.5', '1,5', '5V', 'nan', 'Infinity', '--1', '１'])
def test_voltage_rejects_non_plain_numbers(tmp_path, capsys, value):
    rows = [_row('A', 'low', (value, '1'), ('0', '0')), _row('B', 'low', ('0', '0'), ('0', '0')), _row('C', 'low', ('0', '0'), ('0', '0'))]
    err = _fails(tmp_path, capsys, sheet=_write_sheet(tmp_path / 's.csv', rows))
    assert 'dv_voltage_invalid' in err and 'row=2' in err and 'electrode=A' in err and 'column=T1_min' in err and repr(value)[1:-1] in err
    assert '지수 표기 없이' in err


@pytest.mark.parametrize('value', ['1e-3', '.5', '1234567', 'abc', '0', '-1', '0.0', '', '-0', '+0.0'])
def test_threshold_must_be_positive_plain_decimal(tmp_path, capsys, value):
    err = _fails(tmp_path, capsys, threshold=value)
    assert 'dv_threshold_invalid' in err


@pytest.mark.parametrize('value', ['+1.5', '-12.25', '123456.123456', '0.5', ' 2 ', '-0', '+0.0', '000001.5', '0.000000'])
def test_plain_numbers_are_accepted(tmp_path, value):
    rows = [_row('A', 'low', (value, '999999'), ('0', '0')), _row('B', 'low', ('0', '0'), ('0', '0')), _row('C', 'low', ('0', '0'), ('0', '0'))]
    assert _grade(tmp_path, sheet=_write_sheet(tmp_path / 's.csv', rows)) == 0


def test_negative_zero_is_written_as_zero(tmp_path):
    rows = [_row('A', 'low', ('-0', '-0'), ('0', '0')), _row('B', 'low', ('0', '0'), ('0', '0')), _row('C', 'low', ('-0', '-0'), ('0', '0'))]
    assert _grade(tmp_path, sheet=_write_sheet(tmp_path / 's.csv', rows)) == 0
    text = (tmp_path / 'out' / 'dv_pair_timings.csv').read_text(encoding='utf-8-sig')
    assert '-0' not in text
    assert _dicts(tmp_path / 'out' / 'dv_pair_timings.csv')[0]['dv_ab'] == '0'
    assert '-0' not in (tmp_path / 'out' / 'dv_grades.csv').read_text(encoding='utf-8-sig')


def test_error_value_is_repr_so_newline_stays_on_one_line(tmp_path, capsys):
    rows = [_row('A', 'low', ('1\n2', '1'), ('0', '0')), _row('B', 'low', ('0', '0'), ('0', '0')), _row('C', 'low', ('0', '0'), ('0', '0'))]
    err = _fails(tmp_path, capsys, sheet=_write_sheet(tmp_path / 's.csv', rows))
    line = [item for item in err.splitlines() if 'dv_voltage_invalid' in item][0]
    assert "value='1\\n2'" in line and 'column=T1_min' in line


def test_all_errors_are_collected_with_row_numbers(tmp_path, capsys):
    path = tmp_path / 's.csv'
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(_header(2))
        writer.writerow(_row('A', 'low', ('0', '0'), ('0', '0')))                  # row 2 정상
        writer.writerow(_row('A', 'low', ('0', '0'), ('0', '0')))                  # row 3 중복
        writer.writerow(_row('B', 'mid', ('x', '1'), ('3', '2')))                  # row 4 임피던스·숫자·min>max
        writer.writerow(['Q', 'low', 'M1', '0', '0', '0'])                         # row 5 칸 부족
        writer.writerow(['R', 'low', 'M1', '0', '0', '0', '0', '9'])               # row 6 칸 초과
        writer.writerow(['', 'low', 'M1', '0', '0', '0', '0'])                     # row 7 빈 ID
        writer.writerow(['', '', '', '', '', '', ''])                              # row 8 빈 행: 건너뜀
        writer.writerow(_row('Z', 'high', ('5', '4'), ('0', '0')))                 # row 9 비필수인데 min>max
        writer.writerow(_row('Y', 'bad', ('', ''), ('', '')))                      # row 10 비필수인데 채운 임피던스 오류
    err = _fails(tmp_path, capsys, sheet=path)
    for fragment in ('dv_sheet_duplicate_electrode row=3 electrode=A', 'dv_impedance_invalid row=4 electrode=B column=impedance value=\'mid\'',
                     'dv_voltage_invalid row=4 electrode=B column=T1_min value=\'x\'', 'dv_voltage_range_inverted row=4 electrode=B',
                     'dv_sheet_row_width_invalid row=5', 'dv_sheet_row_width_invalid row=6', 'dv_sheet_electrode_id_empty row=7',
                     'dv_voltage_range_inverted row=9 electrode=Z', 'dv_impedance_invalid row=10 electrode=Y',
                     'dv_sheet_electrode_missing electrode=C'):
        assert fragment in err, fragment
    assert 'row=8' not in err


def test_required_electrode_blank_fields(tmp_path, capsys):
    rows = [_row('A', '', ('0', '0'), ('0', '0')), _row('B', 'low', ('', '0'), ('0', '0')), _row('C', 'low', ('0', '0'), ('0', ''))]
    err = _fails(tmp_path, capsys, sheet=_write_sheet(tmp_path / 's.csv', rows))
    assert 'dv_impedance_invalid row=2 electrode=A' in err
    assert 'dv_voltage_invalid row=3 electrode=B column=T1_min' in err
    assert 'dv_voltage_invalid row=4 electrode=C column=T2_max' in err


def test_optional_row_may_have_blank_voltages_and_impedance(tmp_path):
    rows = [_row('A', 'low', ('0', '0'), ('0', '0')), _row('B', 'low', ('0', '0'), ('0', '0')), _row('C', 'low', ('0', '0'), ('0', '0')),
            _row('Z', '', ('', ''), ('', ''))]
    assert _grade(tmp_path, sheet=_write_sheet(tmp_path / 's.csv', rows)) == 0


@pytest.mark.parametrize('header', [
    ['electrode_id', 'impedance', 'layers', 'T1_min', 'T1_max', 'T2_min'],
    ['electrode_id', 'impedance', 'layers', 'T1_min', 'T1_max', 'T3_min', 'T3_max'],
    ['electrode_id', 'impedance', 'layers', 'T1_min', 'T1_max', 'extra'],
    ['electrode_id', 'impedance', 'T1_min', 'T1_max'],
    ['electrode_id', 'impedance', 'layers'],
])
def test_header_must_match_exactly(tmp_path, capsys, header):
    sheet = _write_sheet(tmp_path / 's.csv', [], header=header)
    assert 'dv_sheet_header_invalid' in _fails(tmp_path, capsys, sheet=sheet)


def test_header_rejects_more_than_20_timings(tmp_path, capsys):
    sheet = _write_sheet(tmp_path / 's.csv', [], n=21)
    assert 'dv_sheet_header_invalid' in _fails(tmp_path, capsys, sheet=sheet)


def test_cp949_sheet_gets_encoding_hint(tmp_path, capsys):
    raw = (','.join(_header(1)) + '\nA,low,가\n').encode('cp949')
    err = _fails(tmp_path, capsys, sheet=_write_sheet(tmp_path / 's.csv', None, raw=raw))
    assert 'dv_sheet_encoding_invalid' in err and 'UTF-8' in err


def test_sheet_missing_exits_2(tmp_path, capsys):
    err = _fails(tmp_path, capsys, sheet=tmp_path / 'nope.csv')
    assert 'dv_sheet_missing' in err


# ---- load_dv_grades ----------------------------------------------------------------------------

def _make(tmp_path):
    assert _grade(tmp_path, threshold='1.5') == 0
    return tmp_path / 'out'


def _edit_csv(out, name, edit, rehash=True):
    path = Path(out) / name
    rows = _rows(path)
    edit(rows)
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        csv.writer(handle, lineterminator='\n').writerows(rows)
    if rehash:
        _rehash(out)


def _rehash(out):
    manifest = _manifest(out)
    manifest['outputs'] = {name: hashlib.sha256((Path(out) / name).read_bytes()).hexdigest() for name in manifest['outputs']}
    _write_manifest(out, manifest)


def _write_manifest(out, manifest):
    (Path(out) / 'dv_grade_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _load_code(out):
    with pytest.raises(dv_grade.DvGradeLoadError) as info:
        dv_grade.load_dv_grades(out)
    return info.value.code


def test_load_round_trip(tmp_path):
    out = _make(tmp_path)
    loaded = dv_grade.load_dv_grades(out)
    assert loaded['manifest']['product_id'] == 'P1'
    assert sorted(row['pair_key'] for row in loaded['pairs']) == ['A-B', 'A-C']
    assert len(loaded['by_case']) == 3
    assert {row['grade'] for row in loaded['pairs']} == {'High', 'Low'}


def test_load_detects_hash_mismatch(tmp_path):
    out = _make(tmp_path)
    path = out / 'dv_grades.csv'
    path.write_bytes(path.read_bytes() + b'\n')
    assert _load_code(out) == 'dv_grade_hash_mismatch'


def test_load_detects_missing_output_file(tmp_path):
    out = _make(tmp_path)
    (out / 'dv_pair_timings.csv').unlink()
    assert _load_code(out) == 'dv_grade_file_missing'


def test_load_rejects_unsupported_versions(tmp_path):
    out = _make(tmp_path)
    manifest = _manifest(out)
    manifest['schema_version'] = 'dv_grade_v2'
    _write_manifest(out, manifest)
    assert _load_code(out) == 'dv_grade_schema_unsupported'
    manifest['schema_version'] = 'dv_grade_v1'
    manifest['grade_rule_version'] = 'dv_grade_rule_v2'
    _write_manifest(out, manifest)
    assert _load_code(out) == 'dv_grade_rule_unsupported'


def test_load_rejects_duplicate_case(tmp_path):
    out = _make(tmp_path)
    _edit_csv(out, 'dv_grades_by_case.csv', lambda rows: rows.append(list(rows[1])))
    assert _load_code(out) == 'dv_grade_duplicate_case'


def test_load_rejects_case_pointing_at_missing_pair(tmp_path):
    out = _make(tmp_path)
    column = _rows(out / 'dv_grades_by_case.csv')[0].index('pair_key')

    def edit(rows):
        rows[1][column] = 'X-Y'
    _edit_csv(out, 'dv_grades_by_case.csv', edit)
    assert _load_code(out) == 'dv_grade_pair_missing'


@pytest.mark.parametrize('field,value', [('dv_v', '9'), ('grade', 'Low'), ('dv_timing', 'T2')])
def test_load_rejects_case_value_differing_from_pair(tmp_path, field, value):
    out = _make(tmp_path)
    header = _rows(out / 'dv_grades_by_case.csv')[0]
    column = header.index(field)

    def edit(rows):
        rows[1][column] = value
    _edit_csv(out, 'dv_grades_by_case.csv', edit)
    assert _load_code(out) == 'dv_grade_case_value_mismatch'


def test_load_rejects_case_electrodes_differing_from_pair(tmp_path):
    out = _make(tmp_path)
    column = _rows(out / 'dv_grades_by_case.csv')[0].index('electrode_a')

    def edit(rows):
        rows[1][column] = 'C'
    _edit_csv(out, 'dv_grades_by_case.csv', edit)
    assert _load_code(out) == 'dv_grade_case_electrode_mismatch'


def test_load_accepts_case_with_reversed_endpoints(tmp_path):
    out = _make(tmp_path)
    loaded = dv_grade.load_dv_grades(out)
    rev = [row for row in loaded['by_case'] if row['case_name'] == 'vertical_short-B-M2-A-M1'][0]
    assert (rev['electrode_a'], rev['electrode_b']) == ('B', 'A')


@pytest.mark.parametrize('mutate', ['delete', 'empty', 'blank', 'number'])
def test_load_rejects_missing_or_empty_product_id(tmp_path, mutate):
    out = _make(tmp_path)
    manifest = _manifest(out)
    if mutate == 'delete':
        del manifest['product_id']
    else:
        manifest['product_id'] = {'empty': '', 'blank': '  ', 'number': 5}[mutate]
    _write_manifest(out, manifest)
    assert _load_code(out) == 'dv_grade_manifest_invalid'


def test_load_missing_manifest(tmp_path):
    out = _make(tmp_path)
    (out / 'dv_grade_manifest.json').unlink()
    assert _load_code(out) == 'dv_grade_manifest_missing'
