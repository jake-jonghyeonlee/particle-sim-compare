"""pasx/T7: `products-sheet` — rows, states, sources, CSV rules, determinism, file-wise atomicity, read only."""
import csv
import io
import json
import os
import re
from pathlib import Path

import pytest

import test_compare2 as tc
from simcompare import cli
from simcompare import pair_assets as pr
from simcompare import products_assets as pa
from simcompare import products_sheet as ps
from test_products_assets import AB, bump_mtime, make_world, rewrite_hotspot, step

NOW = '2026-11-27T09:00:00Z'


@pytest.fixture
def world(tmp_path):
    w = make_world(tmp_path)
    w.doc['common']['report'] = {'diameter_um': 3}
    for name in w.doc['products']:
        w.doc['products'][name]['unit_size_um'] = [0.2, 0.2]
    w.write_doc()
    return w


def build(world, products, steps=('01', '02')):
    for number in steps:
        for product in products:
            assert step(world, product, number, 'T0')[0] == 0


def sheet(world, now=NOW):
    outs, errs = [], []
    code = ps.run_products_sheet(world.path, now=now, out=outs.append, err=errs.append)
    assert code == 0, errs
    return outs[-1]


def folder(world):
    return world.base / 'data/products'


def read_csv(world):
    raw = (folder(world) / ps.CSV_NAME).read_bytes()
    assert raw.startswith(b'\xef\xbb\xbf')
    reader = csv.reader(io.StringIO(raw[3:].decode('utf-8'), newline=''))
    header = next(reader)
    return {row[0]: dict(zip(header, row)) for row in reader}


def tree_state(world):
    """(relative path, size, mtime_ns, sha-ish bytes) of everything below data/products/<P> and the gen.*.yaml files."""
    found = {}
    for path in sorted(world.base.rglob('*')):
        rel = path.relative_to(world.base).as_posix()
        if path.is_file() and (rel.startswith('data/products/') and rel.count('/') >= 3 or rel.startswith('gen.')):
            found[rel] = (path.stat().st_size, path.stat().st_mtime_ns, path.read_bytes())
    return found


# ---- acceptance 1: rows and states ------------------------------------------------------------------------------------------

def test_four_lines_with_their_states_and_reasons(world):
    build(world, ('P1', 'P2'))
    build(world, ('P3',), steps=('01',))                                            # 01 only
    side = pa.open_generation(world.base, 'P2', 'side').path / 'side.json'
    raw = side.read_bytes()
    side.write_bytes(raw[:-1] + (b'}' if raw[-1:] != b'}' else b' '))               # same size, other bytes: 손상
    (folder(world) / 'ZZ_old').mkdir()                                              # a folder of a product that left products.yaml
    (folder(world) / 'ZZ_old' / 'asset_log.jsonl').write_text('{"at":"2026-11-01T00:00:00Z","step":"01","status":"정상"}\n', encoding='utf-8')
    result = sheet(world)
    rows = read_csv(world)
    assert list(rows) == ['P1', 'P2', 'P3', 'ZZ_old']
    assert [rows[p]['자산 상태'] for p in rows] == ['정상', '손상', '없음', '등록 없음']
    assert rows['P2']['이유 코드'] == 'asset_corrupt:file_sha256_mismatch side_unavailable' and rows['P2']['side 상태'] == '손상'
    assert rows['P3']['이유 코드'] and rows['P3']['01 상태'] == '정상' and rows['P3']['summary 상태'] == '없음'
    assert rows['P3']['02 만든 시각'] == '' and rows['P3']['vi_line'] == '' and rows['P3']['지표 상태'] == '없음'
    assert rows['P3']['01 만든 시각'] != '' and rows['P3']['short case 수'] == '6'
    assert rows['ZZ_old']['그룹'] == '' and rows['ZZ_old']['마지막 실행'].startswith('성공 01 2026-11-01')
    assert [item['state'] for item in result['products']] == ['정상', '손상', '없음', '등록 없음']
    print('\n--- sheet rows (state, reasons) ---')
    for name, row in rows.items():
        print(name, row['자산 상태'], row['이유 코드'], row['지표 상태'], row['마지막 실행'])


