# 폐쇄망 실행 순서

아래 변수는 사용자가 실제 경로와 승인한 설정으로 채운다. 모든 출력은 bundle 밖의 `$RUN`에 둔다. sample 성능값이나 실측 crop 기본값을 제공하지 않는다.

```bash
export PYTHONDONTWRITEBYTECODE=1
export BUNDLE=/approved-output/bundle
export RUN=/approved-output/user-run
export ENVS=/approved-envs
export C=$ENVS/compare/bin/python
export D1=$ENVS/dl1/bin/python
export D2=$ENVS/dl2/bin/python
mkdir -p "$RUN"
bash "$BUNDLE/particle-sim-compare/scripts/check_handoff.sh" "$BUNDLE"
```

## 1. 의존성 설치

세 env를 별도로 만들며 DL2는 Python >=3.12가 필요하다. C의 `uv.lock`, DL1/DL2의 `requirements.lock`이 dependency 버전을 고정한다. DL lock은 macOS arm64/Python 3.12 runtime snapshot이며 목표 플랫폼 wheel 준비는 사용자가 확인한다. 네트워크 금지 환경에서는 보안 승인된 wheel directory와 uv를 사전에 준비한다. public weights는 준비하지 않는다.

```bash
python3.12 -m venv "$ENVS/compare"
python3.12 -m venv "$ENVS/dl1"
python3.12 -m venv "$ENVS/dl2"
uv export --frozen --project "$BUNDLE/particle-sim-compare" --no-dev --no-emit-project --format requirements-txt --output-file "$RUN/compare.requirements.txt"
cp "$BUNDLE/particle-sim-dl1/requirements.lock" "$RUN/dl1.requirements.txt"
cp "$BUNDLE/particle-sim-dl2/requirements.lock" "$RUN/dl2.requirements.txt"
uv pip install --python "$C" --no-index --find-links /approved-wheels --require-hashes -r "$RUN/compare.requirements.txt"
uv pip install --python "$D1" --no-index --find-links /approved-wheels -r "$RUN/dl1.requirements.txt"
uv pip install --python "$D2" --no-index --find-links /approved-wheels -r "$RUN/dl2.requirements.txt"
```

허용된 내부 PyPI mirror를 사용하는 설치망이라면 세 `uv pip install`에서 `--no-index --find-links /approved-wheels` 대신 `--index-url https://APPROVED_INTERNAL_MIRROR/simple`을 쓴다. 주소는 사용자 제공이다. 공개 PyPI/download로 자동 fallback하지 않는다. C의 `--require-hashes`와 lock export를 유지한다. DL requirements.lock에는 wheel hash가 없으므로 조직 승인 wheel inventory/hash 검증을 설치 전에 별도로 수행한다. 원본 package 설치는 build-tool wheel을 준비해 `uv pip install --no-deps --no-build-isolation`로 할 수 있으나 archive 무결성을 유지하려면 다음 `PYTHONPATH`로 실행해도 된다.

```bash
COMPARE_PYTHON="$C" DL1_PYTHON="$D1" DL2_PYTHON="$D2" \
  bash "$BUNDLE/particle-sim-compare/scripts/validate_offline.sh" "$BUNDLE" --out "$RUN/offline-validation"
```

출력: `offline-validation/validation.json`, numeric demo/JSON/CSV/HTML/NPZ/PNG, native search/index/ROI JSON, joined JSON. 실패 단계는 명시되며 실패한 기록을 인수 성공으로 바꾸지 않는다.

## 2. 입력 검증과 numeric 비교

`numeric_config_v1` YAML, reference/target numeric_v2, pair table, density, 실제 frame/valid/original asset manifest를 준비한다. real adapter G2는 아직 승인되지 않았다. 현재 synthetic 경로로 실측 완료를 주장하지 않는다. demo는 기능 확인에만 사용한다.

```bash
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare demo-data --out "$RUN/demo"
export NUMERIC_CONFIG=$RUN/demo/config.yaml
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare validate-data --config "$NUMERIC_CONFIG" --out "$RUN/validation"
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare compare --config "$NUMERIC_CONFIG" --out "$RUN/comparison"
```

