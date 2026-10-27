import csv
import json
from pathlib import Path

import pytest

from simcompare.cli import main

HEADER = ['electrode_id', 'impedance', 'layers', 'T1_min', 'T1_max', 'T2_min', 'T2_max']


def _case(root, name, images=False):
    folder = Path(root) / name
    if images:
        folder = folder / 'images'
    folder.mkdir(parents=True)
    (folder / '1.png').write_bytes(b'')   # 내용은 열지 않는다: 이름 존재만 본다


def _tree(tmp_path):
    root = tmp_path / 'product'
    for name in ('lateral_short-VDD-M1-DATA-M1', 'lateral_short-DATA-M2-SCAN-M1', 'lateralshort-VDD-M2-DATA-M1',
                 'lateral_short-VDD-M1-VDD-M1'):
        _case(root, name)
    return root


def _rows(path):
    with open(path, encoding='utf-8-sig', newline='') as handle:
        return list(csv.reader(handle))


def _run(*args):
    return main(['dv-template', *map(str, args)])


def _db(tmp_path, text, name='db.csv'):
    path = tmp_path / name
    path.write_text(text, encoding='utf-8')
    return path


def test_union_layers_sorted_and_empty_voltages(tmp_path, capsys):
    out = tmp_path / 'out'
    assert _run('--root', _tree(tmp_path), '--timings', 2, '--out', out) == 0
    assert _rows(out / 'dv_sheet.csv') == [
        HEADER,
        ['DATA', '', 'M1;M2', '', '', '', ''],
        ['SCAN', '', 'M1', '', '', '', ''],
        ['VDD', '', 'M1;M2', '', '', '', '']]
    assert _rows(out / 'new_electrodes.csv') == [['electrode_id', 'layers', 'case_count'],
                                                 ['DATA', 'M1;M2', '3'], ['SCAN', 'M1', '1'], ['VDD', 'M1;M2', '2']]
    manifest = json.loads((out / 'dv_template_manifest.json').read_text(encoding='utf-8'))
    assert manifest['schema_version'] == 'dv_template_v1' and manifest['timings'] == 2
    assert manifest['impedance_db'] is None and manifest['electrode_count'] == 3
    assert manifest['filled_impedance_count'] == 0 and manifest['new_electrode_count'] == 3
    assert 'tool_version' in manifest and manifest['db_only_electrodes'] == []
    assert str(out) in capsys.readouterr().out


def test_db_fills_some_and_lists_new_electrodes(tmp_path):
    db = _db(tmp_path, 'electrode_id,impedance\nDATA,high\nVDD,low\nGONE,low\nSCAN,\n')
    out = tmp_path / 'out'
    assert _run('--root', _tree(tmp_path), '--timings', 1, '--impedance-db', db, '--out', out) == 0
    sheet = {row[0]: row[1] for row in _rows(out / 'dv_sheet.csv')[1:]}
    assert sheet == {'DATA': 'high', 'SCAN': '', 'VDD': 'low'}
    assert _rows(out / 'new_electrodes.csv') == [['electrode_id', 'layers', 'case_count'], ['SCAN', 'M1', '1']]
    manifest = json.loads((out / 'dv_template_manifest.json').read_text(encoding='utf-8'))
    assert manifest['filled_impedance_count'] == 2 and manifest['db_only_electrodes'] == ['GONE']
    assert manifest['impedance_db']['path'] == str(db) and len(manifest['impedance_db']['sha256']) == 64


def test_filled_sheet_is_reusable_as_db_and_extra_columns_ignored(tmp_path):
    root = _tree(tmp_path)
    first = tmp_path / 'first'
    assert _run('--root', root, '--timings', 2, '--out', first) == 0
    filled = _rows(first / 'dv_sheet.csv')
    filled[1][1], filled[2][1], filled[3][1] = 'low', 'high', 'low'
    filled[1][3] = '1.5'   # 사용자가 채운 전압은 DB 로 쓸 때 무시된다
    path = tmp_path / 'filled.csv'
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        csv.writer(handle).writerows(filled)
    second = tmp_path / 'second'
    assert _run('--root', root, '--timings', 3, '--impedance-db', path, '--out', second) == 0
    sheet = _rows(second / 'dv_sheet.csv')
    assert [row[1] for row in sheet[1:]] == ['low', 'high', 'low']
    assert all(cell == '' for row in sheet[1:] for cell in row[3:])
    assert len(sheet[0]) == 3 + 6 and _rows(second / 'new_electrodes.csv') == [['electrode_id', 'layers', 'case_count']]


@pytest.mark.parametrize('text', ['electrode_id,impedance\nA,low\nA,low\n',
                                  'electrode_id,impedance\nA,Low\n',
                                  'electrode_id,impedance\nA,mid\n',
                                  'electrode_id,value\nA,low\n'])
def test_bad_db_exits_2_without_output(tmp_path, text):
    out = tmp_path / 'out'
    assert _run('--root', _tree(tmp_path), '--timings', 1, '--impedance-db', _db(tmp_path, text), '--out', out) == 2
    assert not out.exists() and not list(tmp_path.glob('.out.staging-*'))


def test_existing_out_is_rejected(tmp_path):
    out = tmp_path / 'out'
    out.mkdir()
    assert _run('--root', _tree(tmp_path), '--timings', 1, '--out', out) == 2
    assert list(out.iterdir()) == []


@pytest.mark.parametrize('timings', [0, 21, -1])
def test_timings_out_of_range(tmp_path, timings):
    out = tmp_path / 'out'
    assert _run('--root', _tree(tmp_path), '--timings', timings, '--out', out) == 2
    assert not out.exists()


def test_same_name_cases_are_ignored_and_recorded(tmp_path):
    root = tmp_path / 'product'
    _case(root, 'lateral_short-A-M1-B-M1')
    _case(root, 'lateral_short-C-M1-C-M2')
    out = tmp_path / 'out'
    assert _run('--root', root, '--timings', 1, '--out', out) == 0
    assert [row[0] for row in _rows(out / 'dv_sheet.csv')[1:]] == ['A', 'B']
    manifest = json.loads((out / 'dv_template_manifest.json').read_text(encoding='utf-8'))
    assert manifest['ignored_same_name_cases'] == ['lateral_short-C-M1-C-M2'] and manifest['case_count'] == 2


def test_images_layout_is_recognized(tmp_path):
    root = tmp_path / 'product'
    _case(root, 'lateral_short-A-M1-B-M2', images=True)
    out = tmp_path / 'out'
    assert _run('--root', root, '--timings', 1, '--out', out) == 0
    assert [row[0] for row in _rows(out / 'dv_sheet.csv')[1:]] == ['A', 'B']


def test_no_cases_exits_2(tmp_path):
    root = tmp_path / 'empty'
    root.mkdir()
    out = tmp_path / 'out'
    assert _run('--root', root, '--timings', 1, '--out', out) == 2
    assert not out.exists()