def test_a_changed_source_and_a_changed_density_are_stale_with_their_reason_codes(world):
    build(world, ('P1', 'P2', 'P3'))
    rewrite_hotspot(world, 'P2', AB, 1, {5, 6, 7})
    sheet(world)
    rows = read_csv(world)
    assert rows['P2']['자산 상태'] == '낡음' and rows['P2']['이유 코드'] == 'source_changed' and rows['P2']['01 상태'] == '낡음'
    assert rows['P1']['자산 상태'] == '정상' and rows['P1']['이유 코드'] == ''
    density = world.base / 'data/input/density'
    target = sorted(density.iterdir())[0]
    target.write_text(target.read_text(encoding='utf-8') + ' \n', encoding='utf-8')
    sheet(world)
    rows = read_csv(world)
    assert rows['P1']['자산 상태'] == '낡음' and 'density_changed' in rows['P1']['이유 코드']
    assert rows['P1']['summary 상태'] == '낡음' and rows['P1']['01 상태'] == '정상'


def test_columns_that_come_from_the_assets_and_the_registration(world):
    world.doc['products']['P1']['note'] = '메모'
    world.write_doc()
    build(world, ('P1', 'P2', 'P3'))
    sheet(world)
    row = read_csv(world)['P1']
    assert row['그룹'] == 'G' and row['note'] == '메모' and row['product_id'] == 'P1' and row['제품군'] == 'mobile'
    assert row['해상도'] == 'x'.join(str(v) for v in tc.RES) and row['unit 크기(um)'] == '0.2x0.2'
    assert row['hotspot 폴더'] == 'data/input/P1' and row['pairs.csv'] == 'data/input/pairs.csv'
    layers = pa.pc.load_products_config(world.path).products['P1']['condition_assignment']['layer_stack']
    assert int(row['층 수']) == len(layers)
    assert row['층 이름(stack_order 순)'].split(' | ') == [i['actual_layer'] for i in sorted(layers, key=lambda i: i['stack_order'])]
    assert row['short case 수'] == '6' and row['CNO case 수'] == '0' and row['제외 case 수'] == '0'
    assert row['기대 case 수'] == '6' and row['absent case 수'] == '0'
    assert row['line 조건 수'] == '3' and row['pixel 조건 수'] == '3'
    assert float(row['관측 지름 최소(um)']) == 1.0 and float(row['관측 지름 최대(um)']) == 50.0 and row['관측 지름 수'] == '32'
    assert row['지표 상태'] == 'available' and row['지도 원자료 상태'] == 'complete' and row['side_basis'] == 'side_extract_v1'
    assert row['지름 부분집합'] == '전체' and float(row['자산 폴더(MB)']) > 0
    assert row['01 만든 시각'].endswith('Z') and float(row['02 소요(s)']) >= 0 and row['마지막 실행'].startswith('성공 02')
    for column in ('manifest', 'input', 'code 01', 'code 02', '그룹 합집합', '밀도 line', '밀도 pixel'):
        assert re.fullmatch(r'sha256:[0-9a-f]{12}', row[column]), (column, row[column])
    manifest = pa.open_generation(world.base, 'P1', 'hotspots').complete['verification']['manifest_sha256']
    assert row['manifest'] == 'sha256:' + manifest[:12]


# ---- acceptance 2: the indicators are the pair side values --------------------------------------------------------------------

def test_vi_and_caa_equal_the_compare2_sides_of_the_pair(world):
    build(world, ('P1', 'P2'))
    outs, errs = [], []
    assert pr.run_pair_step(world.path, 'P1', 'P2', 'compose', run_tag='T1', out=outs.append, err=errs.append) == 0, errs
    sides = json.loads((world.base / 'data/compare/P1__P2/compare2/compare2.json').read_text(encoding='utf-8'))['sides']
    sheet(world)
    rows = read_csv(world)
    for product, role in (('P1', 'reference'), ('P2', 'target')):
        for column, key in (('vi_line', 'vi_line'), ('vi_pixel', 'vi_pixel'), ('caa_line', 'caa_line'), ('caa_pixel', 'caa_pixel')):
            assert float(rows[product][column]) == sides[role][key], (product, column)
        assert float(rows[product]['λ line']) == sides[role]['lambda']['line']
    assert rows['P1']['쌍(마지막 08)'] == 'P1->P2 @ 08 없음' and rows['P2']['쌍(마지막 08)'] == 'P1->P2 @ 08 없음'
    for name in ('ledger', 'maps', 'report'):
        assert pr.run_pair_step(world.path, 'P1', 'P2', name, run_tag='T1', out=outs.append, err=errs.append) == 0, errs
    sheet(world)
    finished = json.loads((world.base / 'data/compare/P1__P2/ledger_report/execution.json').read_text(encoding='utf-8'))['finished_at']
    assert read_csv(world)['P3']['쌍(마지막 08)'] == ''
    assert re.fullmatch(r'P1->P2 @ \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', read_csv(world)['P1']['쌍(마지막 08)'])
    assert read_csv(world)['P1']['쌍(마지막 08)'].endswith(finished[:19] + 'Z')


