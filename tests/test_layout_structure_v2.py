"""layer-align/T1: 입력 스캔 v2 — <모델>/{CNO, Merged_Layer/<전극>, Layer} 새 구조와 예전(legacy) 구조 호환."""
import json
from pathlib import Path
import shutil

import numpy as np
import pytest

from simcompare.adapters import electrode_png
from simcompare.adapters.electrode_png import (
    import_electrode_assets, layout_structure, list_dl1_electrodes, load_layer_png, scan_electrode_layers,
    scan_layer_pngs)
from simcompare.numeric_schema import NumericContractError
from simcompare.numeric_images import resolve_path  # noqa: F401  (resolve_path 계약 확인용)

from test_electrode_png import FULL, H, W, numeric_dir, pattern, run, save
from test_change_candidates import (  # noqa: F401  (module-scoped fixture 재사용)
    cli_args, digests, load, world)


def legacy_tree(tmp_path, name='legacy', cno_name='cno'):
    root = tmp_path / name
    for number, (electrode, seed) in enumerate((('A', 1), ('B', 2), ('C', 3)), start=1):
        save(root / 'PRODUCT' / electrode / f'{number}-M2.png', pattern(seed))
    save(root / 'PRODUCT' / 'A' / '9-M1.png', pattern(5))
    save(root / 'PRODUCT' / cno_name / '7_CNT.png', pattern(4))
    return root


def v2_tree(tmp_path, name='v2', cno_name='CNO', layer=True):
    """legacy_tree 와 같은 내용: 전극 폴더를 Merged_Layer/ 아래로 옮긴다."""
    root = tmp_path / name
    for number, (electrode, seed) in enumerate((('A', 1), ('B', 2), ('C', 3)), start=1):
        save(root / 'PRODUCT' / 'Merged_Layer' / electrode / f'{number}-M2.png', pattern(seed))
    save(root / 'PRODUCT' / 'Merged_Layer' / 'A' / '9-M1.png', pattern(5))
    save(root / 'PRODUCT' / cno_name / '7_CNT.png', pattern(4))
    if layer:
        save(root / 'PRODUCT' / 'Layer' / '1-M2.png', pattern(6))
        save(root / 'PRODUCT' / 'Layer' / '2-M1.png', pattern(7))
    return root


class Frame:
    shape_yx = (H, W)
    pixel_pitch_um = 0.1
    origin_xy_um = (50.0, 50.0)
    frame_id = 'FRAME'


def codes(root):
    with pytest.raises(NumericContractError) as info:
        electrode_png._scan_dl1_model(root, 'PRODUCT')
    return info.value.code


# ---- 새 구조 읽기 ----

def test_v2_electrodes_are_merged_layer_subfolders_and_layer_is_not_an_electrode(tmp_path):
    root = v2_tree(tmp_path)
    assert layout_structure(root, 'PRODUCT') == 'merged_layer_v2'
    assert list_dl1_electrodes(root, 'PRODUCT') == ['A', 'B', 'C']
    scan = electrode_png._scan_dl1_model(root, 'PRODUCT')
    assert scan['structure'] == 'merged_layer_v2'
    assert scan['electrodes']['A']['M2']['fs_rel'] == 'PRODUCT/Merged_Layer/A/1-M2.png'
    assert scan['electrodes']['A']['M2']['relative_path'] == 'PRODUCT/Merged_Layer/A/1-M2.png'
    assert [r['layer'] for r in scan['cno']] == ['CNT'] and scan['cno'][0]['fs_rel'] == 'PRODUCT/CNO/7_CNT.png'
    assert sorted(scan['layers']) == ['M1', 'M2']
    assert [w for w in scan['warnings'] if w['status'] == 'layout_structure'] == [
        {'status': 'layout_structure', 'structure': 'merged_layer_v2', 'model': 'PRODUCT'}]


