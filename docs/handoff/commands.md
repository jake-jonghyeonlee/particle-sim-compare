# 폐쇄망 실행 순서

폐쇄망 호스트는 **Windows**이며 실제로 실행한 플랫폼은 **macOS뿐**이다. 아래 PowerShell 명령은 PowerShell 7.3+의 Windows용 미실행 안내다. Windows PowerShell 5.1 대신 PowerShell 7.3+를 사용한다 (Python `-c`의 따옴표 전달). `.sh` 도구는 Windows에서도 Git Bash의 `bash`가 PATH에 있어야 하며 native PowerShell script가 아니다. Windows Python은 `Scripts/python.exe`를 사용한다. Python 모듈 인자와 입력 계약은 두 플랫폼에서 같다.

아래 변수는 사용자가 실제 경로와 승인한 설정으로 채운다. 모든 출력은 bundle 밖의 `$RUN`에 둔다. sample 성능값이나 실측 crop 기본값을 제공하지 않는다.

```bash
export PYTHONDONTWRITEBYTECODE=1
export BUNDLE=/approved-output/bundle
export RUN=/approved-output/user-run
export ENVS=/approved-envs
export C=$ENVS/compare/bin/python
export D1=$ENVS/dl1/bin/python
export D2=$ENVS/dl2/bin/python
# 아래는 실제 사용자 설정으로 채운다 (실측용 기본값 없음).
export APPROVED_MANIFEST_SHA256=USER_APPROVED_HASH
export SOURCE_ROOT=USER_NUMERIC_SOURCE_ROOT
export ASSET_MANIFEST=USER_RELATIVE_ORIGINAL_ASSET_MANIFEST
export NUMERIC_CONFIG=USER_NUMERIC_CONFIG
export DL1_MANIFEST=$RUN/dl1-input/manifest.json
export DL1_PNG_ROOT=USER_LAYOUT_CNT_PNG_ROOT
export DL1_METADATA=USER_LINEAGE_DESIGN_METADATA
export DL1_GRID=USER_APPROVED_CANDIDATE_GRID
export DL1_GRID_CANDIDATES="USER_GRID_1 USER_GRID_2"
export DL1_QUERY_MANIFEST=$RUN/dl1-query-input/manifest.json
export DL1_QUERY_PNG_ROOT=USER_QUERY_LAYOUT_CNT_PNG_ROOT
export DL1_QUERY_METADATA=USER_QUERY_LINEAGE_DESIGN_METADATA
export DL1_SPLIT=USER_LINEAGE_SAFE_SPLIT
export DL1_RELATIONS="" # G6 pending; 승인한 relations 경로가 있으면 채운다
export DL1_FINGERPRINT=USER_APPROVED_CONFIG_FINGERPRINT
export DL2_CONFIG=USER_FULL_DL2_CONFIG
export DL2_LINEAGES=USER_PRODUCT_LINEAGES
export DL2_SPLIT=USER_ROI_SPLIT
export QUERY_PRODUCT=USER_QUERY_PRODUCT
export QUERY_ROI=USER_QUERY_ROI
export COHORT_JSON=USER_COHORT_FILE
export TOPK=USER_INTEGER
export EPOCHS=USER_EPOCHS
export BATCH=USER_BATCH_SIZE
export LR=USER_LEARNING_RATE
export SEED=USER_SEED
mkdir -p "$RUN"
bash "$BUNDLE/particle-sim-compare/scripts/check_handoff.sh" "$BUNDLE" --expect-manifest-sha256 "$APPROVED_MANIFEST_SHA256"
```

