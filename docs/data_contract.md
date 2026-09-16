# 데이터 계약

코드 정의: `src/simcompare/schema.py`. 흐름은 다음과 같다.

```text
원본 파일 → 입력 어댑터(adapters/*) → 공통 구조(AnalysisBundle, FabDistribution) → 전처리(dataset/preprocess) → 모델·기준선·분석
```

모델과 분석 코드는 원본 파일 형식을 모른다. 실제 자료는 어댑터만 새로 작성해 연결한다.

## 1. 분석 묶음 `AnalysisBundle`

한 묶음 = 제품 × 설계 버전 × 전극 역할 조합 × 불량 종류의 크기별 결과.

| 필드 | 필수 | 설명 |
|---|---|---|
| `sample_id` | ✔ | 묶음 고유 ID. 중복 금지 |
| `product_id`, `product_family`, `design_version` | ✔ | 제품·제품군·설계 버전 |
| `design_lineage_id` | ✔ | 원형 설계와 파생 설계를 묶는 ID. 학습/평가 분리 단위 |
| `electrode_a_role`, `layer_a` | ✔ | 첫 전극 역할과 layer |
| `electrode_b_role`, `layer_b` | ✔ | short는 필수. `lateral_open`은 반드시 `NOT_APPLICABLE`(`"N/A"`) — 누락이 아니라 "해당 없음" |
| `failure_type` | ✔ | `lateral_open` \| `lateral_short` \| `vertical_short`. 세 전극 이상은 다루지 않음 |
| `fab_id` | ✔ | Fab ID |
| `fab_distribution_id` | 선택 | 명시하면 해당 분포를 사용하고 fab_id·layer 일치를 검사 |
| `assigned_process_layer` | 조건부 | Fab 분포 매칭용 공정 layer. `vertical_short`는 `layer_stack.csv`에서 상부 layer를 결정하며 입력값과 다르면 오류 |
| `unit_width`, `unit_height`, `physical_length_unit` | 선택 | unit-pixel 실제 크기. 없으면 물리 길이·면적 보정 비교 불가로 기록 |
| `layouts` | 선택 | 전극별 `ImageInput` (open 1개, short 2개; slot a, b 순) |
| `hotspots` | 선택 | 이물 크기별 가공 hotspot `ImageInput` |
| `ca` | 선택 | `CACurve` |
| `geometry` | 선택 | 두께(`thickness`), 형상 수치(`shape_metrics`), 길이 단위 |
| `parent_design_id`, `change_description` | 선택 | 변경 설계 추적 |
| `provenance` | ✔ | 어댑터 이름, 원본 파일, 계산 설정(몬테카를로 등), 계산 버전, 합성 여부 |

`ImageInput`: `array`(2-D float32), `path`, `pixel_size`(픽셀당 실제 길이), `origin_xy`, `particle_size`와 `particle_size_unit`(hotspot의 원형 이물 지름), `value_semantics`(`binary` \| `processed_intensity` \| `probability`), `legend_id`. `probability`는 명시 범례가 있을 때만 허용된다. real 모드에서는 같은 묶음의 layout·hotspot 배열 크기, pixel size, 원점과 실제 영역이 일치해야 한다.

`CACurve`: `particle_sizes`(원형 이물의 지름, 엄격 증가), `particle_size_unit`, `values`(0~1 무차원 누적 위험값, 미관측은 NaN), `observed`(bool), `unit="1"`, `calc_settings`. CA는 면적 정규화된 확률이 아니므로 원 CA와 `CA / unit-pixel 면적`을 함께 보존한다. 후자는 확률이 아니라 `1/um^2` 단위의 위험 지표다.

## 2. Fab 자료 `FabDistribution`

| 필드 | 설명 |
|---|---|
| `fab_distribution_id`, `fab_id`, `product_family`(없으면 null), `process_layer` | 매칭 키 |
| `bin_lower`, `bin_upper` | 크기 구간. 정렬·비중첩·lower < upper |
| `raw_values`, `observed` | 원본 값. 미관측은 NaN/null |
| `value_kind` | `average_count_per_glass` \| `count` \| `probability` \| `rate_per_area` \| `density` — 서로 같은 방식으로 처리하지 않음 |
| `value_unit`, `size_unit` | 필수 |
| `surveyed_area`, `area_unit` | `count`, `average_count_per_glass`는 필수 |
| `period` | 적용 기간 |
| `averaging_basis` | `average_count_per_glass`는 반드시 `per_glass` |
| `is_reference` | 순위 비교용 지정 기준 분포 여부 |

분포의 **모양**과 **총량**은 `caa.fab_shape_and_total`에서 분리한다. `average_count_per_glass`와 `count`의 총량은 실제 조사 면적으로 나눈 뒤 사용하므로 조사 면적 차이가 오염량 차이로 학습되지 않는다.

## 3. CAA

CAA는 CA와 Fab 가중치로 계산하는 **제품 간 비교용 상대 위험 지표**다. 입력이나 정답으로 쓰지 않고, 실제 불량 확률·예상 불량 개수·불량률·수율로 표시하지 않는다.

- 분포 선택(`caa.select_distribution`): `process_layer_overrides["<product_id>|<failure_type>"]` → 없으면 `assigned_process_layer`. 둘 다 없으면 오류.
- 해석(`caa.interpret_fab`): 현재 규칙은 `demo_probability_per_bin` 하나다. `mode: demo` + `value_kind: probability`에서만 동작하고 결과에 `DEMO CAA` 라벨이 붙는다. real 모드와 다른 종류의 값은 오류다.
- 계산(`caa.compute_caa`): 구간 중앙값과 CA 크기가 정확히 일치하는 점만 곱해 합산한다. 보간·구간 변환 없음. 누락 크기는 목록으로 보고하며 `require_full_coverage: true`면 값을 만들지 않는다.

