"""roi-detail/T2 (a): 인수 없는 candidates-change 실행의 모든 산출 파일 해시를 13f0bb6 코드로 고정한다.

입력은 test_change_roi_v2_baseline 의 합성 world 와 같다. 정규화:
- producer_commit 은 고정 값으로 pin 한다.
- world 루트 경로(와 resolve() 한 /private 형)는 <WORLD> 로 바꾼다.
- 경로가 든 파일의 해시를 값으로 가진 키(DROP_KEYS)는 JSON 에서 뺀다.
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
DATA_URI = re.compile(r'data:image/png;base64,([A-Za-z0-9+/=]+)')

EXPECTED = {   # recorded with the 13f0bb6 code before roi-detail/T2 changed anything
    'alignment.html': '9dafe31eb6cb5b406b6bb44a061b655ff3d990fca7b63eb5c9fc1b064209fee4',
    'alignment.json': 'e0810a78d37a84c51f3efcf83c4d1b68003a78a1460245c3b6dc088d5d354aaa',
    'candidate_queries_v1.json': '25a374670c060c9a4f87ded60eaba3e47ecf39fe6ffa9ac37259d67434ba5176',
    'direct/direct_pairs_sidecar.json': '514ad798b62a6024c1b54625ecadad62928b8ab25f9108cbcf3e33da8a4c9d1d',
    'direct/roi_correspondence.json': '2c929403e370f477e5e28dc8a0b482b6d60377310ac4e04d71a99e89691390c8',
    'execution.json': 'e3bfa2df623e967226316da23e3012e76ad27994e7213c579addc721a03fb774',
    'funnel.csv': 'dc760c1db9ac06d9bb4e0426771fbe0b4f231250c129d37f37ae5f435154dd3c',
    'funnel.json': '3580e5156552660d04e5f57ad3feb17c6e2e41a01e9adae6c1941aa3743099ac',
    'reference_export/mask_derivation.json': 'c3bb858150a9d81d529e4e984974d3717c11d893f7aab3e25728edef4006442a',
    'reference_export/review_rois.json': '247c1b617db13a374fe94f9515253c2b9a4cd603bf5bc593dfa23981c67cbe30',
    'reference_index_export/mask_derivation.json': 'c3bb858150a9d81d529e4e984974d3717c11d893f7aab3e25728edef4006442a',
    'reference_index_export/review_rois.json': 'adc907d1ada14a17d877a039becde6edd17cf47412cdb5338c718eec63e2acfc',
    'target_export/mask_derivation.json': 'c3bb858150a9d81d529e4e984974d3717c11d893f7aab3e25728edef4006442a',
    'target_export/review_rois.json': '6912d0dca63805a1b8b922df14432d57a82223c657c53ae4af7157aa2a8563bf',
    'target_index_export/mask_derivation.json': 'c3bb858150a9d81d529e4e984974d3717c11d893f7aab3e25728edef4006442a',
    'target_index_export/review_rois.json': '91b9d6097b3a9aabdd78d213a03f5d29bea5d581bd5f7a1204b7b9698ee18054',
}


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _drop(value):
    if isinstance(value, dict):
        return {k: _drop(v) for k, v in value.items() if k not in DROP_KEYS}
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
    return SimpleNamespace(out=out, code=code, roots=[str(base), str(base.resolve())])


def test_default_run_writes_exactly_the_pinned_files_with_pinned_hashes(pinned_run):
    assert pinned_run.code == 3
    assert normalised_hashes(pinned_run.out, pinned_run.roots) == EXPECTED