def test_v2_and_legacy_give_the_same_layer_lists_except_for_paths(tmp_path):
    new = scan_electrode_layers(v2_tree(tmp_path), 'PRODUCT')
    old = scan_electrode_layers(legacy_tree(tmp_path), 'PRODUCT')
    assert sorted(new) == sorted(old) == ['M1', 'M2']
    for layer in old:
        assert [p.replace('/Merged_Layer/', '/') for p in new[layer]] == old[layer]


def test_legacy_result_has_no_layers_and_no_layout_structure_warning(tmp_path):
    root = legacy_tree(tmp_path)
    scan = electrode_png._scan_dl1_model(root, 'PRODUCT')
    assert scan['structure'] == 'legacy' and scan['layers'] == {}
    assert not [w for w in scan['warnings'] if w['status'] == 'layout_structure']
    assert layout_structure(root, 'PRODUCT') == 'legacy'
    assert layout_structure(root, 'NOPE') is None
    assert scan_layer_pngs(root, 'PRODUCT') == {} and scan_layer_pngs(root, 'NOPE') == {}


def test_uppercase_cno_is_cnt_in_legacy_and_v2(tmp_path):
    for root in (legacy_tree(tmp_path, cno_name='CNO'), v2_tree(tmp_path, cno_name='CNO'), v2_tree(tmp_path, 'v2b', cno_name='cno')):
        scan = electrode_png._scan_dl1_model(root, 'PRODUCT')
        assert [r['layer'] for r in scan['cno']] == ['CNT']
        assert 'CNO' not in scan['electrodes'] and 'cno' not in scan['electrodes']


def test_legacy_folder_named_layer_stays_an_electrode(tmp_path):
    root = legacy_tree(tmp_path)
    save(root / 'PRODUCT' / 'Layer' / '4-M2.png', pattern(8))
    scan = electrode_png._scan_dl1_model(root, 'PRODUCT')
    assert scan['structure'] == 'legacy' and 'Layer' in scan['electrodes'] and scan['layers'] == {}
    assert list_dl1_electrodes(root, 'PRODUCT') == ['A', 'B', 'C', 'Layer']


def test_scan_layer_pngs_is_sorted_and_names_real_paths(tmp_path):
    root = v2_tree(tmp_path)
    assert scan_layer_pngs(root, 'PRODUCT') == {'M1': 'PRODUCT/Layer/2-M1.png', 'M2': 'PRODUCT/Layer/1-M2.png'}
    assert list(scan_layer_pngs(root, 'PRODUCT')) == ['M1', 'M2']


def test_v2_without_layer_folder_has_empty_layers(tmp_path):
    root = v2_tree(tmp_path, layer=False)
    assert electrode_png._scan_dl1_model(root, 'PRODUCT')['layers'] == {}
    assert scan_layer_pngs(root, 'PRODUCT') == {}


# ---- 오류 5종 ----

def _add_fake_dirs(monkeypatch, parent, names):
    """대소문자를 가리지 않는 파일시스템(macOS)에서는 cno 와 CNO 를 함께 만들 수 없어, 목록에만 가짜 폴더를 더한다."""
    real = electrode_png.os.scandir

    class Fake:
        def __init__(self, name):
            self.name, self.path = name, str(Path(parent) / name)

        def is_dir(self):
            return True

        def is_file(self):
            return False

    def scandir(path):
        entries = list(real(path))
        if Path(path) == Path(parent):
            entries += [Fake(n) for n in names]
        return entries
    monkeypatch.setattr(electrode_png.os, 'scandir', scandir)


def test_error_cnt_folder_duplicate(tmp_path, monkeypatch):
    for root in (legacy_tree(tmp_path, cno_name='cno'), v2_tree(tmp_path, cno_name='cno')):
        with monkeypatch.context() as m:
            _add_fake_dirs(m, root / 'PRODUCT', ['CNO'])
            assert codes(root) == 'cnt_folder_duplicate'