Windows PowerShell (미실행):

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$BUNDLE = "D:/approved-output/bundle"
$RUN = "D:/approved-output/user-run"
$ENVS = "D:/approved-envs"
$C = "$ENVS/compare/Scripts/python.exe"
$D1 = "$ENVS/dl1/Scripts/python.exe"
$D2 = "$ENVS/dl2/Scripts/python.exe"
# 아래는 실제 사용자 설정으로 채운다 (실측용 기본값 없음).
$APPROVED_MANIFEST_SHA256 = "USER_APPROVED_HASH"
$SOURCE_ROOT = "USER_NUMERIC_SOURCE_ROOT"
$ASSET_MANIFEST = "USER_RELATIVE_ORIGINAL_ASSET_MANIFEST"
$NUMERIC_CONFIG = "USER_NUMERIC_CONFIG"
$DL1_MANIFEST = "$RUN/dl1-input/manifest.json"
$DL1_PNG_ROOT = "USER_LAYOUT_CNT_PNG_ROOT"
$DL1_METADATA = "USER_LINEAGE_DESIGN_METADATA"
$DL1_GRID = "USER_APPROVED_CANDIDATE_GRID"
$DL1_GRID_CANDIDATES = "USER_GRID_1 USER_GRID_2"
$DL1_QUERY_MANIFEST = "$RUN/dl1-query-input/manifest.json"
$DL1_QUERY_PNG_ROOT = "USER_QUERY_LAYOUT_CNT_PNG_ROOT"
$DL1_QUERY_METADATA = "USER_QUERY_LINEAGE_DESIGN_METADATA"
$DL1_SPLIT = "USER_LINEAGE_SAFE_SPLIT"
$DL1_RELATIONS = "" # G6 pending; 승인한 relations 경로가 있으면 채운다
$DL1_FINGERPRINT = "USER_APPROVED_CONFIG_FINGERPRINT"
$DL2_CONFIG = "USER_FULL_DL2_CONFIG"
$DL2_LINEAGES = "USER_PRODUCT_LINEAGES"
$DL2_SPLIT = "USER_ROI_SPLIT"
$QUERY_PRODUCT = "USER_QUERY_PRODUCT"
$QUERY_ROI = "USER_QUERY_ROI"
$COHORT_JSON = "USER_COHORT_FILE"
$TOPK = "USER_INTEGER"
$EPOCHS = "USER_EPOCHS"
$BATCH = "USER_BATCH_SIZE"
$LR = "USER_LEARNING_RATE"
$SEED = "USER_SEED"
New-Item -ItemType Directory -Force -Path $RUN | Out-Null
bash "$BUNDLE/particle-sim-compare/scripts/check_handoff.sh" "$BUNDLE" --expect-manifest-sha256 "$APPROVED_MANIFEST_SHA256"
```

build/check는 manifest_sha256을 출력한다. 승인된 별도 전달 값과 대조하며 검사할 manifest 자신을 기대 값으로 사용하지 않는다. build는 python3 >=3.11.4가 필요하다. 이전 scripts/1_install.sh…5_check_particle_sim_run.sh를 bundle 안에서 실행하지 않는다.

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

Windows PowerShell (미실행):

```powershell
py -3.12 -m venv "$ENVS/compare"
py -3.12 -m venv "$ENVS/dl1"
py -3.12 -m venv "$ENVS/dl2"
uv export --frozen --project "$BUNDLE/particle-sim-compare" --no-dev --no-emit-project --format requirements-txt --output-file "$RUN/compare.requirements.txt"
Copy-Item "$BUNDLE/particle-sim-dl1/requirements.lock" "$RUN/dl1.requirements.txt"
Copy-Item "$BUNDLE/particle-sim-dl2/requirements.lock" "$RUN/dl2.requirements.txt"
uv pip install --python "$C" --no-index --find-links /approved-wheels --require-hashes -r "$RUN/compare.requirements.txt"
uv pip install --python "$D1" --no-index --find-links /approved-wheels -r "$RUN/dl1.requirements.txt"
uv pip install --python "$D2" --no-index --find-links /approved-wheels -r "$RUN/dl2.requirements.txt"
```

허용된 내부 PyPI mirror를 사용하는 설치망이라면 세 `uv pip install`에서 `--no-index --find-links /approved-wheels` 대신 `--index-url https://APPROVED_INTERNAL_MIRROR/simple`을 쓴다. 주소는 사용자 제공이다. 공개 PyPI/download로 자동 fallback하지 않는다. C의 `--require-hashes`와 lock export를 유지한다. DL requirements.lock에는 wheel hash가 없으므로 조직 승인 wheel inventory/hash 검증을 설치 전에 별도로 수행한다. 원본 package 설치는 build-tool wheel을 준비해 `uv pip install --no-deps --no-build-isolation`로 할 수 있으나 archive 무결성을 유지하려면 다음 `PYTHONPATH`로 실행해도 된다.

