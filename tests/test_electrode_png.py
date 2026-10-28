"""전극 PNG → review_assets_v1 어댑터(T1a): 극성 계약, strict 디코딩, 중앙 crop, 공유·결정성."""
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest
import yaml

from png_helpers import raw_png
from simcompare.adapters import electrode_png
from simcompare.adapters.electrode_png import SOURCE_KIND_CONTRACT, import_electrode_assets
from simcompare.adapters.numeric import load_dataset, source_payload_hash
from simcompare.adapters.real_hotspot import configured_dataset, import_hotspots, load_real_manifest
from simcompare.cli import main
from simcompare.numeric_images import read_binary
from simcompare.numeric_schema import NumericContractError
from simcompare.roi_export import export_review_rois

FIXTURE = Path(__file__).parent / 'fixtures/review_roi_v1'
H, W = 8, 10            # dataset frame; layout PNGs are (H+1000, W+1000)
FULL = (H + 1000, W + 1000)


def numeric_dir(tmp_path, name='numeric'):
    """review_roi_v1 합성 fixture를 hotspot crop frame(원점 (50,50), 0.1 µm)으로 바꾼 사본."""
    root = tmp_path / name
    shutil.copytree(FIXTURE, root)
    manifest = json.loads((root / 'manifest.json').read_text())
    frame = manifest['frames'][0]
    frame['origin_xy_um'], frame['pixel_pitch_um'] = [50.0, 50.0], 0.1
    manifest['unit_domain'].update(unit_width_um=1.0, unit_height_um=0.8, measured_width_um=1.0, measured_height_um=0.8)
    manifest['panel'].update(panel_width_um=10, panel_height_um=8)
    manifest['input_hash'] = manifest['source_manifest_hash'] = source_payload_hash(manifest)
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return root