def test_error_layout_structure_mixed(tmp_path):
    root = v2_tree(tmp_path)
    save(root / 'PRODUCT' / 'D' / '4-M2.png', pattern(9))       # Merged_Layer 밖의 전극 폴더
    assert codes(root) == 'layout_structure_mixed'


def test_non_png_folder_next_to_merged_layer_only_warns(tmp_path):
    root = v2_tree(tmp_path)
    (root / 'PRODUCT' / 'notes').mkdir()
    (root / 'PRODUCT' / 'notes' / 'x.txt').write_text('x')
    scan = electrode_png._scan_dl1_model(root, 'PRODUCT')
    assert {'status': 'unrelated_folder', 'path': 'PRODUCT/notes'} in scan['warnings']


def test_error_layer_folder_duplicate(tmp_path, monkeypatch):
    root = v2_tree(tmp_path)
    _add_fake_dirs(monkeypatch, root / 'PRODUCT', ['layer'] if (root / 'PRODUCT' / 'Layer').exists() else ['Layer'])
    assert codes(root) == 'layer_folder_duplicate'


def test_error_invalid_layer_png_name(tmp_path):
    root = v2_tree(tmp_path)
    save(root / 'PRODUCT' / 'Layer' / 'M3.png', pattern(8))
    assert codes(root) == 'invalid_layer_png_name'


def test_error_duplicate_layer_png(tmp_path):
    root = v2_tree(tmp_path)
    save(root / 'PRODUCT' / 'Layer' / '5-M2.png', pattern(8))
    assert codes(root) == 'duplicate_layer_png'


def test_error_codes_reach_exit_two_through_the_cli(tmp_path):
    from simcompare.cli import main
    root = v2_tree(tmp_path)
    save(root / 'PRODUCT' / 'Layer' / '5-M2.png', pattern(8))
    code = main(['import-electrode-assets', '--source-kind', 'dl1_png_folder', '--root', str(root),
                 '--numeric-data-dir', str(numeric_dir(tmp_path)), '--source-version', 'layout_v1',
                 '--max-asset-memory-mb', '512', '--out', str(tmp_path / 'out_cli')])
    assert code == 2 and not (tmp_path / 'out_cli' / 'review_assets.json').exists()


# ---- load_layer_png ----

def test_load_layer_png_polarity_and_crop(tmp_path):
    root = v2_tree(tmp_path)
    got = load_layer_png(root, 'PRODUCT', Frame, scan_layer_pngs(root, 'PRODUCT')['M2'])
    expected = pattern(6)[500:-500, 500:-500]
    assert got.dtype == bool and got.shape == (H, W)
    assert expected.any() and not expected.all()
    assert np.array_equal(got, expected)          # 검정 = 형상, 테두리 500 px 제외


def test_load_layer_png_size_mismatch_is_electrode_frame_mismatch(tmp_path):
    root = v2_tree(tmp_path)
    save(root / 'PRODUCT' / 'Layer' / '1-M2.png', pattern(6, (H + 1000, W + 1001)))
    with pytest.raises(NumericContractError, match=r'electrode_frame_mismatch.*PRODUCT/Layer/1-M2\.png'):
        load_layer_png(root, 'PRODUCT', Frame, 'PRODUCT/Layer/1-M2.png')


# ---- import-electrode-assets ----

def test_import_electrode_assets_on_v2_matches_legacy_and_records_real_paths(tmp_path):
    data = numeric_dir(tmp_path)
    old = run(legacy_tree(tmp_path), tmp_path / 'out_old', data)
    # 같은 데이터로 새 입력: cno 이름이 _/- 어느 쪽이든 같다
    root = v2_tree(tmp_path)
    new = run(root, tmp_path / 'out_new', data)
    assert new['status'] == old['status'] and new['exit_code'] == 0 and new['cases_converted'] == old['cases_converted']
    record = json.loads((tmp_path / 'out_new' / 'electrode_asset_import_record.json').read_text())
    text = json.dumps(record)
    assert 'PRODUCT/Merged_Layer/A/1-M2.png' in text and 'PRODUCT/CNO/7_CNT.png' in text
    assert 'PRODUCT/A/1-M2.png' not in text
    # 같은 내용이므로 asset 파일(내용 주소 지정)은 같다
    old_assets = sorted(p.name for p in (tmp_path / 'out_old' / 'assets').iterdir())
    new_assets = sorted(p.name for p in (tmp_path / 'out_new' / 'assets').iterdir())
    assert old_assets == new_assets
    shutil.rmtree(root)


