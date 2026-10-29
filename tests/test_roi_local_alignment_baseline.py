"""roi-detail/T2 (a): 인수 없는 candidates-change 실행의 모든 산출 파일 해시를 13f0bb6 코드로 고정한다.

입력은 test_change_roi_v2_baseline 의 합성 world 와 같다. 정규화:
- producer_commit 은 고정 값으로 pin 한다.
- world 루트 경로(와 resolve() 한 /private 형)는 <WORLD> 로 바꾼다.
- 경로가 든 파일의 해시를 값으로 가진 키(DROP_KEYS)는 JSON 에서 뺀다.
- 입력 PNG 의 sha256 은 PIL/zlib 버전에 따라 달라진다(3.9/numpy 1.23.5/Pillow 10.1.0 대 3.11). 그 지문 키(FINGERPRINT_KEYS)의 값은
  placeholder 로 바꾸고, 각 지문이 입력 파일의 실제 해시와 같은지는 test_fingerprints_match_the_input_files 가 따로 단정한다.
- alignment.html 의 data URI PNG 는 디코드한 원시 픽셀(모양+바이트)의 sha256 으로 바꾼다(zlib 구현 차이 회피).
- 그 밖의 파일은 텍스트(경로 치환 후) 그대로 해시한다.
"""
import base64
import hashlib
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from simcompare import roi_export
from test_change_candidates import build_product, run_change, write_config

PINNED_COMMIT = '0' * 40
PLACEHOLDER = '<WORLD>'
DROP_KEYS = ('config_sha256', 'export_sha256', 'index_export_sha256')
FINGERPRINT_KEYS = ('sha256', 'files_sha256', 'manifest_sha256', 'dataset_input_hash', 'source_manifest_hash')
FP = '<SHA256>'
DATA_URI = re.compile(r'data:image/png;base64,([A-Za-z0-9+/=]+)')

# recorded with the 13f0bb6 code (git archive copy), same normalisation; equal on Python 3.11/numpy 2.x and 3.9.14/numpy 1.23.5.
# roi-detail/T3: only 'alignment.html' was re-recorded. Cause = the T3 adv:3 position-figure caption (figcaption and alt gain
# "· 변경 위치는 약 <pitch x 축소 배수> µm 블록 단위로 칠한다(...)"); the normalised HTML differs from 13f0bb6 in those two lines only.
# 'candidate_queries_v1.json' was re-recorded too: its alignment_report.bytes is the byte length of alignment.html, which grew with the caption
# (all other keys unchanged). The other 14 hashes are still the 13f0bb6 values.
EXPECTED = {
    'alignment.html': '47312cba5f1186245b90d32af638280eadc2dc97ff0391abad7622156a2c236b',
    'alignment.json': '5d68280e5cdf8e45149e5c8c073772d8c6c4b067ed02e524cb20c4d632236617',
    'candidate_queries_v1.json': '0535fc868c99cf33feab2f4bfe079b52552f72a37e4c5e0d942e7c6c2ac221f1',
    'direct/direct_pairs_sidecar.json': 'ef5f9351a18e374765ce28b124be96cb28dadd88c288f837790fa42fb468607c',
    'direct/roi_correspondence.json': '2c929403e370f477e5e28dc8a0b482b6d60377310ac4e04d71a99e89691390c8',
    'execution.json': '852e6e6a34f98c896914668bb3bbf0312a9c6822ee9e628a457c577c384e4906',
    'funnel.csv': 'dc760c1db9ac06d9bb4e0426771fbe0b4f231250c129d37f37ae5f435154dd3c',
    'funnel.json': '3580e5156552660d04e5f57ad3feb17c6e2e41a01e9adae6c1941aa3743099ac',
    'reference_export/mask_derivation.json': 'c3bb858150a9d81d529e4e984974d3717c11d893f7aab3e25728edef4006442a',
    'reference_export/review_rois.json': 'cd877643a6f02ed02ee02a82bb680df73e0f5da5725b0fdf332250b71b36329d',
    'reference_index_export/mask_derivation.json': 'c3bb858150a9d81d529e4e984974d3717c11d893f7aab3e25728edef4006442a',
    'reference_index_export/review_rois.json': '3c80a0e102f3b7d605d5d14596c2bb170ea7dd74b46536788207ee3f2a039314',
    'target_export/mask_derivation.json': 'c3bb858150a9d81d529e4e984974d3717c11d893f7aab3e25728edef4006442a',
    'target_export/review_rois.json': 'eaec51855674cd0746a019a37a4565087e9c088740e9429a2684a5939ac2d0dd',
    'target_index_export/mask_derivation.json': 'c3bb858150a9d81d529e4e984974d3717c11d893f7aab3e25728edef4006442a',
    'target_index_export/review_rois.json': '9f31b30e50c26475dba46d53e394572f22eda37e43933b4492b48cc7a201e3cd',
}


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _drop(value):
    if isinstance(value, dict):
        return {k: (FP if k in FINGERPRINT_KEYS else _drop(v)) for k, v in value.items() if k not in DROP_KEYS}
    if isinstance(value, list):
        return [_drop(v) for v in value]
    return value


def _replace_roots(text, roots):
    for root in sorted(set(roots), key=len, reverse=True):
        text = text.replace(root, PLACEHOLDER)
    return text


def _pixel_digest(match):
    image = Image.open(io.BytesIO(base64.b64decode(match.group(1))))
    arr = np.asarray(image)
    return 'PNG[{}|{}|{}]'.format(image.mode, 'x'.join(str(n) for n in arr.shape), _sha_bytes(arr.tobytes()))