```bash
COMPARE_PYTHON="$C" DL1_PYTHON="$D1" DL2_PYTHON="$D2" \
  bash "$BUNDLE/particle-sim-compare/scripts/validate_offline.sh" "$BUNDLE" --out "$RUN/offline-validation" --expect-manifest-sha256 "$APPROVED_MANIFEST_SHA256"
```

Windows PowerShell (미실행):

```powershell
$env:COMPARE_PYTHON = $C; $env:DL1_PYTHON = $D1; $env:DL2_PYTHON = $D2
bash "$BUNDLE/particle-sim-compare/scripts/validate_offline.sh" "$BUNDLE" --out "$RUN/offline-validation" --expect-manifest-sha256 "$APPROVED_MANIFEST_SHA256"
```

출력: `offline-validation/validation.json`, numeric demo/JSON/CSV/HTML/NPZ/PNG, native search/index/ROI JSON, joined JSON. 실패 단계는 명시되며 실패한 기록을 인수 성공으로 바꾸지 않는다.

## 2. 입력 검증과 numeric 비교

`numeric_config_v1` YAML, reference/target numeric_v2, pair table, density, 실제 frame/valid/original asset manifest를 준비한다. real adapter G2는 아직 승인되지 않았다. 현재 synthetic 경로로 실측 완료를 주장하지 않는다. demo는 기능 확인에만 사용한다.

```bash
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare demo-data --out "$RUN/demo"
export DEMO_NUMERIC_CONFIG=$RUN/demo/config.yaml
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare validate-data --config "$DEMO_NUMERIC_CONFIG" --out "$RUN/validation"
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare compare --config "$DEMO_NUMERIC_CONFIG" --out "$RUN/comparison"
```

Windows PowerShell (미실행):

```powershell
$env:PYTHONPATH = "$BUNDLE/particle-sim-compare/src"
& "$C" -m simcompare demo-data --out "$RUN/demo"
$DEMO_NUMERIC_CONFIG = "$RUN/demo/config.yaml"
$env:PYTHONPATH = "$BUNDLE/particle-sim-compare/src"
& "$C" -m simcompare validate-data --config "$DEMO_NUMERIC_CONFIG" --out "$RUN/validation"
$env:PYTHONPATH = "$BUNDLE/particle-sim-compare/src"
& "$C" -m simcompare compare --config "$DEMO_NUMERIC_CONFIG" --out "$RUN/comparison"
```

출력: validation JSON, `comparison/comparison.json`, table CSV, HTML. line/pixel·failure pieces·shared buckets·transfer·original case CA·coverage·unit/distribution/basis가 보존된다. G1/G2 미확정 실측 weighted/panel/delta는 null+reason이며 missing은 0이 아니다.

demo validate-data는 pending 계약을 보여 주므로 정상적으로 exit 3 (partial)이다. demo config는 manual 선택이 비어 있어 ROI export가 zero ROIs를 낸다. 4~7절은 사용자의 완전한 source/case/frame/asset 문맥 또는 tests/fixtures/handoff의 별도 synthetic 교환 fixture를 준비한 후 실행한다.

## 3. 단일 제품 전극 수 지도

config의 `electrode_map.product`는 제품 하나를 명시한다. 제품을 겹치지 않는다. 기본값은 영역 선택 없이 count map이며 summary는 layer별 per-pixel 최대다.

```bash
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare electrode-map --config "$NUMERIC_CONFIG" --out "$RUN/map"
```

Windows PowerShell (미실행):

```powershell
$env:PYTHONPATH = "$BUNDLE/particle-sim-compare/src"
& "$C" -m simcompare electrode-map --config "$NUMERIC_CONFIG" --out "$RUN/map"
```

