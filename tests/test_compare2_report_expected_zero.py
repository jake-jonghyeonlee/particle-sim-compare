"""compare2-report: the information box for expected cases counted as 0 area (compare2-expected-zero/T2)."""
import json

import test_compare2 as tc
import test_compare2_expected_zero as ez
from simcompare.cli import main


def make_report(folder, name='rep'):
    out = folder / 'out'
    code = main(['compare2-report', '--compare2-dir', str(out), '--config', str(folder / 'compare2.yaml'),
                 '--out', str(folder / name)])
    html = (folder / name / 'report.html').read_bytes()
    record = json.loads((folder / name / 'report.json').read_text(encoding='utf-8'))
    return code, html.decode('utf-8'), record


def test_report_shows_the_zero_counted_cases(tmp_path, monkeypatch):
    ref, tgt = ez.two_one_sided()
    folder = tmp_path / 'absent'
    folder.mkdir()
    code, out, doc = ez.run_inventory(folder, monkeypatch, ref, tgt)
    assert code == 0
    exit_code, html, record = make_report(folder)
    assert 'id="absent-reference"' in html and 'id="absent-target"' in html
    assert '기준 A에 폴더가 없는 기대 case 1개를 면적 0 으로 셌습니다.' in html
    assert '대상 B에 폴더가 없는 기대 case 1개를 면적 0 으로 셌습니다.' in html
    assert ez.name_of(ez.BASE['GH']) in html and ez.name_of(ez.BASE['AC']) in html
    assert '한쪽 폴더가 없는 case 는 면적 0 으로 셉니다.' in html
    # information only: the box adds no warning code; exit code and warnings equal the report without the key (below)
    assert 'absent_expected_cases' not in {w['code'] for w in record['warnings']}
    assert exit_code == record['exit_code']
    stripped = json.loads((out / 'compare2.json').read_text(encoding='utf-8'))
    for role in ('reference', 'target'):
        del stripped['sides'][role]['absent_expected_cases']
    (out / 'compare2.json').write_text(json.dumps(stripped), encoding='utf-8')
    exit_without, html_without, record_without = make_report(folder, 'plain')
    assert exit_without == exit_code and record_without['warnings'] == record['warnings']
    assert 'absent-reference' not in html_without and '면적 0 으로 셌습니다' not in html_without


def test_long_name_list_is_folded(tmp_path):
    from simcompare.compare2_report import _absent_block
    names = [f'case{i}' for i in range(5)]
    doc = {'sides': {'reference': {'absent_expected_cases': {'names': names, 'count': 5}}, 'target': {}}}
    block = _absent_block(doc)
    assert '<details><summary>case 이름 보기</summary>' in block and 'case4' in block
    assert _absent_block({'sides': {'reference': {}, 'target': {}}}) == ''


def test_report_without_the_key_has_no_box(tmp_path, monkeypatch):
    ref, tgt = tc.two_sides()
    folder = tmp_path / 'same'
    folder.mkdir()
    code, out, doc = ez.run_inventory(folder, monkeypatch, ref, tgt)
    assert 'absent_expected_cases' not in doc['sides']['reference']
    _exit, html, _record = make_report(folder)
    assert 'absent-' not in html and '면적 0 으로 셌습니다' not in html and '한쪽 폴더가 없는 case' not in html