# ---- candidates-change: legacy 와 새 구조의 계산 출력이 같다 ----

def _norm(path):
    return Path(path).read_bytes()


def test_candidates_change_output_is_identical_for_legacy_and_v2_inputs(world, tmp_path):
    from simcompare.cli import main
    legacy = world.dl1
    v2 = tmp_path / 'dl1_v2'
    shutil.copytree(legacy, v2)
    for model in sorted(p for p in v2.iterdir() if p.is_dir()):
        (model / 'Merged_Layer').mkdir()
        for electrode in sorted(p for p in model.iterdir() if p.is_dir() and p.name not in ('cno', 'Merged_Layer')):
            electrode.rename(model / 'Merged_Layer' / electrode.name)
        (model / 'cno').rename(model / 'CNO')
        # Layer 는 계산에 쓰이지 않는다(T1 은 스캔만): 있어도 출력이 같아야 한다
        save(model / 'Layer' / '1-L1.png', np.zeros((400 + 1000, 420 + 1000), bool))
    outs = {}
    for name, dl1 in (('legacy', legacy), ('v2', v2)):
        out = tmp_path / f'out_{name}'
        assert main(cli_args(world.config, out, dl1)) in (0, 3)
        outs[name] = out
    old, new = digests(outs['legacy']), digests(outs['v2'])
    assert sorted(old) == sorted(new)
    differing = sorted(k for k in old if old[k] != new[k])
    # 다른 파일은 provenance 경로가 들어간 것뿐이어야 한다(키 목록으로 고정)
    assert differing == [k for k in differing if k in ALLOWED_PATH_FILES], differing
    for key in differing:
        a = json.loads((outs['legacy'] / key).read_text(encoding='utf-8'))
        b = json.loads((outs['v2'] / key).read_text(encoding='utf-8'))
        assert _strip_path_values(a, str(legacy)) == _strip_path_values(b, str(v2)), key
    for key in old:
        if key not in differing:
            assert old[key] == new[key]
    payload_old, payload_new = load(outs['legacy']), load(outs['v2'])
    assert payload_old['directions'] == payload_new['directions']


ALLOWED_PATH_FILES = {'execution.json', 'candidate_queries_v1.json', 'alignment.json', 'direct/direct_pairs_sidecar.json'}


def _drop_layout_warnings(obj):
    if isinstance(obj, dict):
        # files_sha256 = CNT 파일 목록(relative_path 포함)의 해시라 cno/ ↔ CNO/ 이름 차이로만 달라진다(경로 키).
        return {k: _drop_layout_warnings(v) for k, v in obj.items() if k != 'files_sha256'}
    if isinstance(obj, list):
        return [_drop_layout_warnings(v) for v in obj
                if not (isinstance(v, dict) and v.get('status') == 'layout_structure')]
    return obj


def _strip_path_values(obj, root_text):
    """dl1 root 문자열, provenance 의 모델 하위 경로(Merged_Layer/·CNO/) 차이, 새 구조 경고(layout_structure)만 지운다."""
    text = json.dumps(_drop_layout_warnings(obj), sort_keys=True)
    text = text.replace(root_text, '<DL1>').replace('Merged_Layer/', '').replace('/CNO/', '/cno/')
    text = text.replace('dl1_v2', 'dl1').replace('out_v2', 'out_legacy')
    return text
