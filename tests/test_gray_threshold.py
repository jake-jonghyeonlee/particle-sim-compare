"""gray63/T1: hotspot PNG occupancy is ``gray > 63`` (values 1..63 are not occupied) and 01 records the rule."""
import csv
import json

import numpy as np
from PIL import Image
import pytest

from simcompare.adapters.real_hotspot import import_hotspots, load_real_manifest
from simcompare.numeric_schema import NumericContractError
from simcompare.png_strict import HOTSPOT_GRAY_THRESHOLD, read_gray_hotspot


def _save(path, array, mode='L'):
    Image.fromarray(array.astype(np.uint8), mode=mode).save(path)


def test_threshold_constant():
    assert HOTSPOT_GRAY_THRESHOLD == 63


def test_mixed_values_only_above_63_are_occupied(tmp_path):
    array = np.array([[0, 1, 63, 64, 255], [10, 62, 0, 200, 64]], dtype=np.uint8)
    _save(tmp_path / 'a.png', array)
    mask, low = read_gray_hotspot(tmp_path / 'a.png', 'a.png', return_low_gray=True)
    assert np.array_equal(mask, array > 63)
    assert int(mask.sum()) == 4 and low == 4  # occupied: 64, 255, 200, 64; low: 1, 63, 10, 62
    assert low == int(np.count_nonzero((array >= 1) & (array <= 63)))


def test_zero_and_64_only_equals_nonzero_rule(tmp_path):
    array = np.array([[0, 64], [64, 0]], dtype=np.uint8)
    _save(tmp_path / 'b.png', array)
    mask, sha, low = read_gray_hotspot(tmp_path / 'b.png', 'b.png', return_sha256=True, return_low_gray=True)
    assert np.array_equal(mask, array != 0) and low == 0 and len(sha) == 64
    assert np.array_equal(read_gray_hotspot(tmp_path / 'b.png', 'b.png'), array != 0)


def test_rgb_same_channels_follow_the_rule_and_differing_channels_are_rejected(tmp_path):
    gray = np.array([[0, 5, 63], [64, 100, 255]], dtype=np.uint8)
    _save(tmp_path / 'rgb.png', np.stack([gray] * 3, axis=-1), mode='RGB')
    mask, low = read_gray_hotspot(tmp_path / 'rgb.png', 'rgb.png', return_low_gray=True)
    assert np.array_equal(mask, gray > 63) and low == 2
    colored = np.stack([gray, gray, gray], axis=-1)
    colored[0, 0, 1] = 9
    _save(tmp_path / 'color.png', colored, mode='RGB')
    with pytest.raises(NumericContractError) as caught:
        read_gray_hotspot(tmp_path / 'color.png', 'color.png')
    assert caught.value.code == 'png_hotspot_color_rejected'


def _source(root, values, csv_area='0.04'):
    folder = root / 'images' / 'lateral_short-A-L1-B-L1'
    (folder / 'result').mkdir(parents=True)
    for name in ('1.png', '2.png'):
        Image.new('L', (2270, 2270), 0).save(folder / name)
    corner = np.zeros((1270, 1270), dtype=np.uint8)
    for index, value in enumerate(values):
        corner[0, index] = value
    _save(folder / 'hotspot_lateral_short01.png', corner)
    _save(folder / 'hotspot_lateral_short02.png', np.zeros_like(corner))
    with (folder / 'result/lateral_short.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(['defect_size(um)', 'Area(um2)'])
        writer.writerow(['1', csv_area])  # simulator CSV counts every nonzero pixel (4 here)
        writer.writerow(['2', '0'])
    return folder


def _import(tmp_path, values=(0, 30, 63, 64, 255), csv_area='0.04'):
    _source(tmp_path / 'src', values, csv_area)
    import_hotspots(tmp_path / 'src', tmp_path / 'out')
    return json.loads((tmp_path / 'out/manifest.json').read_text(encoding='utf-8'))


def test_import_records_rule_and_counts_with_new_area_and_csv_status(tmp_path):
    manifest = _import(tmp_path)
    assert manifest['hotspot_occupancy'] == {'rule': 'gray_gt_63', 'threshold': 63, 'low_gray_pixels_total': 2}
    record = {r['diameter_um']: r for r in manifest['cases'][0]['records']}[1]
    assert record['occupied_pixels'] == 2 and record['low_gray_pixels'] == 2
    assert record['png_area_um2'] == pytest.approx(0.02)
    # csv-ne0/T1: the CSV check compares (occupied + low_gray) * 0.01 = 0.04 with the nonzero-rule CSV.
    assert record['csv_status'] == 'consistent'
    assert record['area_difference_um2'] == pytest.approx(0.0, abs=1e-12)
    assert load_real_manifest(tmp_path / 'out')['hotspot_occupancy']['rule'] == 'gray_gt_63'


def test_manifest_without_rule_or_with_other_rule_is_refused(tmp_path):
    manifest = _import(tmp_path)
    path = tmp_path / 'out/manifest.json'
    without = {k: v for k, v in manifest.items() if k != 'hotspot_occupancy'}
    path.write_text(json.dumps(without), encoding='utf-8')
    with pytest.raises(NumericContractError) as caught:
        load_real_manifest(tmp_path / 'out')
    assert caught.value.code == 'hotspot_occupancy_rule_mismatch' and '01(import-hotspots)' in str(caught.value)
    other = dict(manifest, hotspot_occupancy={'rule': 'gray_nonzero', 'threshold': 0, 'low_gray_pixels_total': 0})
    path.write_text(json.dumps(other), encoding='utf-8')
    with pytest.raises(NumericContractError) as caught:
        load_real_manifest(tmp_path / 'out')
    assert caught.value.code == 'hotspot_occupancy_rule_mismatch'


def test_changing_hotspot_occupancy_breaks_the_source_payload_hash(tmp_path):
    manifest = _import(tmp_path)
    manifest['hotspot_occupancy']['low_gray_pixels_total'] = 0
    (tmp_path / 'out/manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(NumericContractError) as caught:
        load_real_manifest(tmp_path / 'out')
    assert caught.value.code == 'real_manifest_hash_mismatch'


def test_csv_check_counts_low_gray_pixels_and_survives_reload(tmp_path):
    """csv-ne0/T1 (a): low_gray pixels present, CSV follows the nonzero rule -> consistent, also after load."""
    manifest = _import(tmp_path, csv_area='0.04')
    record = {r['diameter_um']: r for r in manifest['cases'][0]['records']}[1]
    assert record['low_gray_pixels'] == 2 and record['png_area_um2'] == pytest.approx(0.02)
    assert record['csv_status'] == 'consistent'
    loaded = load_real_manifest(tmp_path / 'out')
    reloaded = {r['diameter_um']: r for r in loaded['cases'][0]['records']}[1]
    assert reloaded['csv_status'] == 'consistent' and reloaded['png_area_um2'] == pytest.approx(0.02)


@pytest.mark.parametrize('csv_area', ['0.02', '0.07'])
def test_csv_check_still_reports_a_real_mismatch(tmp_path, csv_area):
    """csv-ne0/T1 (b): a CSV that disagrees with the nonzero pixel count stays mismatch (gray>63-only area included)."""
    manifest = _import(tmp_path, csv_area=csv_area)
    record = {r['diameter_um']: r for r in manifest['cases'][0]['records']}[1]
    assert record['csv_status'] == 'mismatch'
    assert record['area_difference_um2'] == pytest.approx(0.04 - float(csv_area))
    assert load_real_manifest(tmp_path / 'out')['cases'][0]['records'][0]['csv_status'] == 'mismatch'