출력: `map/electrode_map.json`, `.npz` count/valid 배열, layer/summary PNG와 physical extent sidecar. 수치 배열을 JSON/CSV 셀별 행으로 늘리지 않는다. 사용자가 영역을 원하는 경우에만 별도 디렉터리에서 `--min-count USER_INTEGER`를 넣는다. threshold는 실제 사용자가 선택한다.

## 4. 사용자 위치와 명시적 map 영역 ROI 내보내기

`roi_export` 설정에 asset manifest와 `manual`의 product/design/case/frame/centre를 완전히 채운다. 원본 shape/CNT/valid hash가 필요하다. case가 없는 점은 blocked이며 자동 배정하지 않는다. map 영역은 config의 명시적인 min_count와 `map_region_ids`가 있을 때만 요청된다. crop 크기는 export가 추측하지 않는다.

```bash
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare export-review-rois --config "$NUMERIC_CONFIG" --out "$RUN/roi"
```

Windows PowerShell (미실행):

```powershell
$env:PYTHONPATH = "$BUNDLE/particle-sim-compare/src"
& "$C" -m simcompare export-review-rois --config "$NUMERIC_CONFIG" --out "$RUN/roi"
```

출력: `roi/review_rois.json`. manual이 기본 출처이고 multi-case region은 roi_id가 다른 행들로 남는다. blocked 행은 DL2 index/search에 들어가지 않는다. DL2 crop 설정은 다음 단계에서 사용자가 제공한다. C source root를 `$SOURCE_ROOT`에, original asset manifest 상대 경로를 `$ASSET_MANIFEST`에 지정한다.

## 5. DL1 validate / train / search / evaluate

DL1은 main에 merge되지 않은 `feat/png-input` @ `e67a8b2a3719d7141472a32503d00f52bbdf625c`를 사용한다. 상세 입력 규칙은 bundle의 `particle-sim-dl1/docs/USAGE.md`다. manifest를 손으로 작성하지 않고 아래 importer로 생성한다.

사용자 제공: 원본 layout/electrode·CNT PNG 폴더, `dl1_png_metadata_v1` 계보/설계 metadata (모델별 lineage_id/design_version), candidate_grid, minimum line width 근거, lineage-safe split, relation label(미확정 G6는 unavailable 유지), epochs/batch/lr/seed, query_id. hotspot/CA를 입력 shape로 쓰지 않는다. grid를 임의로 덮어쓰지 않는다.

layout/electrode·CNT PNG = **BLACK pattern on WHITE background** (DL1 importer); hotspot PNG = **WHITE pattern on BLACK background** (compare, `value_semantics: binary`). DL1은 0.1 µm/pixel, 모델 내부 같은 크기, 각 변 >1000 pixels인 전체 원본 PNG를 읽는다. `<모델>/<전극>/<순번>-<layer>.png`와 `<모델>/cno/<순번>_<layer>.png` 구조를 준비한다. crop/2×2 unit 환산을 하지 않는다. grid 검사 후보는 사용자가 준다.

