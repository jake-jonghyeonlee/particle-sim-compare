"""change-roi-v2 기준선(T0): candidates-change 기본 실행의 산출물 해시를 0717947 에서 고정한다.

뒤 Task 가 기본 실행의 바이트를 바꾸지 않는지 확인하는 회귀 시험이다. 입력은 test_change_candidates 의 first_run 과
같은 world 이다. producer_commit 은 git HEAD 라 commit 마다 바뀌므로 고정 값으로 pin 한다.
정규화: alignment_report(HTML 경로·bytes·downscaled·crops_omitted)는 HTML 이 바뀔 때마다 달라지므로 candidate_queries_v1.json
에서 최상위 `alignment_report` 객체 전체를 지우고 다시 쓴 텍스트를 해시한다. alignment.json·alignment.html·execution.json 은 제외한다.
"""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from simcompare import roi_export
from test_change_candidates import (build_product, load, run_change, write_config)

PINNED_COMMIT = '0' * 40

# data_dir·config_sha256·export_sha256 에 입력 폴더의 절대 경로가 들어간다(sidecar·candidate_queries). 원본 바이트를 그대로 비교하려고
# world 를 고정 폴더에 만들고, 그 폴더의 resolve() 경로가 기록한 값과 다르면(다른 호스트·OS) 해시 단정만 건너뛴다.
WORLD_NAME = 'simcompare_change_roi_v2_baseline'
EXPECTED_WORLD_ROOT = '/private/tmp/' + WORLD_NAME      # macOS: /tmp -> /private/tmp

# 뒤 Task 가 정규화 키를 늘릴 수 있게 한 곳에 둔다(T4b: contributing_cases·sidecar pairs 의 area 키 제외 예정).
CANDIDATE_DROP_KEYS = ('alignment_report',)

RAW_FILES = ('funnel.json', 'funnel.csv', 'direct/roi_correspondence.json', 'direct/direct_pairs_sidecar.json')
RAW_GLOBS = ('*/review_rois.json', '*/mask_derivation.json')

EXPECTED = {   # recorded at 0717947
    'funnel.json': '457ca1d93d71e51adca415ee7441ae37794e73078d61986fea78412abf466dec',
    'funnel.csv': 'dc760c1db9ac06d9bb4e0426771fbe0b4f231250c129d37f37ae5f435154dd3c',
    'direct/roi_correspondence.json': '3ff874f13acae437d1221bd23561bf81fb27235c045faf4d589fca026600d107',
    'direct/direct_pairs_sidecar.json': 'd36b2be014adce6f92ae5232a17f8551274913435e3e499ac6a937e95e703922',
    'reference_export/review_rois.json': 'e8792f0f9c8a101859d5b9c3df273853099bc9731d4d881d670447dbd1c11ed2',
    'reference_index_export/review_rois.json': '8d0696f3d994e7bc424ac4d9c8ee55fa767df4872deb66124618f1bc0998ebe0',
    'target_export/review_rois.json': 'dd917e808d6b0d14db18dfef48f917913c5c46e1106da0165bd0b40c6b33e241',
    'target_index_export/review_rois.json': 'fc3f93efa5995c6fc5c2f3d61c07f37daaf145ae1cd5ddc96ee356cfd5906b5c',
    'reference_export/mask_derivation.json': '15bf297534cb28e7767c0058ade9141b1a645d629273a65c2533261223942d29',
    'reference_index_export/mask_derivation.json': '15bf297534cb28e7767c0058ade9141b1a645d629273a65c2533261223942d29',
    'target_export/mask_derivation.json': '15bf297534cb28e7767c0058ade9141b1a645d629273a65c2533261223942d29',
    'target_index_export/mask_derivation.json': '15bf297534cb28e7767c0058ade9141b1a645d629273a65c2533261223942d29',
    'candidate_queries_v1.json(normalised)': 'f09cd7c35ed3c0887818257f1a797a863cf395d8761f7c4f7ce2f3786fb17f56',
}


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def normalised_candidates_text(path):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    for key in CANDIDATE_DROP_KEYS:
        payload.pop(key, None)
    return json.dumps(payload, indent=2, ensure_ascii=False)


def baseline_hashes(out):
    out = Path(out)
    files = [out/name for name in RAW_FILES]
    for pattern in RAW_GLOBS:
        files.extend(sorted(out.glob(pattern)))
    hashes = {p.relative_to(out).as_posix(): _sha_bytes(p.read_bytes()) for p in files}
    hashes['candidate_queries_v1.json(normalised)'] = _sha_bytes(
        normalised_candidates_text(out/'candidate_queries_v1.json').encode('utf-8'))
    return hashes


@pytest.fixture(scope='module')
def pinned_run(tmp_path_factory):
    base = (Path('/tmp') if Path('/tmp').is_dir() else Path(tempfile.gettempdir()))/WORLD_NAME
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    base = base.resolve()
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
        out = tmp_path_factory.mktemp('baseline_out')/'out'
        code = run_change(world, out)
    finally:
        mp.undo()
    return SimpleNamespace(out=out, code=code, payload=load(out), root=str(base))


def test_exit_code_and_status_match_the_baseline(pinned_run):
    assert pinned_run.code == 3                      # top_k (3) is not filled: only one place changed
    assert pinned_run.payload['producer_commit'] == PINNED_COMMIT


def test_fixture_has_no_images_skipped_warning(pinned_run):
    execution = json.loads((pinned_run.out/'execution.json').read_text(encoding='utf-8'))
    codes = [w.get('code') if isinstance(w, dict) else w for w in pinned_run.payload.get('warnings', [])]
    codes += [w.get('code') if isinstance(w, dict) else w for w in execution.get('warnings', [])]
    assert 'alignment_report_images_skipped' not in codes


def test_default_run_hashes_are_pinned(pinned_run):
    if pinned_run.root != EXPECTED_WORLD_ROOT:
        pytest.skip('hashes were recorded with the world at %s' % EXPECTED_WORLD_ROOT)
    assert baseline_hashes(pinned_run.out) == EXPECTED