출력: validation JSON, `comparison/comparison.json`, table CSV, HTML. line/pixel·failure pieces·shared buckets·transfer·original case CA·coverage·unit/distribution/basis가 보존된다. G1/G2 미확정 실측 weighted/panel/delta는 null+reason이며 missing은 0이 아니다.

## 3. 단일 제품 전극 수 지도

config의 `electrode_map.product`는 제품 하나를 명시한다. 제품을 겹치지 않는다. 기본값은 영역 선택 없이 count map이며 summary는 layer별 per-pixel 최대다.

```bash
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare electrode-map --config "$NUMERIC_CONFIG" --out "$RUN/map"
```

출력: `map/electrode_map.json`, `.npz` count/valid 배열, layer/summary PNG와 physical extent sidecar. 수치 배열을 JSON/CSV 셀별 행으로 늘리지 않는다. 사용자가 영역을 원하는 경우에만 별도 디렉터리에서 `--min-count USER_INTEGER`를 넣는다. threshold는 실제 사용자가 선택한다.

## 4. 사용자 위치와 명시적 map 영역 ROI 내보내기

`roi_export` 설정에 asset manifest와 `manual`의 product/design/case/frame/centre를 완전히 채운다. 원본 shape/CNT/valid hash가 필요하다. case가 없는 점은 blocked이며 자동 배정하지 않는다. map 영역은 config의 명시적인 min_count와 `map_region_ids`가 있을 때만 요청된다. crop 크기는 export가 추측하지 않는다.

```bash
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare export-review-rois --config "$NUMERIC_CONFIG" --out "$RUN/roi"
```

출력: `roi/review_rois.json`. manual이 기본 출처이고 multi-case region은 roi_id가 다른 행들로 남는다. blocked 행은 DL2 index/search에 들어가지 않는다. DL2 crop 설정은 다음 단계에서 사용자가 제공한다. C source root를 `$SOURCE_ROOT`에, original asset manifest 상대 경로를 `$ASSET_MANIFEST`에 지정한다.

## 5. DL1 validate / train / search / evaluate

사용자 제공: 원본 unit/element/CNT/VIA manifest, candidate_grid, minimum line width 근거, lineage-safe split, relation label(미확정 G6는 unavailable 유지), epochs/batch/lr/seed, query_id. hotspot/CA를 입력 shape로 쓰지 않는다. grid를 임의로 덮어쓰지 않는다.

```bash
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.data "$DL1_MANIFEST" --report "$RUN/dl1_input_validation.json"
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.train --manifest "$DL1_MANIFEST" --split "$DL1_SPLIT" --relations "$DL1_RELATIONS" --epochs "$EPOCHS" --batch-size "$BATCH" --lr "$LR" --seed "$SEED" --backbone tiny --device cpu --out "$RUN/dl1.pt"
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.search --ckpt "$RUN/dl1.pt" --query-manifest "$DL1_QUERY_MANIFEST" --candidates-manifest "$DL1_MANIFEST" --query "$QUERY_PRODUCT" --topk "$TOPK" --out "$RUN/dl1_search.json"
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.evaluate --ckpt "$RUN/dl1.pt" --manifest "$DL1_MANIFEST" --split "$DL1_SPLIT" --relations "$DL1_RELATIONS" --topk "$TOPK" --out "$RUN/dl1_evaluation.json"
```

출력: 입력 grid/coverage 증거(stdout JSON), checkpoint, 제품 score/rank/exclusions/grid/fingerprint, 평가 JSON. 실제 label/target를 정하기 전에는 성능 합격을 선언하지 않는다. 공개 ConvNeXt V2는 검증·licence 확인 전 사용하지 않는다. `tiny`는 로컬 초기화이며 weights download가 없다.

## 6. DL2 validate / train / index / search / evaluate

사용자 제공: 모든 real near/wide sizes, pitches, jitter, core, intermediate-layer policy, max_pair_attempts를 명시한 config; product lineage와 ROI split; 선택 ROI id; DL1 후보 제품 cohort JSON; G6 label/acceptance targets. synthetic config는 실측 default가 아니다.