```bash
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.import_png --root "$DL1_PNG_ROOT" --out "$RUN/dl1-inspect"
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.import_png --root "$DL1_PNG_ROOT" --out "$RUN/dl1-grids" --inspect-grids $DL1_GRID_CANDIDATES
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.import_png --root "$DL1_PNG_ROOT" --out "$RUN/dl1-input" --export --metadata "$DL1_METADATA" --candidate-grid "$DL1_GRID"
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.import_png --verify-bundle "$RUN/dl1-input"
# 독립 query 폴더도 같은 grid로 검사/내보내고 검증한다.
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.import_png --root "$DL1_QUERY_PNG_ROOT" --out "$RUN/dl1-query-inspect"
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.import_png --root "$DL1_QUERY_PNG_ROOT" --out "$RUN/dl1-query-grids" --inspect-grids $DL1_GRID_CANDIDATES
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.import_png --root "$DL1_QUERY_PNG_ROOT" --out "$RUN/dl1-query-input" --export --metadata "$DL1_QUERY_METADATA" --candidate-grid "$DL1_GRID"
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.import_png --verify-bundle "$RUN/dl1-query-input"
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.data "$DL1_MANIFEST" --report "$RUN/dl1_input_validation.json"
DL1_RELATION_ARGS=() # 기본값에서 unknown을 negative로 바꾸지 않는다
# DL1_RELATION_ARGS=(--unlabelled-negatives) # 사용자가 명시적으로 선택할 때만
if [[ -n "$DL1_RELATIONS" ]]; then DL1_RELATION_ARGS=(--relations "$DL1_RELATIONS"); fi
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.train --manifest "$DL1_MANIFEST" --split "$DL1_SPLIT" "${DL1_RELATION_ARGS[@]}" --epochs "$EPOCHS" --batch-size "$BATCH" --lr "$LR" --seed "$SEED" --backbone tiny --device cpu --out "$RUN/dl1.pt"
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.search --ckpt "$RUN/dl1.pt" --query-manifest "$DL1_QUERY_MANIFEST" --candidates-manifest "$DL1_MANIFEST" --query "$QUERY_PRODUCT" --topk "$TOPK" --out "$RUN/dl1_search.json"
# 다음 평가는 승인 relations가 있을 때만 실행 (G6 pending이면 실행 보류).
if [[ -n "$DL1_RELATIONS" ]]; then
PYTHONPATH="$BUNDLE/particle-sim-dl1/src" "$D1" -m psdl1.evaluate --ckpt "$RUN/dl1.pt" --manifest "$DL1_MANIFEST" --split "$DL1_SPLIT" --relations "$DL1_RELATIONS" --topk "$TOPK" --out "$RUN/dl1_evaluation.json"
fi
```

Windows PowerShell (미실행):

```powershell
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl1/src"
& "$D1" -m psdl1.import_png --root "$DL1_PNG_ROOT" --out "$RUN/dl1-inspect"
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl1/src"
& "$D1" -m psdl1.import_png --root "$DL1_PNG_ROOT" --out "$RUN/dl1-grids" --inspect-grids ($DL1_GRID_CANDIDATES -split " ")
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl1/src"
& "$D1" -m psdl1.import_png --root "$DL1_PNG_ROOT" --out "$RUN/dl1-input" --export --metadata "$DL1_METADATA" --candidate-grid "$DL1_GRID"
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl1/src"
& "$D1" -m psdl1.import_png --verify-bundle "$RUN/dl1-input"
# 독립 query 폴더도 같은 grid로 검사/내보내고 검증한다.
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl1/src"
& "$D1" -m psdl1.import_png --root "$DL1_QUERY_PNG_ROOT" --out "$RUN/dl1-query-inspect"
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl1/src"
& "$D1" -m psdl1.import_png --root "$DL1_QUERY_PNG_ROOT" --out "$RUN/dl1-query-grids" --inspect-grids ($DL1_GRID_CANDIDATES -split " ")
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl1/src"
& "$D1" -m psdl1.import_png --root "$DL1_QUERY_PNG_ROOT" --out "$RUN/dl1-query-input" --export --metadata "$DL1_QUERY_METADATA" --candidate-grid "$DL1_GRID"
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl1/src"
& "$D1" -m psdl1.import_png --verify-bundle "$RUN/dl1-query-input"
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl1/src"
& "$D1" -m psdl1.data "$DL1_MANIFEST" --report "$RUN/dl1_input_validation.json"
$DL1_RELATION_ARGS = @() # unknown을 negative로 바꾸지 않는다
# $DL1_RELATION_ARGS = @("--unlabelled-negatives") # 명시적으로 선택할 때만
if ($DL1_RELATIONS) { $DL1_RELATION_ARGS = @("--relations", $DL1_RELATIONS) }
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl1/src"
& "$D1" -m psdl1.train --manifest "$DL1_MANIFEST" --split "$DL1_SPLIT" @DL1_RELATION_ARGS --epochs "$EPOCHS" --batch-size "$BATCH" --lr "$LR" --seed "$SEED" --backbone tiny --device cpu --out "$RUN/dl1.pt"
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl1/src"
& "$D1" -m psdl1.search --ckpt "$RUN/dl1.pt" --query-manifest "$DL1_QUERY_MANIFEST" --candidates-manifest "$DL1_MANIFEST" --query "$QUERY_PRODUCT" --topk "$TOPK" --out "$RUN/dl1_search.json"
# 다음 평가는 승인 relations가 있을 때만 실행 (G6 pending이면 실행 보류).
if ($DL1_RELATIONS) {
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl1/src"
& "$D1" -m psdl1.evaluate --ckpt "$RUN/dl1.pt" --manifest "$DL1_MANIFEST" --split "$DL1_SPLIT" --relations "$DL1_RELATIONS" --topk "$TOPK" --out "$RUN/dl1_evaluation.json"
}
```