def save(path, black, mode='L'):
    """black: bool array, True = 검정 픽셀(형상)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    gray = np.where(black, 0, 255).astype(np.uint8)
    image = Image.fromarray(gray)
    if mode == 'RGB':
        image = image.convert('RGB')
    elif mode == '1':
        image = image.convert('1')
    image.save(path)


def pattern(seed, shape=FULL):
    arr = np.zeros(shape, dtype=bool)
    arr[500 + seed:503 + seed, 502:508] = True      # 중앙(crop 안)
    arr[10:12, 10:12] = True                        # 테두리(crop 밖)
    return arr


def dl1_tree(tmp_path, name='dl1', *, extra=True):
    root = tmp_path / name
    for number, (electrode, seed) in enumerate((('A', 1), ('B', 2), ('C', 3)), start=1):
        save(root / 'PRODUCT' / electrode / f'{number}-M2.png', pattern(seed))
    save(root / 'PRODUCT' / 'cno' / '7_CNT.png', pattern(4))
    if extra:
        save(root / 'PRODUCT' / 'A' / '9-M1.png', pattern(5))
        for electrode in ('A',):
            save(root / 'OTHER' / electrode / '1-M2.png', pattern(1))
    return root


def order_file(tmp_path, order=('CNT', 'M2', 'M1')):
    path = tmp_path / 'stack-order.json'
    path.write_text(json.dumps({'stack_order_schema_version': 'dl1_png_stack_order_v1', 'order': list(order)}))
    return path


def run(root, out, data, **kw):
    kw.setdefault('source_version', 'layout_v1')
    kw.setdefault('max_asset_memory_mb', 512)
    return import_electrode_assets('dl1_png_folder', root, out, numeric_data_dir=data, **kw)


def tree_bytes(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(Path(folder).rglob('*')) if p.is_file()}


def combine(adapter_out, data, target):
    """export-review-rois는 data_dir 안의 asset_manifest만 읽으므로 두 폴더를 한 data_dir로 합친다(시험 편의)."""
    shutil.copytree(data, target)
    shutil.copyfile(adapter_out / 'review_assets.json', target / 'review_assets.json')
    for path in (adapter_out / 'assets').iterdir():
        shutil.copyfile(path, target / 'assets' / path.name)
    return target


def export_cli(tmp_path, data_dir, center):
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'synthetic', 'output_dir': 'numeric_outputs',
           'reference': {'data_dir': str(data_dir), 'product_id': 'PRODUCT'},
           'target': {'data_dir': str(data_dir), 'product_id': 'PRODUCT'},
           'map': {'input': {'data_dir': str(data_dir), 'product_id': 'PRODUCT',
                             'asset_manifest': {'path': 'review_assets.json',
                                                'sha256': hashlib.sha256((data_dir / 'review_assets.json').read_bytes()).hexdigest()}},
                   'diameter_um': 5},
           'review_rois': {'manual': [{'product_id': 'PRODUCT', 'design_version': 'DESIGN', 'frame_id': 'FRAME',
                                       'case_id': 'A', 'center_xy_um': center}]}}
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(cfg))
    out = tmp_path / 'roi'
    code = main(['export-review-rois', '--config', str(path), '--out', str(out)])
    return code, json.loads((out / 'review_rois.json').read_text())


def test_dl1_folder_to_ready_rois_through_export_review_rois(tmp_path):
    data = numeric_dir(tmp_path)
    out = tmp_path / 'out'
    result = run(dl1_tree(tmp_path), out, data, stack_order=order_file(tmp_path))
    assert result['status'] == 'available' and result['exit_code'] == 0 and result['cases_converted'] == 2
    code, payload = export_cli(tmp_path, combine(out, data, tmp_path / 'combined'), [50.55, 50.25])
    ready = [r for r in payload['rois'] if r['status'] == 'ready']
    assert len(ready) == 1 and ready[0]['case_id'] == 'A' and ready[0]['reasons'] == []
    assert [e['asset']['value_semantics'] for e in ready[0]['source_assets']['layouts']] == ['binary_zero_is_marked'] * 2
    assert ready[0]['source_assets']['cnt']['value_semantics'] == 'binary_zero_is_marked'
    assert ready[0]['source_assets']['valid']['value_semantics'] == 'binary'
    assert payload['source_manifest_hash'] == load_dataset(data).provenance['input_hash']
    assert code == 0 or code == 3   # blocked 아닌 ROI가 있으면 충분하다(다른 행 상태는 이 시험의 대상이 아님)


def test_polarity_black_pattern_is_shape_and_round_trips_through_dl2_decoding(tmp_path):
    data = numeric_dir(tmp_path)
    out = tmp_path / 'out'
    run(dl1_tree(tmp_path), out, data)
    manifest = json.loads((out / 'review_assets.json').read_text())
    entry = manifest['cases'][0]
    ref = entry['assets']['layouts'][0]['asset']
    assert ref['value_semantics'] == 'binary_zero_is_marked'
    expected = pattern(1)[500:-500, 500:-500]          # A 전극: 검정 = 형상 = 1
    assert expected.any() and not expected.all()
    # DL2 read_asset과 같은 규칙을 psdl2 없이 다시 적용: mode 1/L PNG, {0,255}, binary_zero_is_marked
    with Image.open(out / ref['path']) as image:
        assert image.mode == 'L'
        arr = np.array(image)
    assert set(np.unique(arr).tolist()) <= {0, 255}
    assert np.array_equal(arr == 0, expected)
    # simcompare 자신의 reader도 같은 shape=1을 얻는다
    assert np.array_equal(read_binary(out, ref['path'], sha256=ref['sha256'], value_semantics=ref['value_semantics']), expected)
    valid = entry['assets']['valid']
    assert np.array_equal(read_binary(out, valid['path'], sha256=valid['sha256'], value_semantics='binary'),
                          load_dataset(data).frames['FRAME'].valid)


def test_contract_table_and_unlisted_source_kind_is_blocked(tmp_path):
    assert SOURCE_KIND_CONTRACT == {
        'dl1_png_folder': {'value_semantics': 'binary_zero_is_marked', 'polarity_basis': 'user_answer_2026_10_19'},
        'hotspot_case_electrode': {'value_semantics': 'binary_zero_is_marked', 'polarity_basis': 'user_answer_2026_10_19'}}
    out = tmp_path / 'out'
    result = import_electrode_assets('some_other_png_folder', tmp_path, out, numeric_data_dir=tmp_path,
                                     source_version='v', max_asset_memory_mb=1)
    assert result['status'] == 'blocked' and result['reasons'] == ['electrode_polarity_unspecified']
    assert result['exit_code'] == 3 and not out.exists()


def test_provenance_hashes_and_no_hotspot_files(tmp_path):
    data, root, out = numeric_dir(tmp_path), dl1_tree(tmp_path), tmp_path / 'out'
    before = tree_bytes(root)
    run(root, out, data, stack_order=order_file(tmp_path))
    assert tree_bytes(root) == before          # 입력은 수정하지 않는다
    manifest = json.loads((out / 'review_assets.json').read_text())
    record = json.loads((out / 'electrode_asset_import_record.json').read_text())
    prov = manifest['cases'][0]['electrode_asset_provenance']
    first = prov['layouts'][0]
    assert first['original_sha256'] == hashlib.sha256((root / 'PRODUCT/A/1-M2.png').read_bytes()).hexdigest()
    assert (first['relative_path'], first['original_filename'], first['filename_number'], first['decoded_mode']) == (
        'PRODUCT/A/1-M2.png', '1-M2.png', 1, 'L')
    assert prov['polarity_basis'] == record['polarity_basis'] == 'user_answer_2026_10_19'
    assert prov['frame_correspondence']['crop_offset_px'] == [500, 500]
    assert prov['cnt']['components'][0]['files'][0]['filename_number'] == 7
    assert record['source_kind'] == 'dl1_png_folder' and record['adapter_version'] == 'electrode_png_v1'
    assert len(record['input_hash']) == 64 and record['dataset_input_hash'] == manifest['source_manifest_hash']
    assert record['files']['PRODUCT/A/1-M2.png'] == first['original_sha256']
    assert record['counts']['cases_converted'] == 2
    assert record['stack_order']['models']['PRODUCT']['resolved_order'][0] == {'order_name': 'CNT', 'source_name': 'CNT'}
    names = [p.name for p in out.rglob('*')]
    assert not any(word in name.lower() for name in names for word in ('hotspot', 'ca_', 'density', 'rate'))
    assert not any(word in json.dumps(case['assets']) for case in manifest['cases'] for word in ('hotspot', 'density'))


def test_unbound_files_and_other_models_are_recorded_not_exported(tmp_path):
    data, out = numeric_dir(tmp_path), tmp_path / 'out'
    run(dl1_tree(tmp_path), out, data)
    record = json.loads((out / 'electrode_asset_import_record.json').read_text())
    statuses = {(w['status'], w.get('path') or w.get('model')) for w in record['warnings']}
    assert ('png_not_bound_to_dataset_case', 'PRODUCT/A/9-M1.png') in statuses
    assert ('model_not_in_dataset', 'OTHER') in statuses
    assert 'PRODUCT/A/9-M1.png' not in record['files']


def test_identical_masks_are_written_once(tmp_path):
    data, out = numeric_dir(tmp_path), tmp_path / 'out'
    root = tmp_path / 'dl1'
    same = pattern(1)
    save(root / 'PRODUCT/A/1-M2.png', same)
    save(root / 'PRODUCT/B/2-M2.png', same, mode='RGB')     # 바이트는 다르지만 디코딩 결과는 같다
    save(root / 'PRODUCT/C/3-M2.png', same)
    save(root / 'PRODUCT/cno/1_CNT.png', same)               # CNT 합집합도 같은 마스크 → 같은 파일
    run(root, out, data)
    record = json.loads((out / 'electrode_asset_import_record.json').read_text())
    layouts = {e['asset']['path'] for case in json.loads((out / 'review_assets.json').read_text())['cases']
               for e in case['assets']['layouts']}
    assert len(layouts) == 1
    z_files = sorted(p.name for p in (out / 'assets').glob('z_*.png'))
    assert len(z_files) == 1 and record['counts']['distinct_asset_masks_written'] == 2   # z 마스크 하나 + valid
    assert record['counts']['png_files_decoded'] == 2      # 같은 바이트(1-M2, 3-M2, cno)는 한 번만 디코딩, RGB 사본은 따로


def test_memory_limit_exits_2_with_estimate_and_writes_nothing(tmp_path, capsys):
    data, out = numeric_dir(tmp_path), tmp_path / 'out'
    root = dl1_tree(tmp_path)
    code = main(['import-electrode-assets', '--source-kind', 'dl1_png_folder', '--root', str(root),
                 '--numeric-data-dir', str(data), '--source-version', 'v', '--max-asset-memory-mb', '0.001', '--out', str(out)])
    assert code == 2 and not out.exists()
    err = capsys.readouterr().err
    assert 'asset_memory_limit_exceeded' in err and 'estimated' in err
    assert not [p for p in tmp_path.iterdir() if '.tmp-' in p.name]
    with pytest.raises(SystemExit):                      # 필수 인수: 기본값 없음
        main(['import-electrode-assets', '--source-kind', 'dl1_png_folder', '--root', str(root),
              '--numeric-data-dir', str(data), '--source-version', 'v', '--out', str(out)])


def test_cli_success_and_existing_output_is_refused(tmp_path, capsys):
    data, out, root = numeric_dir(tmp_path), tmp_path / 'out', dl1_tree(tmp_path)
    args = ['import-electrode-assets', '--source-kind', 'dl1_png_folder', '--root', str(root), '--numeric-data-dir', str(data),
            '--source-version', 'v', '--max-asset-memory-mb', '256', '--out', str(out)]
    assert main(args) == 0
    keep = out / 'keep.txt'
    keep.write_text('user file')
    before = tree_bytes(out)
    assert main(args) == 2
    assert 'electrode_output_exists' in capsys.readouterr().err
    assert tree_bytes(out) == before


def test_deterministic_rerun_is_byte_identical(tmp_path):
    data, root = numeric_dir(tmp_path), dl1_tree(tmp_path)
    run(root, tmp_path / 'one', data, stack_order=order_file(tmp_path))
    run(root, tmp_path / 'two', data, stack_order=order_file(tmp_path))
    assert tree_bytes(tmp_path / 'one') == tree_bytes(tmp_path / 'two')


def test_new_provenance_keys_do_not_change_export_rows(tmp_path):
    data, out = numeric_dir(tmp_path), tmp_path / 'out'
    run(dl1_tree(tmp_path), out, data)
    with_prov = combine(out, data, tmp_path / 'with_prov')
    stripped = combine(out, data, tmp_path / 'stripped')
    manifest = json.loads((stripped / 'review_assets.json').read_text())
    manifest.pop('electrode_asset_import')
    for case in manifest['cases']:
        case.pop('electrode_asset_provenance')
    (stripped / 'review_assets.json').write_text(json.dumps(manifest))
    rows = []
    for folder in (with_prov, stripped):
        dataset = load_dataset(folder)
        ref = {'path': 'review_assets.json', 'sha256': hashlib.sha256((folder / 'review_assets.json').read_bytes()).hexdigest()}
        selections = {'manual': [{'product_id': 'PRODUCT', 'design_version': 'DESIGN', 'frame_id': 'FRAME', 'case_id': 'A',
                                  'center_xy_um': [50.55, 50.25]}]}
        export_review_rois(dataset, folder, tmp_path / ('r_' + folder.name), selections=selections, asset_manifest=ref,
                           producer_commit='fixed')
        rows.append(json.loads((tmp_path / ('r_' + folder.name) / 'review_rois.json').read_text())['rois'])
    assert rows[0] == rows[1] and rows[0][0]['status'] == 'ready'
    encoded = json.dumps(rows[0][0]['source_assets'])
    assert not any(word in encoded for word in ('hotspot', 'ca_um2', 'density', 'rate'))


def test_dataset_endpoint_without_png_is_blocked_not_guessed(tmp_path):
    data, out, root = numeric_dir(tmp_path), tmp_path / 'out', dl1_tree(tmp_path)
    (root / 'PRODUCT/C/3-M2.png').unlink()
    result = run(root, out, data)
    record = json.loads((out / 'electrode_asset_import_record.json').read_text())
    assert result['status'] == 'partial' and result['exit_code'] == 3
    assert record['blocked_cases'] == [{'case_id': 'B', 'product_id': 'PRODUCT', 'reasons': ['electrode_png_missing']}]
    assert [c['identity']['case_id'] for c in json.loads((out / 'review_assets.json').read_text())['cases']] == ['A']


def test_layer_case_variant_is_not_matched_and_no_cno_blocks_cnt(tmp_path):
    data, root = numeric_dir(tmp_path), tmp_path / 'dl1'
    for electrode, number in (('A', 1), ('B', 2), ('C', 3)):
        save(root / 'PRODUCT' / electrode / f'{number}-m2.png', pattern(number))
    with pytest.raises(NumericContractError, match='no_case_could_be_converted'):
        run(root, tmp_path / 'out', data)
    root2 = dl1_tree(tmp_path, 'dl1b')
    shutil.rmtree(root2 / 'PRODUCT/cno')
    with pytest.raises(NumericContractError, match='cnt_not_provided'):
        run(root2, tmp_path / 'out2', data)


def _case_error(tmp_path, root, match, **kw):
    out = tmp_path / 'err_out'
    with pytest.raises(NumericContractError, match=match):
        run(root, out, tmp_path / 'numeric_err' if (tmp_path / 'numeric_err').exists() else numeric_dir(tmp_path, 'numeric_err'), **kw)
    assert not out.exists() and not [p for p in tmp_path.iterdir() if '.tmp-' in p.name]


def test_grey_pixel_is_rejected_naming_the_file(tmp_path):
    root = dl1_tree(tmp_path)
    gray = np.where(pattern(1), 0, 255).astype(np.uint8)
    gray[600, 600] = 128
    Image.fromarray(gray).save(root / 'PRODUCT/A/1-M2.png')
    _case_error(tmp_path, root, r'nonbinary_nominal_mask.*PRODUCT/A/1-M2\.png')


def test_near_black_l_value_one_is_not_silently_white(tmp_path):
    root = dl1_tree(tmp_path)
    gray = np.where(pattern(1), 0, 255).astype(np.uint8)
    gray[600, 600] = 1
    Image.fromarray(gray).save(root / 'PRODUCT/A/1-M2.png')
    _case_error(tmp_path, root, r'nonbinary_nominal_mask.*PRODUCT/A/1-M2\.png')


def test_transparent_rgba_is_rejected(tmp_path):
    root = dl1_tree(tmp_path)
    rgba = np.zeros(FULL + (4,), dtype=np.uint8) + 255
    rgba[..., :3] = np.where(pattern(1), 0, 255)[..., None]
    rgba[5, 5, 3] = 0
    Image.fromarray(rgba, 'RGBA').save(root / 'PRODUCT/A/1-M2.png')
    _case_error(tmp_path, root, r'png_nonopaque_pixels.*PRODUCT/A/1-M2\.png')


def test_sixteen_bit_png_is_rejected(tmp_path):
    root = dl1_tree(tmp_path)
    raw = b''.join(b'\x00' + b'\xff\xff' * FULL[1] for _ in range(FULL[0]))
    (root / 'PRODUCT/A/1-M2.png').write_bytes(raw_png(FULL[1], FULL[0], 16, 0, raw))
    _case_error(tmp_path, root, r'PRODUCT/A/1-M2\.png')


def test_exif_rotation_is_rejected(tmp_path):
    root = dl1_tree(tmp_path)
    exif = Image.Exif()
    exif[274] = 6
    Image.fromarray(np.where(pattern(1), 0, 255).astype(np.uint8)).save(root / 'PRODUCT/A/1-M2.png', exif=exif)
    _case_error(tmp_path, root, r'png_nonidentity_orientation.*PRODUCT/A/1-M2\.png')


def test_duplicate_electrode_layer_in_one_folder_names_both_files(tmp_path):
    root = dl1_tree(tmp_path)
    save(root / 'PRODUCT/A/5-M2.png', pattern(1))
    _case_error(tmp_path, root, r'duplicate_electrode_layer.*PRODUCT/A/1-M2\.png.*PRODUCT/A/5-M2\.png')


def test_malformed_png_name_is_an_error(tmp_path):
    root = dl1_tree(tmp_path)
    save(root / 'PRODUCT/A/M2.png', pattern(1))
    _case_error(tmp_path, root, r'invalid_electrode_png_name.*PRODUCT/A/M2\.png')


def test_cno_name_accepts_hyphen_like_electrodes_and_still_accepts_underscore(tmp_path):
    # 실자료 DL1: <model>/<electrode>/1-PXL.png 와 같은 위상의 cno/1-PXL.png(하이픈). 예전 자료 cno/7_CNT.png(밑줄)도 그대로 읽는다.
    root = dl1_tree(tmp_path)
    save(root / 'PRODUCT' / 'cno' / '8-PXL.png', pattern(5))
    scan = electrode_png._scan_dl1_model(root, 'PRODUCT')
    assert sorted((r['filename_number'], r['layer']) for r in scan['cno']) == [(7, 'CNT'), (8, 'PXL')]
    assert sorted(scan['electrodes']) == ['A', 'B', 'C']          # cno 는 전극 목록에 들어가지 않는다
    assert electrode_png.list_dl1_electrodes(root, 'PRODUCT') == ['A', 'B', 'C']


def test_cno_name_without_number_separator_is_still_an_error(tmp_path):
    root = dl1_tree(tmp_path)
    save(root / 'PRODUCT' / 'cno' / 'PXL.png', pattern(5))
    with pytest.raises(NumericContractError, match=r'invalid_electrode_png_name.*PRODUCT/cno/PXL\.png'):
        electrode_png._scan_dl1_model(root, 'PRODUCT')


def test_layer_name_casefold_collision_with_stack_order_rejects(tmp_path):
    root = dl1_tree(tmp_path)
    save(root / 'PRODUCT/B/8-m2.png', pattern(2))
    _case_error(tmp_path, root, r'layer_name_casefold_collision', stack_order=order_file(tmp_path))
    # 순서 파일 자체의 대소문자 충돌도 거부한다
    _case_error(tmp_path, dl1_tree(tmp_path, 'dl1x'), r'stack_order_casefold_collision',
                stack_order=order_file(tmp_path, ('CNT', 'M2', 'm2')))


def test_casefold_variants_without_stack_order_only_warn(tmp_path):
    root, out = dl1_tree(tmp_path), tmp_path / 'out'
    save(root / 'PRODUCT/B/8-m2.png', pattern(2))
    run(root, out, numeric_dir(tmp_path))
    record = json.loads((out / 'electrode_asset_import_record.json').read_text())
    assert any(w['status'] == 'layer_name_case_variants' for w in record['warnings'])


def test_name_absent_from_stack_order_is_an_error(tmp_path):
    _case_error(tmp_path, dl1_tree(tmp_path), r'stack_order_entry_missing.*CNT',
                stack_order=order_file(tmp_path, ('M2', 'M1')))


def test_wrong_layout_shape_and_wrong_frame_origin_are_named_errors(tmp_path):
    root = dl1_tree(tmp_path)
    save(root / 'PRODUCT/A/1-M2.png', pattern(1, (H + 1000, W + 1001)))
    _case_error(tmp_path, root, r'electrode_frame_mismatch.*PRODUCT/A/1-M2\.png')
    with pytest.raises(NumericContractError, match='layout_hotspot_frame_mismatch'):
        run(dl1_tree(tmp_path, 'dl1y'), tmp_path / 'o2', FIXTURE)      # fixture frame: origin (100,200), pitch 0.5


# ---- hotspot_case_electrode (실제 route: real_hotspot_v1 + configured_dataset) ----

CASE = 'lateral_short-A-L1-B-L1'
REAL_SHAPE = (1010, 1020)      # hotspot crop (10, 20)


def real_source(tmp_path, *, shape2=None):
    root = tmp_path / 'hotspot_src'
    folder = root / 'images' / CASE
    (folder / 'result').mkdir(parents=True)
    save(folder / '1.png', pattern(1, REAL_SHAPE))
    save(folder / '2.png', pattern(2, shape2 or REAL_SHAPE))
    crop = np.zeros((REAL_SHAPE[0] - 1000, REAL_SHAPE[1] - 1000), dtype=np.uint8)
    crop[0, 0] = 255
    Image.fromarray(crop).save(folder / 'hotspot_lateral_short01.png')
    Image.fromarray(crop * 0).save(folder / 'hotspot_lateral_short02.png')
    with (folder / 'result/lateral_short.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['defect_size(um)', 'Area(um2)'])
        writer.writerow(['1', '0.01'])
        writer.writerow(['2', '0'])
    return root


def real_data(tmp_path):
    root = real_source(tmp_path)
    imported = tmp_path / 'imported'
    import_hotspots(root, imported)
    (imported / 'pairs.csv').write_text('pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\nAB,A,B,line,pair-v1\n')
    assignment = {'product_id': 'PRODUCT', 'design_version': 'DESIGN', 'source_id': 'SOURCE', 'process_step_id': 'STEP',
                  'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT', 'pair_table_path': 'pairs.csv',
                  'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}]}
    path = tmp_path / 'assignment.json'
    path.write_text(json.dumps(assignment))
    return root, imported, path, assignment


def cnt_root(tmp_path):
    root = tmp_path / 'cnt_dl1'
    save(root / 'PRODUCT/cno/1_CNT.png', pattern(6, REAL_SHAPE))
    return root


def test_hotspot_case_electrode_through_configured_dataset_to_ready_roi(tmp_path, capsys):
    root, imported, assignment_path, assignment = real_data(tmp_path)
    out = tmp_path / 'out'
    code = main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(root),
                 '--numeric-data-dir', str(imported), '--condition-assignment', str(assignment_path),
                 '--cnt-from-dl1-root', str(cnt_root(tmp_path)), '--source-version', 'layout_v1',
                 '--max-asset-memory-mb', '256', '--out', str(out)])
    assert code == 0, capsys.readouterr().err
    manifest = json.loads((out / 'review_assets.json').read_text())
    entry = manifest['cases'][0]
    assert entry['identity']['case_id'] == CASE and entry['frame']['shape_yx'] == [10, 20]
    prov = entry['electrode_asset_provenance']
    assert [(p['electrode_slot'], p['original_filename']) for p in prov['layouts']] == [(1, '1.png'), (2, '2.png')]
    assert prov['source_kind'] == 'hotspot_case_electrode' and prov['polarity_basis'] == 'user_answer_2026_10_19'
    assert prov['cnt']['components'][0]['files'][0]['relative_path'].startswith('cnt_from_dl1_root:')
    # 1.png = electrode 1 (A), 2.png = electrode 2 (B); black = shape
    for slot, seed in ((0, 1), (1, 2)):
        ref = entry['assets']['layouts'][slot]['asset']
        got = read_binary(out, ref['path'], sha256=ref['sha256'], value_semantics=ref['value_semantics'])
        assert np.array_equal(got, pattern(seed, REAL_SHAPE)[500:-500, 500:-500])
    dataset = configured_dataset(imported, load_real_manifest(imported), assignment)
    assert manifest['source_manifest_hash'] == dataset.provenance['input_hash']
    ref = {'path': 'review_assets.json', 'sha256': hashlib.sha256((out / 'review_assets.json').read_bytes()).hexdigest()}
    export_review_rois(dataset, out, tmp_path / 'roi', asset_manifest=ref, producer_commit='fixed',
                       selections={'manual': [{'product_id': 'PRODUCT', 'design_version': 'DESIGN',
                                               'frame_id': 'real_hotspot_crop_v1', 'case_id': CASE,
                                               'center_xy_um': [50.55, 50.25]}]})
    rows = json.loads((tmp_path / 'roi/review_rois.json').read_text())['rois']
    assert [r['status'] for r in rows] == ['ready']
    assert not any(p.name.startswith('hotspot_') for p in out.rglob('*'))


def test_hotspot_case_without_cnt_source_is_blocked_not_zero_filled(tmp_path):
    root, imported, assignment_path, _ = real_data(tmp_path)
    with pytest.raises(NumericContractError, match='no_case_could_be_converted.*cnt_not_provided'):
        import_electrode_assets('hotspot_case_electrode', root, tmp_path / 'out', numeric_data_dir=imported,
                                condition_assignment=assignment_path, source_version='v', max_asset_memory_mb=256)
    assert not (tmp_path / 'out').exists()


def test_real_manifest_requires_explicit_assignment(tmp_path):
    root, imported, _, _ = real_data(tmp_path)
    with pytest.raises(NumericContractError, match='condition_assignment_required'):
        import_electrode_assets('hotspot_case_electrode', root, tmp_path / 'out', numeric_data_dir=imported,
                                source_version='v', max_asset_memory_mb=256)


def test_hotspot_shape_mismatch_between_1_and_2_is_a_named_error(tmp_path):
    _, imported, assignment_path, _ = real_data(tmp_path / 'ok')     # import-hotspots 자체가 거부하는 모양은 사후 교체로 만든다
    bad = tmp_path / 'bad'
    shutil.copytree(tmp_path / 'ok' / 'hotspot_src', bad)
    save(bad / 'images' / CASE / '2.png', pattern(2, (1010, 1021)))
    with pytest.raises(NumericContractError, match=r'electrode_shape_mismatch.*1\.png.*2\.png'):
        import_electrode_assets('hotspot_case_electrode', bad, tmp_path / 'out', numeric_data_dir=imported,
                                condition_assignment=assignment_path, cnt_from_dl1_root=cnt_root(tmp_path),
                                source_version='v', max_asset_memory_mb=256)


def test_hotspot_case_name_must_match_dataset_endpoints(tmp_path):
    root, imported, assignment_path, _ = real_data(tmp_path)
    renamed = tmp_path / 'renamed'
    shutil.copytree(root, renamed)
    (renamed / 'images' / CASE).rename(renamed / 'images' / 'lateral_short-A-L1-C-L1')
    with pytest.raises(NumericContractError, match='no_case_could_be_converted.*electrode_case_folder_missing'):
        import_electrode_assets('hotspot_case_electrode', renamed, tmp_path / 'out', numeric_data_dir=imported,
                                condition_assignment=assignment_path, cnt_from_dl1_root=cnt_root(tmp_path),
                                source_version='v', max_asset_memory_mb=256)


try:
    import resource
except ImportError:      # Windows: no getrusage
    resource = None

_RSS_PROBE = """
import resource, sys, tempfile
from pathlib import Path
from simcompare.adapters.electrode_png import _Writer, _crop, _decode
data = Path(sys.argv[1]).read_bytes()
folder = Path(tempfile.mkdtemp())
(folder / 'assets').mkdir()
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
marked, _ = _decode(data, 'probe')
_Writer(folder).put(_crop(marked, (2400, 2400), 'probe'), 'binary_zero_is_marked')
after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(after - before)
"""


def test_memory_estimate_is_realistic_for_color_modes_not_just_gray():
    # B5: 3400x3400 RGB/RGBA와 회색조의 추정이 다르고, 옛 추정(104.7 MiB)보다 충분히 크다.
    size = 1 << 20
    gray = electrode_png._estimate_mb([(3400, 3400, size, 'L')], 2400 ** 2)
    for mode in ('RGB', 'RGBA'):
        assert electrode_png._estimate_mb([(3400, 3400, size, mode)], 2400 ** 2) > 200
    assert gray < electrode_png._estimate_mb([(3400, 3400, size, 'RGB')], 2400 ** 2)
    assert electrode_png._estimate_mb([(10, 10, 1, 'RGB')], 100, dataset_bytes=50 * 2 ** 20) > 50   # 데이터셋 선적재 계상


@pytest.mark.skipif(resource is None, reason='resource.getrusage unavailable on this platform')
@pytest.mark.parametrize('mode', ['RGB', 'RGBA'])
def test_memory_estimate_bounds_measured_peak_rss_of_color_decode_and_write(tmp_path, mode):
    n = 3400
    pixels = np.zeros((n, n), np.uint8)
    pixels[100:200, :] = 255
    path = tmp_path / f'{mode}.png'
    Image.fromarray(pixels).convert(mode).save(path)
    out = subprocess.run([sys.executable, '-c', _RSS_PROBE, str(path)], capture_output=True, text=True, check=True)
    unit = 1 if sys.platform == 'darwin' else 1024          # ru_maxrss: macOS bytes, Linux KiB
    measured_mb = int(out.stdout.strip()) * unit / 2 ** 20
    estimate = electrode_png._estimate_mb([(n, n, path.stat().st_size, mode)], 2400 ** 2)
    assert measured_mb <= 1.25 * estimate, (mode, measured_mb, estimate)


_FULL_IMPORT_PROBE = """
import json, resource, sys
from pathlib import Path
from simcompare.adapters import electrode_png as e
from simcompare.numeric_schema import NumericContractError
root, limit = Path(sys.argv[1]), float(sys.argv[2])
seen = []
original = e._estimate_mb
def spy(*args, **kwargs):
    value = original(*args, **kwargs)
    seen.append(value)
    return value