```bash
PYTHONPATH="$BUNDLE/particle-sim-dl2/src" "$D2" -m psdl2.data "$RUN/roi/review_rois.json" --assets-root "$SOURCE_ROOT"
PYTHONPATH="$BUNDLE/particle-sim-dl2/src" "$D2" -m psdl2.train "$RUN/roi/review_rois.json" --assets-root "$SOURCE_ROOT" --config "$DL2_CONFIG" --lineages "$DL2_LINEAGES" --split "$DL2_SPLIT" --out "$RUN/dl2.pt"
PYTHONPATH="$BUNDLE/particle-sim-dl2/src" "$D2" -m psdl2.index "$RUN/roi/review_rois.json" --assets-root "$SOURCE_ROOT" --checkpoint "$RUN/dl2.pt" --out "$RUN/dl2_index.json"
PYTHONPATH="$BUNDLE/particle-sim-dl2/src" "$D2" -m psdl2.search "$RUN/roi/review_rois.json" --assets-root "$SOURCE_ROOT" --roi-id "$QUERY_ROI" --checkpoint "$RUN/dl2.pt" --index "$RUN/dl2_index.json" --cohort "$COHORT_JSON" --topk "$TOPK" --expand --out "$RUN/dl2_search.json"
"$D2" -c 'import json,sys; json.dump([json.load(open(sys.argv[1]))],open(sys.argv[2],"w"))' "$RUN/dl2_search.json" "$RUN/dl2_search_results.json"
PYTHONPATH="$BUNDLE/particle-sim-dl2/src" "$D2" -m psdl2.evaluate "$RUN/dl2_search_results.json" --k "$TOPK" --split "$DL2_SPLIT" --partition test > "$RUN/dl2_evaluation.json"
```

출력: import/preflight JSON、checkpoint、index fingerprints/coverage/source identity、search primary/supplementary/no-match、평가 JSON. label이 제공되면 train/evaluate에 승인된 `--labels`를 추가한다. label 부재시 평가 unavailable을 그대로 기록한다. 검색 hit는 원인·risk·좌표 정합 결과가 아니다.

## 7. C에서 원본 수치 옆에 읽기

결과를 `$RUN` 아래에 모으고 승인한 DL1 config fingerprint를 `$DL1_FINGERPRINT`에 넣는다. DL2 index에서 승인 checkpoint/view fingerprint와 schema를 확인한 후 아래로 JSON을 추출한다. DL1 bindings는 원본 source 연결을 확인하여 [README.md](README.md)의 필드를 작성한다; 명시하지 않으면 제품 score만 읽고 수치 연결은 unavailable이다.

```bash
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare.dl_results context "$SOURCE_ROOT" "$RUN/roi/review_rois.json" --asset-manifest "$ASSET_MANIFEST" --out "$RUN/numeric_context.json"
"$C" -c 'import json,sys; x=json.load(open(sys.argv[1])); json.dump({k:x[k] for k in ("schema_version","checkpoint_fingerprint","view_fingerprint")},open(sys.argv[2],"w"))' "$RUN/dl2_index.json" "$RUN/dl2_expected.json"
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare.dl_results seal --context "$RUN/numeric_context.json" --dl1 "$RUN/dl1_search.json" --dl1-fingerprint "$DL1_FINGERPRINT" --dl1-bindings "$RUN/dl1_bindings.json" --dl2 "$RUN/dl2_search.json" --dl2-index "$RUN/dl2_index.json" --dl2-expected "$RUN/dl2_expected.json" --pending-gates "$RUN/pending-gates.json" --out "$RUN/exchange.json"
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare.dl_results read "$RUN/exchange.json" --out "$RUN/joined.json"
```

`pending-gates.json`는 unresolved run 항목의 문자열 배열이다. seal은 sidecar를 생성하며 native JSON을 고치지 않는다. `joined.json`은 DL score/scope/coverage와 기존 numeric 원본 값을 나란히 담는다. hash/schema/fingerprint/frame/source 불일치는 exit 2다. 결과가 없으면 해당 `--dl1`/`--dl2`를 생략하여 explicit unavailable을 기록한다. read가 error이면 joined 파일은 남지 않고 원래 numeric report는 독립적으로 유지된다. 실제 인수 기록은 빈 template 사본을 사용자 측에서 채운다.