def normalised_hashes(out, roots):
    out = Path(out)
    hashes = {}
    for path in sorted(p for p in out.rglob('*') if p.is_file()):
        name = path.relative_to(out).as_posix()
        if path.suffix in ('.json', '.html', '.csv', '.txt'):
            text = _replace_roots(path.read_text(encoding='utf-8'), roots)
            if path.suffix == '.json':
                text = json.dumps(_drop(json.loads(text)), indent=2, ensure_ascii=False)
            elif path.suffix == '.html':
                text = DATA_URI.sub(_pixel_digest, text)
            hashes[name] = _sha_bytes(text.encode('utf-8'))
        else:
            hashes[name] = _sha_bytes(path.read_bytes())
    return hashes


@pytest.fixture(scope='module')
def pinned_run(tmp_path_factory):
    base = tmp_path_factory.mktemp('local_align_baseline_world')
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = build_product(base, side)
    config = write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    world = SimpleNamespace(base=base, config=config, dl1=base/'dl1')
    original = roi_export.export_review_rois
    mp = pytest.MonkeyPatch()

    def pinned(*args, **kwargs):
        kwargs['producer_commit'] = PINNED_COMMIT
        return original(*args, **kwargs)

    mp.setattr(roi_export, 'export_review_rois', pinned)
    try:
        out = tmp_path_factory.mktemp('local_align_baseline_out')/'out'
        code = run_change(world, out)
    finally:
        mp.undo()
    return SimpleNamespace(out=out, code=code, roots=[str(base), str(base.resolve())], base=base)


def test_default_run_writes_exactly_the_pinned_files_with_pinned_hashes(pinned_run):
    assert pinned_run.code == 3
    assert normalised_hashes(pinned_run.out, pinned_run.roots) == EXPECTED


def _walk(value, key=None):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v, key)
    else:
        yield key, value


def _walk_assets(value, key=None):
    """Every dict that carries both ``path`` and ``sha256`` (the asset records of a ``source_assets`` block)."""
    if isinstance(value, dict):
        if 'path' in value and 'sha256' in value:
            yield key, value
        else:
            for k, v in value.items():
                yield from _walk_assets(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_assets(v, key)


def _file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_fingerprints_match_the_input_files(pinned_run):
    """The placeholder-normalised keys still mean what they say: each equals the real sha256 of its input file."""
    from simcompare import change_candidates
    out, base = pinned_run.out, pinned_run.base
    alignment = json.loads((out/'alignment.json').read_text(encoding='utf-8'))
    for name, item in alignment['cnt_inputs'].items():
        for entry in item['files']:
            assert entry['sha256'] == _file_sha(base/'dl1'/entry['relative_path'])
    queries = json.loads((out/'candidate_queries_v1.json').read_text(encoding='utf-8'))
    execution = json.loads((out/'execution.json').read_text(encoding='utf-8'))
    for side in ('reference', 'target'):
        data = base/f'data_{side}'
        spec = queries['inputs'][side]
        assert spec['asset_manifest']['sha256'] == _file_sha(data/spec['asset_manifest']['path'])
        assert spec['electrode_import_record']['sha256'] == _file_sha(data/spec['electrode_import_record']['path'])
        assert spec['manifest_sha256'] == _file_sha(data/spec['manifest_path'])
        assert re.fullmatch(r'[0-9a-f]{64}', spec['dataset_input_hash'])
        assert execution['inputs'][side]['manifest_sha256'] == spec['manifest_sha256']
        export = json.loads((out/f'{side}_export'/'review_rois.json').read_text(encoding='utf-8'))
        assert export['source_manifest_hash'] == spec['dataset_input_hash']
        files = alignment['cnt_inputs'][side]['files']
        assert queries['inputs']['cnt'][side]['files_sha256'] == change_candidates._sha_json(files)
        assert execution['inputs']['cnt'][side]['files_sha256'] == change_candidates._sha_json(files)
    # roi-detail/T2 advice 5: the record hashes in execution.json / the sidecar and the hashes in the index export each match their own input file
    sidecar = json.loads((out/'direct'/'direct_pairs_sidecar.json').read_text(encoding='utf-8'))
    for side in ('reference', 'target'):
        data = base/f'data_{side}'
        spec = queries['inputs'][side]
        for doc in (execution, sidecar):
            record = doc['inputs'][side]
            assert record['manifest_sha256'] == _file_sha(data/spec['manifest_path'])
            for key in ('asset_manifest', 'electrode_import_record'):
                assert record[key]['sha256'] == _file_sha(data/spec[key]['path']), (side, key)
        index = json.loads((out/f'{side}_index_export'/'review_rois.json').read_text(encoding='utf-8'))
        assert index['source_manifest_hash'] == spec['dataset_input_hash']
        checked = 0
        for roi in index['rois']:
            for key, entry in _walk_assets(roi['source_assets']):
                assert entry['sha256'] == _file_sha(base/f'adapter_{side}'/entry['path']), (side, key)
                checked += 1
        assert checked >= 4
    # every sha256 left in the exports names a file of the world (asset PNG/npz copies)
    world_hashes = {_file_sha(p) for p in base.rglob('*') if p.is_file()}
    for side in ('reference', 'target'):
        export = json.loads((out/f'{side}_export'/'review_rois.json').read_text(encoding='utf-8'))
        found = [v for k, v in _walk(export) if k == 'sha256']
        assert found and all(v in world_hashes for v in found)
