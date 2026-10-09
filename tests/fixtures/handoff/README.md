# 실제 producer 함수에서 만든 합성 파일 교환 fixture

실제 data/성능 label/평가 숫자가 아니다. Native DL search/index JSON은 해당 저장소 `.venv`에서 실행한 함수의 반환값을 그대로 JSON으로 쓴 뒤 temp에서 복사했다. native 파일을 수작업으로 고치지 않았다. Negative 사례는 test가 temp 복사본에 명시적으로 변형하며 이 원본은 유지한다.

- C baseline: `f9d7a31d85eadc14d4b8467f452e3610ea6378cb`
- DL1: `7f9d659a014d7f5e3aaf4b63118d0ca51774b75f`
- DL2: `e46a44e0afbe94cd3f7a8d0264ee02f3076cf44b`
- C interpreter: Python 3.11.15; DL interpreters: Python 3.12.13.

생성 명령(repo C cwd):

```bash
SIMCOMPARE_HANDOFF_GENERATE=/tmp/simcompare-t12-native \
  .venv/bin/python -m pytest -p no:warnings tests/test_handoff.py -k actual -x
```

`@pytest.mark.cross_repo` test가 실제 실행한 세 명령:

```bash
/Users/jonghyeonlee/git/particle-sim-compare/.venv/bin/python \
  /Users/jonghyeonlee/git/particle-sim-compare/tests/fixtures/handoff/generate.py prepare \
  /tmp/simcompare-t12-native --repo /Users/jonghyeonlee/git/particle-sim-compare
/Users/jonghyeonlee/git/particle-sim-dl1/.venv/bin/python \
  /Users/jonghyeonlee/git/particle-sim-compare/tests/fixtures/handoff/generate.py dl1 \
  /tmp/simcompare-t12-native --repo /Users/jonghyeonlee/git/particle-sim-dl1
/Users/jonghyeonlee/git/particle-sim-dl2/.venv/bin/python \
  /Users/jonghyeonlee/git/particle-sim-compare/tests/fixtures/handoff/generate.py dl2 \
  /tmp/simcompare-t12-native --repo /Users/jonghyeonlee/git/particle-sim-dl2
```

마지막으로 test가 `simcompare.dl_results.create_exchange`를 실행하여 `exchange.json`을 생성한다. 같은 동작의 CLI:

```bash
FP=$(.venv/bin/python -c 'import json; print(json.load(open("/tmp/simcompare-t12-native/dl1_search.json"))["fingerprint"])')
.venv/bin/python -m simcompare.dl_results seal \
  --context /tmp/simcompare-t12-native/numeric_context.json \
  --dl1 /tmp/simcompare-t12-native/dl1_search.json --dl1-fingerprint "$FP" \
  --dl1-bindings /tmp/simcompare-t12-native/dl1_bindings.json \
  --dl2 /tmp/simcompare-t12-native/dl2_search.json \
  --dl2-index /tmp/simcompare-t12-native/dl2_index.json \
  --dl2-expected /tmp/simcompare-t12-native/dl2_expected.json \
  --out /tmp/simcompare-t12-native/exchange.json
```

생성 후 Python `shutil.copyfile` / `copytree`로 temp 파일을 이 폴더에 복사했다. `exchange.json`의 파일 상대 경로와 exact byte hash가 그대로 유지된다. `review_rois.json`의 생성 당시 `assets_root`는 temp 절대 경로이므로 재실행에서는 항상 DL2 `--assets-root tests/fixtures/handoff/numeric`으로 명시적으로 재배치한다. 원본 frame이나 source asset reference를 수정하지 않는다.

`prepare`는 C의 기존 `review_roi_v1` 합성 fixture를 복사하여 valid hole을 채운 별도 synthetic 원본을 만든다. valid file hash, asset reference와 source_payload_hash를 다시 계산하고, 다른 제품 OTHER에 case A를 복사하여 case_id 충돌 반례를 만든다. C export API가 manual 2개, multi-case map region 2개, case 없는 blocked manual 1개를 만든다. 이 원본 manifest는 DL native JSON이 아니며 `generate.py`의 명시적 합성 정의다. 각 map case의 d=5 hotspot은 4x4 cells, pitch 0.5이므로 CA=4 um²; line union=4, pixel=0이다. map case A/B가 같은 region이어도 roi_id가 다르다.

DL1 `dl1_search.json`은 C의 실제 원본 layout/CNT/valid에서 `Product`를 만들고 `psdl1.search.search`를 실행한 결과다. explicit input identity binding은 원본 case A에만 연결한다. 같은 role/layer의 B/C를 제품 set에 중복 넣지 않는다. `dl1_standalone_search.json`은 DL1 자체 `tests/conftest.py:make_product('bus',32)` fixture로 만든 별도의 query/candidate search 결과다.

DL2 `dl2_index.json`, `dl2_search.json`은 C export를 `psdl2.data.load_export`로 import하고, `build_index` / `search`로 생성했다. own `configs/synthetic.example.json`의 명시적 합성 geometry를 사용하되 C 원본의 실제 layer `M2`를 vocabulary로 설정한다. 구조/좌표 검증 예제이며 실측 crop 기본값이 아니다. OTHER/A는 supplementary를 유지하고, blocked manual은 index에 들어가지 않는다.

`dl2_standalone/`는 DL2 자체 `tests/test_end_to_end.py:synthetic_exchange`가 만든 3 products / 6 ROIs, 원본 assets/config/split/lineages와 그 index/search다. 원본 model 초기화만 썼다. optimizer step, download, public weights, retrieval 품질 합격 판정은 없다.

`numeric_context.json`과 native index/search는 실제 함수의 결과이며 **expected oracle가 아니다**. 테스트의 4 um², A/B 두 행, product collision, origin (100,200), source hash와 scope 등 expected는 테스트 코드에 직접 적었다. 재생성 test는 sibling repo/.venv가 없으면 명시적인 cross-repo skip reason을 남긴다. simcompare runtime은 psdl1/psdl2를 import하지 않는다.