e._estimate_mb = spy
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
code = 0
try:
    e.import_electrode_assets('hotspot_case_electrode', root / 'source', root / 'adapter', numeric_data_dir=root / 'imported',
                              condition_assignment=root / 'assignment.json', cnt_from_dl1_root=root / 'cnt',
                              source_version='rss-test', max_asset_memory_mb=limit)
except NumericContractError as exc:
    code = 2 if 'asset_memory_limit_exceeded' in str(exc) else 1
    print(exc, file=sys.stderr)
after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({'estimate_mb': seen[-1], 'peak_bytes': after - before, 'code': code,
                  'adapter_exists': (root / 'adapter').exists()}))
"""


def _full_size_fixture(tmp_path):
    from simcompare.numeric_schema import DIAMETERS_UM
    source = tmp_path / 'source'
    case = 'lateral_short-A-L1-B-L1'
    folder = source / 'images' / case
    (folder / 'result').mkdir(parents=True)
    image = Image.new('RGB', (3400, 3400), 0)
    image.save(folder / '1.png')
    image.save(folder / '2.png')
    cnt = tmp_path / 'cnt' / 'PRODUCT' / 'cno'
    cnt.mkdir(parents=True)
    image.save(cnt / '1_CNT.png')
    mask = np.zeros((2400, 2400), np.uint8)
    mask[0, 0] = 255
    for d in DIAMETERS_UM:
        Image.fromarray(mask).save(folder / f'hotspot_lateral_short{d:02d}.png')
    with (folder / 'result' / 'lateral_short.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['defect_size(um)', 'Area(um2)'])
        writer.writerows([[d, '0.01'] for d in DIAMETERS_UM])
    expected = tmp_path / 'expected.txt'
    expected.write_text(case + '\n')
    import_hotspots(source, tmp_path / 'imported', expected_cases=expected)
    assignment = {'product_id': 'PRODUCT', 'design_version': 'DESIGN', 'source_id': 'SOURCE', 'process_step_id': 'STEP',
                  'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT',
                  'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}]}
    (tmp_path / 'assignment.json').write_text(json.dumps(assignment))


def test_dataset_bytes_counts_every_retained_mask_without_double_counting_aliases(tmp_path):
    root, imported, assignment_path, _ = real_data(tmp_path)
    dataset = electrode_png._load_dataset(imported, 'manifest.json', assignment_path)
    arrays = {}

    def walk(obj, seen=set()):
        if isinstance(obj, np.ndarray):
            base = obj
            while isinstance(base.base, np.ndarray):
                base = base.base
            arrays[id(base)] = base.nbytes
    for frame in dataset.frames.values():
        walk(frame.valid)
    # Real records are lazy since the streaming compare: the loaded dataset retains no source mask.
    assert electrode_png._dataset_bytes(dataset) == sum(int(f.valid.nbytes) for f in dataset.frames.values())
    for case in dataset.cases:  # an eager dict of loaded records is still counted mask by mask
        case.records = {d: case.records[d] for d in case.records}
    masks = [r.mask for c in dataset.cases for r in c.records.values() if r.mask is not None]
    assert masks, 'fixture must retain observed masks'
    for m in masks:
        walk(m)
    assert electrode_png._dataset_bytes(dataset) == sum(arrays.values())
    assert electrode_png._dataset_bytes(dataset) > sum(int(f.valid.nbytes) for f in dataset.frames.values())


@pytest.mark.skipif(resource is None, reason='resource.getrusage unavailable on this platform')
def test_full_import_path_peak_rss_is_bounded_by_estimate_and_low_limit_fails_before_writing(tmp_path):
    # R1: 실제 import_hotspots + import_electrode_assets, 32 지름 × 2400² 마스크, 3400² RGB PNG.
    _full_size_fixture(tmp_path)
    unit = 1 if sys.platform == 'darwin' else 1024          # ru_maxrss: macOS bytes, Linux KiB

    def probe(limit):
        run = subprocess.run([sys.executable, '-c', _FULL_IMPORT_PROBE, str(tmp_path), str(limit)],
                             capture_output=True, text=True, check=True, cwd=tmp_path)
        return json.loads(run.stdout.strip().splitlines()[-1])

    ok = probe(2048)
    assert ok['code'] == 0 and ok['adapter_exists']
    measured_mb = ok['peak_bytes'] * unit / 2 ** 20
    assert measured_mb <= 1.25 * ok['estimate_mb'], (measured_mb, ok['estimate_mb'])
    shutil.rmtree(tmp_path / 'adapter')
    low = probe(ok['estimate_mb'] * 0.9)       # 진짜 필요량 아래의 한도 → 쓰기 전에 거절
    assert low['code'] == 2 and not low['adapter_exists']


def test_hotspot_case_electrode_with_case_images_subfolder_and_defect_alias(tmp_path, capsys):
    src = tmp_path / 'hotspot_src'
    case = 'lateralshort-A-L1-B-L1'
    folder = src / case
    (folder / 'result').mkdir(parents=True)
    (folder / 'images').mkdir()
    save(folder / 'images/1.png', pattern(1, REAL_SHAPE))
    save(folder / 'images/2.png', pattern(2, REAL_SHAPE))
    crop = np.zeros((REAL_SHAPE[0] - 1000, REAL_SHAPE[1] - 1000), dtype=np.uint8)
    crop[0, 0] = 255
    Image.fromarray(crop).save(folder / 'images/hotspot_lateral_short01.png')
    with (folder / 'result/lateral_short.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['defect_size(um)', 'Area(um2)'])
        writer.writerow(['1', '0.01'])
    imported = tmp_path / 'imported'
    import_hotspots(src, imported)
    (imported / 'pairs.csv').write_text('pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\nAB,A,B,line,pair-v1\n')
    assignment = {'product_id': 'PRODUCT', 'design_version': 'DESIGN', 'source_id': 'SOURCE', 'process_step_id': 'STEP',
                  'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT', 'pair_table_path': 'pairs.csv',
                  'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}]}
    path = tmp_path / 'assignment.json'
    path.write_text(json.dumps(assignment))
    out = tmp_path / 'out'
    code = main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(src),
                 '--numeric-data-dir', str(imported), '--condition-assignment', str(path),
                 '--cnt-from-dl1-root', str(cnt_root(tmp_path)), '--source-version', 'layout_v1',
                 '--max-asset-memory-mb', '256', '--out', str(out)])
    assert code == 0, capsys.readouterr().err
    entry = json.loads((out / 'review_assets.json').read_text())['cases'][0]
    assert entry['identity']['case_id'] == case
    prov = entry['electrode_asset_provenance']
    assert [(p['electrode_slot'], p['original_filename']) for p in prov['layouts']] == [(1, '1.png'), (2, '2.png')]