# ---- acceptance 3: CSV rules ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('note', ['=SUM(1)', '+1', '-1', '@x', '\tx', '\rx'])
def test_a_text_cell_that_looks_like_a_formula_gets_a_leading_quote(world, note):
    world.doc['products']['P1']['note'] = note
    world.write_doc()
    sheet(world)
    assert read_csv(world)['P1']['note'] == "'" + note
    assert "'" + note in (folder(world) / ps.CSV_NAME).read_bytes().decode('utf-8-sig')


def test_a_plain_note_and_numbers_are_not_quoted_and_hashes_have_the_prefix(world):
    world.doc['products']['P1']['note'] = 'plain'
    world.write_doc()
    build(world, ('P1',))
    sheet(world)
    row = read_csv(world)['P1']
    assert row['note'] == 'plain' and not row['vi_line'].startswith("'") and row['manifest'].startswith('sha256:')
    assert (folder(world) / ps.CSV_NAME).read_bytes()[:3] == b'\xef\xbb\xbf'


# ---- acceptance 4: determinism, sheet_id ------------------------------------------------------------------------------------------

def test_the_same_input_gives_byte_equal_files_and_both_files_carry_the_same_sheet_id(world):
    build(world, ('P1', 'P2', 'P3'))
    first = sheet(world)
    csv_a, html_a = [(folder(world) / name).read_bytes() for name in (ps.CSV_NAME, ps.HTML_NAME)]
    second = sheet(world)
    assert [(folder(world) / name).read_bytes() for name in (ps.CSV_NAME, ps.HTML_NAME)] == [csv_a, html_a]
    assert first['sheet_id'] == second['sheet_id']
    assert first['sheet_id'] in html_a.decode('utf-8') and read_csv(world)['P1']['sheet_id'] == first['sheet_id']
    later = sheet(world, now='2027-01-01T00:00:00Z')                                   # only the HTML update time differs
    assert later['sheet_id'] == first['sheet_id'] and (folder(world) / ps.CSV_NAME).read_bytes() == csv_a
    assert (folder(world) / ps.HTML_NAME).read_bytes() != html_a
    step(world, 'P3', '02', 'T2')                                                       # P3 had no 02 before? (it did) -> force a change
    assert step(world, 'P2', '01', 'T3', force=True)[0] == 0
    assert sheet(world)['sheet_id'] != first['sheet_id']


def test_html_is_self_contained_and_shows_the_states_and_the_notice(world):
    build(world, ('P1',))
    sheet(world)
    page = (folder(world) / ps.HTML_NAME).read_text(encoding='utf-8')
    assert '자동 생성 — 손으로 고친 내용은 다음 갱신 때 덮입니다' in page and NOW in page
    assert not re.search(r'(src|href)\s*=\s*["\'](https?:)?//', page) and 'http://' not in page and 'https://' not in page
    assert 'st-ok' in page and 'st-missing' in page and '<details>' in page and 'sha256:' in page
    assert page.count('<tr>') == 1 + 3                                                  # header row + three products
    print('\nHTML sheet: %s (%d bytes)' % (folder(world) / ps.HTML_NAME, len(page.encode('utf-8'))))


# ---- acceptance 5: file-wise atomicity -----------------------------------------------------------------------------------------------