출력: 입력 grid/coverage 증거(stdout JSON), checkpoint, 제품 score/rank/exclusions/grid/fingerprint, 평가 JSON. 실제 label/target를 정하기 전에는 성능 합격을 선언하지 않는다. 공개 ConvNeXt V2는 검증·licence 확인 전 사용하지 않는다. `tiny`는 로컬 초기화이며 weights download가 없다.

G6 pending에서 --relations를 임의로 만들지 않는다. 승인 relation이 없으면 train에서 --relations를 생략하고 사용자 결정에 따라 --unlabelled-negatives를 명시한다. evaluate는 --relations가 필수이므로 승인 label/relations가 생길 때까지 실행하지 않고 pending으로 기록한다.

## 6. DL2 validate / train / index / search / evaluate

사용자 제공: 모든 real near/wide sizes, pitches, jitter, core, intermediate-layer policy, max_pair_attempts를 명시한 config; product lineage와 ROI split; 선택 ROI id; DL1 후보 제품 cohort JSON; G6 label/acceptance targets. synthetic config는 실측 default가 아니다. DL2 configs/synthetic.example.json은 recipe를 config 키 안에 담으며 현재 read_config는 이 wrapper도 읽는다; config만 추출하려면 아래 명령을 쓴다.

```bash
"$D2" -c 'import json,sys; x=json.load(open(sys.argv[1])); json.dump(x["config"],open(sys.argv[2],"w"))' "$BUNDLE/particle-sim-dl2/configs/synthetic.example.json" "$RUN/dl2_synthetic_recipe.json"
```

Windows PowerShell (미실행):

```powershell
& "$D2" -c 'import json,sys; x=json.load(open(sys.argv[1])); json.dump(x["config"],open(sys.argv[2],"w"))' "$BUNDLE/particle-sim-dl2/configs/synthetic.example.json" "$RUN/dl2_synthetic_recipe.json"
```

```bash
PYTHONPATH="$BUNDLE/particle-sim-dl2/src" "$D2" -m psdl2.data "$RUN/roi/review_rois.json" --assets-root "$SOURCE_ROOT"
PYTHONPATH="$BUNDLE/particle-sim-dl2/src" "$D2" -m psdl2.train "$RUN/roi/review_rois.json" --assets-root "$SOURCE_ROOT" --config "$DL2_CONFIG" --lineages "$DL2_LINEAGES" --split "$DL2_SPLIT" --out "$RUN/dl2.pt" > "$RUN/dl2_train_stdout.json"
PYTHONPATH="$BUNDLE/particle-sim-dl2/src" "$D2" -m psdl2.index "$RUN/roi/review_rois.json" --assets-root "$SOURCE_ROOT" --checkpoint "$RUN/dl2.pt" --out "$RUN/dl2_index.json" > "$RUN/dl2_approved_index_stdout.json"
PYTHONPATH="$BUNDLE/particle-sim-dl2/src" "$D2" -m psdl2.search "$RUN/roi/review_rois.json" --assets-root "$SOURCE_ROOT" --roi-id "$QUERY_ROI" --checkpoint "$RUN/dl2.pt" --index "$RUN/dl2_index.json" --cohort "$COHORT_JSON" --topk "$TOPK" --expand --out "$RUN/dl2_search.json"
"$D2" -c 'import json,sys; json.dump([json.load(open(sys.argv[1]))],open(sys.argv[2],"w"))' "$RUN/dl2_search.json" "$RUN/dl2_search_results.json"
PYTHONPATH="$BUNDLE/particle-sim-dl2/src" "$D2" -m psdl2.evaluate "$RUN/dl2_search_results.json" --k "$TOPK" --split "$DL2_SPLIT" --partition test > "$RUN/dl2_evaluation.json"
```