## 4. 예시 어댑터 파일 형식 (`adapters/example.py`)

```text
data_dir/
  bundles.csv       # 묶음 메타데이터 (필수)
  layouts.csv       # sample_id, slot(a|b), path, pixel_size, origin_x, origin_y
  hotspots.csv      # sample_id, particle_size, particle_size_unit, path, pixel_size, origin_x, origin_y
  ca_records.csv    # sample_id, particle_size, particle_size_unit, ca_value, ca_unit(1)
  fab.json          # 기존 공통 분포 형식(선택)
  fab_measurements.csv # glass당 평균 검출 개수 가공자료(선택, 둘 중 하나 필수)
  layer_stack.csv   # 제품별 하부→상부 적층 순서(vertical short에 필요)
  design_review_status.csv # 외부 전기·설계 검토 상태(선택)
  geometry.json     # {sample_id: {"thickness": {...}, "shape_metrics": {...}, "length_unit": "um"}}
  provenance.json   # {"calc_settings": {...}, "calc_version": "...", "is_synthetic": false}
  arrays/*.npy, images/*.png
```

`bundles.csv` 예:

```csv
sample_id,product_id,product_family,design_version,design_lineage_id,electrode_a_role,electrode_b_role,layer_a,layer_b,failure_type,fab_id,fab_distribution_id,assigned_process_layer,unit_width,unit_height,physical_length_unit,parent_design_id,change_description,calc_version
P1-v1-OPEN,P1,F1,v1,L1,data,N/A,M2,N/A,lateral_open,FAB_A,,M2,60,90,um,,,sim-3.2
P1-v2-VSHORT,P1,F1,v2,L1,gate,data,M1,M2,vertical_short,FAB_A,,M2,60,90,um,P1-v1,wider gap,sim-3.2
```

`ca_records.csv`의 크기별 기록은 `schema.group_size_records`로 묶음별 곡선이 된다(중복 크기·단위 혼합은 오류).

## 5. 매칭·검증 규칙

- 모든 부속 파일의 `sample_id`는 `bundles.csv`에 있어야 한다. 모르는 ID는 목록과 함께 오류.
- 경로는 `data_dir` 내부 상대경로만 허용(절대경로·`..` 탈출 오류).
- 빈 셀은 None/NaN. 0으로 대체하지 않는다.
- open에 slot b layout이 있으면 오류.
- 유사 사례 비교는 같은 `failure_type`과 같은 전극 역할 집합(순서 무관)에서만. layer는 `analysis.layer_policy: exact`(이름 일치) 또는 `ignore`.
- 지원 길이 단위는 `nm`, `um`, `mm`이며 `data.canonical_length_unit`(현재 `um`)으로 명시 변환한다.
- CA는 무차원 `1`이고 면적 보정은 canonical unit으로 환산한 unit-pixel 면적으로 나눈다.
- 이미지 `pixel_size`와 `origin_x/y`는 해당 묶음의 `physical_length_unit`을 사용한다. 현재 실제 자료의 기준 단위는 `um`이다.
- 순위는 `failure_type + 전극 역할 집합 + layer 집합`이 같은 비교 그룹 안에서만 계산한다.
- 서로 다른 CA 크기 격자는 `data.ca_grid`에 정렬한다. 기본 `exact`는 일치하는 크기만, `loglinear_within_range`는 관측 범위 **안**에서만 log-size 선형 보간한다. 범위 밖 외삽 금지.
- 이미지는 종횡비를 유지한 letterbox로 `data.image_size`에 맞추고 여백은 유효 영역 마스크 0이다.

## 6. Fab 가공자료와 적층표

실제 입력 예시와 작성 규칙은 [`domain-input-example/`](domain-input-example/README.md)에 있다. `fab_measurements.csv`는 Fab·제품군·공정 layer·1~50 um 이물 지름별 `glass 1장당 평균 검출 개수`, glass 한 장의 실제 조사 면적과 측정 기간을 제공한다. real 모드에서는 각 분포에 1~50 um 전체 행이 필요하다.

`layer_stack.csv`는 `product_id, design_version(* 허용), layer_name, stack_order, fab_process_layer`를 가진다. 숫자가 큰 layer를 상부로 보고 vertical short에 적용한다. 제품 간 이름이 다른 layer의 대응은 아직 보류되어 있어 같은 이름끼리만 비교한다.

`design_review_status.csv`의 `comparison_group_id`는 `ranking.csv` 또는 `review_cards.json`의 값을 그대로 사용한다. 외부 저항·capacitance·coupling·신호·설계 규칙·다른 short/open 검토 상태와 결과 참조만 보존하며 이 프로젝트가 해당 전기량을 계산하지 않는다.

## 7. 확장 지점

- 국부 이미지: 같은 `sample_id` 아래 고정 실제 길이 조각 목록을 추가하는 방식으로 확장한다. 조각을 별도 제품/묶음으로 만들지 않는다(분리 검증 누수 방지). 첫 버전은 전체 이미지만 사용한다.
- 색상 hotspot: `data.hotspot_legend`(`legend_id`, `entries[{rgb, value}]`, `tolerance`)가 있을 때만 확률로 변환한다.
