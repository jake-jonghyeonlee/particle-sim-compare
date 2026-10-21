# export-template-rois — hotspot를 열지 않는 단일 제품 template ROI export

`simcompare export-template-rois --config <yaml> --out <새 폴더>`

한 제품의 case 폴더에 있는 **1.png/2.png(처리 마스크)만** 읽어 `review_roi_v1` ROI와 ROI별 `mask_derivation`을 만든다.
hotspot 이미지·CSV·CA 배열은 열지 않는다. 기존 `import-hotspots` → `import-electrode-assets` → `export-review-rois`를
같은 함수로 호출한 결과이며, 기존 명령과 출력 형식은 바뀌지 않는다(추가 명령이다).

## 입력 트리와 hotspot 거부

- `data_dir`: `<case>/1.png`, `<case>/2.png`(또는 `<case>/images/1.png`, `2.png`)만 둔다. 폴더 이름은 `<불량>-<전극A>-<layerA>-<전극B>-<layerB>`.
- 트리에서 파일 이름만 훑어(열지 않음) `hotspot_<불량><지름>.png` 패턴 파일이나 `result/` 폴더가 하나라도 보이면
  `template_hotspot_input_present`로 종료 코드 2, 아무것도 만들지 않는다.
- CNT는 `cnt_from_dl1_root`의 `<product_id>/cno/*.png` 합집합 1장을 제품의 모든 case가 공유한다. VIA는 항상 `[]`.

## 설정 `template_export_config_v1` (YAML)

모든 값은 필수이고 기본값이 없다. 상대 경로는 설정 파일 위치 기준이다. 알 수 없는 필드는 오류다.

```yaml
schema_version: template_export_config_v1
data_dir: input/hotspot/MODEL          # 1.png/2.png만 있는 트리
cnt_from_dl1_root: input/dl1           # <root>/<product_id>/cno/*.png
product_id: PRODUCT
design_version: DESIGN
source_id: SOURCE
process_step_id: STEP
exposure_id: EXPOSURE
unit_id: UNIT
layer_stack:                           # case의 모든 endpoint layer가 있어야 한다. density 값은 받지 않는다
  - {actual_layer: M1, stack_order: 1}
  - {actual_layer: M2, stack_order: 2}
  - {actual_layer: M3, stack_order: 3}
electrode_role_policy: electrode_id    # 또는 electrode_roles: {A: gate, ...} (둘 중 정확히 하나)
pair_table_path: pairs.csv             # electrode_role_policy: electrode_id일 때 필수
source_version: layout_v1
max_asset_memory_mb: 16384
mask_derivation_rule: between_stack_order_v1   # 이 값만 허용. 생략하면 종료 코드 2
rois:                                  # case마다 위치(µm, frame 안)
  - {case_id: vertical_short-A-M1-C-M3, center_xy_um: [50.5, 50.5]}
```

## mask_derivation 규칙 `between_stack_order_v1`

- vertical: `processed_endpoint` = `analysis_layer`(stack_order가 큰 쪽 layer)와 같은 layer의 endpoint `electrode_id`.
  `removed_layers` = `layer_stack`에서 stack_order가 두 endpoint **사이(양 끝 제외)**인 layer 전부(이름순 정렬, 중복 없음). 사이가 없으면 `[]`.
- lateral(및 그 외): `processed_endpoint: null`, `removed_layers: []` (알려진 빈 값).
- `layer_stack`에 모든 layer가 있다는 것은 사용자 전제다. endpoint layer가 없으면 종료 코드 2.
- 항목은 ready ROI의 case마다 하나다(같은 case의 ROI가 여럿이어도 한 항목, blocked ROI는 제외).
- 항목의 `endpoints`는 `(electrode_id, actual_layer)` 순으로 정렬해서 쓴다. endpoint 순서만 다른 case 폴더(예: `…-A-M1-C-M3`과 `…-C-M3-A-M1`)는 한 항목으로 합쳐진다.
  이 순서는 `review_rois.json` 행의 endpoint 순서와 **다를 수 있으므로** 소비자는 순서·위치로 짝짓지 말고 failure_type과 endpoint 집합으로 대응시켜야 한다(DL2 `load_dataset`이 그렇게 한다).
  `processed_endpoint`는 `electrode_id`로 지정하므로 순서와 무관하다.

## 출력 (`--out`은 새 경로, 임시 폴더에서 만든 뒤 `os.replace`)

| 경로 | 내용 |
|---|---|
| `data/` | import 결과 + electrode asset 어댑터 결과를 합친 폴더(`manifest.json`, `review_assets.json`, `electrode_asset_import_record.json`, `assets/*.png`, `pairs.csv`) |
| `roi/review_rois.json` | `review_roi_v1`. asset은 복사하지 않고 `assets_root`(= 게시된 `data/` 절대 경로)로 참조. DL2는 `assets_root` 옵션으로 옮길 수 있다 |
| `roi/mask_derivation.json` | `psdl2_mask_derivation_v1`: `{schema_version, rule_version, cases: [{case: {failure_type, endpoints: [{electrode_id, role, actual_layer} x2]}, processed_endpoint, removed_layers}]}` |
| `template_export_execution.json` | 입력 hash, 읽은 파일 목록, 출력 hash, ROI 수 |

처리 마스크임을 나타내는 표시는 sim-compare가 알 수 없으므로 출력에 없다. 1.png/2.png가 처리 마스크라는 사실은 사용자 전제다.

## 종료 코드

| 코드 | 의미 |
|---|---|
| 0 | 완료 |
| 3 | 일부 ROI가 blocked(위치가 frame 밖, 알 수 없는 case 등). 출력은 게시되고 `mask_derivation.json`은 ready ROI만 담는다 |
| 2 | 입력·설정 오류: 설정 오류, `mask_derivation_rule` 없음/알 수 없음, `template_hotspot_input_present`, `template_layer_missing_from_stack`, 이미 있는 `--out`. 오류 메시지는 stderr(`오류: <코드>: …`) |

실패해도 이미 있던 `--out`과 입력 트리는 건드리지 않고, 이 실행이 만든 임시 폴더와 빈 상위 폴더만 지운다.

## DL2 왕복 시험과 `PSDL2_ROOT`

`tests/test_template_export.py`의 DL2 왕복 시험은 이 출력을 DL2(`particle-sim-dl2`) 저장소의 `.venv`로 `load_export`에 읽혀 본다. DL2 checkout 위치는 환경 변수 `PSDL2_ROOT`로 지정한다
(기본값은 개발 Mac의 경로). 그 경로에 checkout과 `.venv`가 없으면 시험은 실패하지 않고 건너뛴다(skip 메시지에 `PSDL2_ROOT`가 나온다).
폐쇄망 서버나 CI에서는 `PSDL2_ROOT=<DL2 checkout>`을 지정하지 않으면 이 시험이 돌지 않는다.

## 한계

- 코드가 hotspot 미접촉을 보장하는 범위는 이름 검사(패턴, `result/`)와 기존 import가 읽는 파일 종류(1.png/2.png, DL1 `cno` PNG, 이 실행이 만든 파일)다. 테스트가 `open`/`np.load`/`Image.open` 계측으로 확인한다.
- asset은 1.png/2.png의 중앙 crop이다(바깥 50 µm 테두리는 export되지 않음). 이는 기존 `import-electrode-assets`의 제한과 같다.
- valid 영역은 항상 전부 1이다(기존 real 경로와 같음).