def test_an_interruption_between_the_two_replaces_leaves_each_file_old_or_new_and_the_sheet_ids_differ(world, monkeypatch):
    build(world, ('P1', 'P2', 'P3'))
    first = sheet(world)
    old = {name: (folder(world) / name).read_bytes() for name in (ps.CSV_NAME, ps.HTML_NAME)}
    assert step(world, 'P2', '01', 'T3', force=True)[0] == 0                           # a new content
    real = ps._publish
    calls = []

    def interrupted(path, data):
        calls.append(path.name)
        if len(calls) == 2:
            raise OSError('disk gone before the second replace')
        real(path, data)
    monkeypatch.setattr(ps, '_publish', interrupted)
    errs = []
    assert ps.run_products_sheet(world.path, now=NOW, out=lambda r: None, err=errs.append) == 2 and 'products_sheet_io' in errs[0]
    new_csv, old_html = (folder(world) / ps.CSV_NAME).read_bytes(), (folder(world) / ps.HTML_NAME).read_bytes()
    assert old_html == old[ps.HTML_NAME] and new_csv != old[ps.CSV_NAME]                # CSV is new and whole, HTML is the old one
    csv_id = read_csv(world)['P1']['sheet_id']
    html_id = re.search(r'sheet_id <code>([0-9a-f]{16})</code>', old_html.decode('utf-8')).group(1)
    assert csv_id != html_id and html_id == first['sheet_id']
    assert not [p for p in folder(world).iterdir() if p.name.startswith('.')]          # no temporary file is left
    monkeypatch.setattr(ps, '_publish', real)
    again = sheet(world)
    assert read_csv(world)['P1']['sheet_id'] == again['sheet_id'] and again['sheet_id'] in (folder(world) / ps.HTML_NAME).read_text(encoding='utf-8')


# ---- acceptance 6: read only -----------------------------------------------------------------------------------------------------------

def test_the_sheet_changes_nothing_but_its_two_files(world):
    build(world, ('P1', 'P2', 'P3'))
    pa.ensure_product_settings(pa.pc.load_products_config(world.path), 'P1', pa.open_generation(world.base, 'P1', 'hotspots'))
    (folder(world) / 'ZZ_old').mkdir()
    before = tree_state(world)
    listing_before = sorted(p.relative_to(world.base).as_posix() for p in world.base.rglob('*') if 'products_sheet' not in p.name)
    sheet(world)
    sheet(world)
    assert tree_state(world) == before
    listing_after = sorted(p.relative_to(world.base).as_posix() for p in world.base.rglob('*') if 'products_sheet' not in p.name)
    assert listing_after == listing_before
    assert sorted(p.name for p in folder(world).iterdir() if p.is_file()) == [ps.CSV_NAME, ps.HTML_NAME]


def test_the_sheet_reads_no_mask(world, monkeypatch):
    build(world, ('P1', 'P2', 'P3'))
    from simcompare.adapters import real_hotspot
    called = []
    monkeypatch.setattr(real_hotspot, 'load_real_manifest', lambda *a, **k: called.append(1))
    sheet(world)
    assert called == []


# ---- a missing assets world, a bad config, the command line ------------------------------------------------------------------------------

def test_a_world_without_assets_has_one_line_per_product_with_state_missing(world):
    sheet(world)
    rows = read_csv(world)
    assert [rows[p]['자산 상태'] for p in rows] == ['없음'] * 3 and rows['P1']['이유 코드'] == 'asset_missing hotspots_unavailable' \
        or rows['P1']['이유 코드'].startswith('asset_missing')
    assert rows['P1']['마지막 실행'] == '' and rows['P1']['지표 상태'] == '없음'


def test_a_bad_config_exits_2_and_writes_nothing(world):
    errs = []
    assert ps.run_products_sheet(world.base / 'missing.yaml', out=lambda r: None, err=errs.append) == 2 and errs
    world.path.write_text('schema_version: other\n', encoding='utf-8')
    assert ps.run_products_sheet(world.path, out=lambda r: None, err=errs.append) == 2
    assert not (folder(world) / ps.CSV_NAME).exists() and not (folder(world) / ps.HTML_NAME).exists()


def test_cli_products_sheet(world, capsys):
    build(world, ('P1',))
    assert cli.main(['products-sheet', '--config', str(world.path), '--now', NOW]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'written' and [p['state'] for p in result['products']][0] == '정상'
    assert cli.main(['products-sheet', '--config', str(world.base / 'nope.yaml')]) == 2
    assert '오류' in capsys.readouterr().err