Windows PowerShell (미실행):

```powershell
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl2/src"
& "$D2" -m psdl2.data "$RUN/roi/review_rois.json" --assets-root "$SOURCE_ROOT"
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl2/src"
& "$D2" -m psdl2.train "$RUN/roi/review_rois.json" --assets-root "$SOURCE_ROOT" --config "$DL2_CONFIG" --lineages "$DL2_LINEAGES" --split "$DL2_SPLIT" --out "$RUN/dl2.pt" > "$RUN/dl2_train_stdout.json"
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl2/src"
& "$D2" -m psdl2.index "$RUN/roi/review_rois.json" --assets-root "$SOURCE_ROOT" --checkpoint "$RUN/dl2.pt" --out "$RUN/dl2_index.json" > "$RUN/dl2_approved_index_stdout.json"
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl2/src"
& "$D2" -m psdl2.search "$RUN/roi/review_rois.json" --assets-root "$SOURCE_ROOT" --roi-id "$QUERY_ROI" --checkpoint "$RUN/dl2.pt" --index "$RUN/dl2_index.json" --cohort "$COHORT_JSON" --topk "$TOPK" --expand --out "$RUN/dl2_search.json"
& "$D2" -c 'import json,sys; json.dump([json.load(open(sys.argv[1]))],open(sys.argv[2],"w"))' "$RUN/dl2_search.json" "$RUN/dl2_search_results.json"
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl2/src"
& "$D2" -m psdl2.evaluate "$RUN/dl2_search_results.json" --k "$TOPK" --split "$DL2_SPLIT" --partition test > "$RUN/dl2_evaluation.json"
```

출력: import/preflight JSON、checkpoint、index fingerprints/coverage/source identity、search primary/supplementary/no-match、평가 JSON. label이 제공되면 train/evaluate에 승인된 `--labels`를 추가한다. label 부재시 평가 unavailable을 그대로 기록한다. 검색 hit는 원인·risk·좌표 정합 결과가 아니다.

## 7. C에서 원본 수치 옆에 읽기

결과를 `$RUN` 아래에 모으고 승인한 DL1 config fingerprint를 `$DL1_FINGERPRINT`에 넣는다. 검사 대상 index와 독립적으로 보관한 승인 checkpoint와 training/index stdout으로 expected 정보를 만든다. DL1 bindings는 원본 source 연결을 확인하여 [README.md](README.md)의 필드를 작성한다; 명시하지 않으면 제품 score만 읽고 수치 연결은 unavailable이다. 이 binding은 사용자 주장에 의존한다: C는 DL1 input_identity와 원본 asset의 실제 대응 또는 ranking에 없는 design_version을 검증하지 못한다. 같은 제품의 다른 case를 잘못 주장해도 자동 판별하지 못하므로 unique binding을 별도로 검토한다.

```bash
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare.dl_results context "$SOURCE_ROOT" "$RUN/roi/review_rois.json" --asset-manifest "$ASSET_MANIFEST" --out "$RUN/numeric_context.json"
PYTHONPATH="$BUNDLE/particle-sim-dl2/src" "$D2" -c 'import json,sys; from psdl2.index import load_encoder,view_config_dict; from psdl2.checkpoint import fingerprint; _,v,fp=load_encoder(sys.argv[1]); approval=json.load(open(sys.argv[2])); assert fp==approval["checkpoint_fingerprint"]; json.dump({"schema_version":"psdl2_index_v2","checkpoint_fingerprint":fp,"view_fingerprint":fingerprint(view_config_dict(v))},open(sys.argv[3],"w"))' "$RUN/dl2.pt" "$RUN/dl2_approved_index_stdout.json" "$RUN/dl2_expected.json"
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare.dl_results seal --context "$RUN/numeric_context.json" --dl1 "$RUN/dl1_search.json" --dl1-fingerprint "$DL1_FINGERPRINT" --dl1-bindings "$RUN/dl1_bindings.json" --dl2 "$RUN/dl2_search.json" --dl2-cohort "$COHORT_JSON" --dl2-index "$RUN/dl2_index.json" --dl2-expected "$RUN/dl2_expected.json" --pending-gates "$RUN/pending-gates.json" --out "$RUN/exchange.json"
PYTHONPATH="$BUNDLE/particle-sim-compare/src" "$C" -m simcompare.dl_results read "$RUN/exchange.json" --out "$RUN/joined.json"
```

Windows PowerShell (미실행):

```powershell
$env:PYTHONPATH = "$BUNDLE/particle-sim-compare/src"
& "$C" -m simcompare.dl_results context "$SOURCE_ROOT" "$RUN/roi/review_rois.json" --asset-manifest "$ASSET_MANIFEST" --out "$RUN/numeric_context.json"
$env:PYTHONPATH = "$BUNDLE/particle-sim-dl2/src"
& "$D2" -c 'import json,sys; from psdl2.index import load_encoder,view_config_dict; from psdl2.checkpoint import fingerprint; _,v,fp=load_encoder(sys.argv[1]); approval=json.load(open(sys.argv[2])); assert fp==approval["checkpoint_fingerprint"]; json.dump({"schema_version":"psdl2_index_v2","checkpoint_fingerprint":fp,"view_fingerprint":fingerprint(view_config_dict(v))},open(sys.argv[3],"w"))' "$RUN/dl2.pt" "$RUN/dl2_approved_index_stdout.json" "$RUN/dl2_expected.json"
$env:PYTHONPATH = "$BUNDLE/particle-sim-compare/src"
& "$C" -m simcompare.dl_results seal --context "$RUN/numeric_context.json" --dl1 "$RUN/dl1_search.json" --dl1-fingerprint "$DL1_FINGERPRINT" --dl1-bindings "$RUN/dl1_bindings.json" --dl2 "$RUN/dl2_search.json" --dl2-cohort "$COHORT_JSON" --dl2-index "$RUN/dl2_index.json" --dl2-expected "$RUN/dl2_expected.json" --pending-gates "$RUN/pending-gates.json" --out "$RUN/exchange.json"
$env:PYTHONPATH = "$BUNDLE/particle-sim-compare/src"
& "$C" -m simcompare.dl_results read "$RUN/exchange.json" --out "$RUN/joined.json"
```

`pending-gates.json`는 unresolved run 항목의 문자열 배열이다. seal은 sidecar를 생성하며 native JSON을 고치지 않는다. `joined.json`은 DL score/scope/coverage와 기존 numeric 원본 값을 나란히 담는다. hash/schema/fingerprint/frame/source 불일치는 exit 2다. 결과가 없으면 해당 `--dl1`/`--dl2`를 생략하여 explicit unavailable을 기록한다. read가 error이면 joined 파일은 남지 않고 원래 numeric report는 독립적으로 유지된다. 실제 인수 기록은 빈 template 사본을 사용자 측에서 채운다.

Expected fingerprint는 검사 대상 index에서 추출하지 않는다. 승인 checkpoint와 training/index stdout을 따로 보관한 후 위 명령으로 checkpoint 측 view/config를 계산한다. Score 검증은 supplied index와의 float64 cosine (6자리 rounding, absolute tolerance 1.5e-6) 일치만 보장한다. 다른 checkpoint가 같은 rounded scores를 내면 원래 producer를 구별할 수 없다. 출력에는 primary와 supplementary 별도 목록, primary_cohort, expanded, coverage가 남고 cohort 파일 hash도 sidecar에 고정된다. 기존 출력 교체는 --force로 명시한다.
