# CA 단위와 면적 정규화 계약 (2026-09-27)

particle-sim에서 이미 ROI로 나눈 CA를 다시 나누던 결함을 수정한다. 원 nominal/mean CA를 um^2로 전달하고 simcompare가 ROI 면적으로 한 번만 나눈다. legacy 무차원 입력은 기존 계산을 유지한다.

면적 단위 nm^2/um^2/mm^2를 canonical 길이 제곱으로 환산하며 결과 단위는 1이다. 무차원 1은 기존 0~1 범위 검사와 결과 단위 1/um^2를 유지한다. 면적 단위에는 0~1 검사를 적용하지 않는다. 결측과 외삽 정책은 유지한다.

담당자 확인 필요: 두 경로의 도메인 해석 및 비교 가능 범위. domain_rules.yaml의 legacy 규칙은 confirmed를 유지하며 particle_sim 하위 규칙만 provisional이다.

검증 결과와 두 저장소 수정 파일 목록은 최종 작업 기록에 추가한다.

## 검증

- A 시작 전 uv run pytest: 176 passed, 55 skipped, 1 warning (8.08s).
- A 수정 후 uv run pytest: 184 passed, 55 skipped, 1 warning (6.58s).
- 필수 회귀: 세 면적 단위 환산과 상대 오차 1e-12, ROI 비 4배, 미관측 NaN, legacy 단위, 잘못된 단위 거부, risk_table/ranking 결과 단위 확인.
- B 기본 uv run pytest: 441 passed, 3 failed (55.60s). 기존 tests.unit import의 저장소 루트 경로 문제이며 변경 코드 결함이 아니다; 관련 import 변경 시도는 되돌렸다.
- B PYTHONPATH=. uv run pytest: 444 passed (55.41s). 저장소 루트를 명시하면 전체 통과한다.
- B uv run ruff check --no-cache src tests: All checks passed.
- 두 저장소 git diff --check 통과.

## particle-sim-compare 수정 파일

- README.md
- docs/data_contract.md
- docs/domain-input-example/domain_rules.yaml
- docs/usage.md
- src/simcompare/adapters/particle_sim_run.py
- src/simcompare/analysis.py
- src/simcompare/checks.py
- src/simcompare/schema.py
- tests/test_analysis_cards.py
- tests/test_particle_sim_export_integration.py
- tests/test_particle_sim_run.py
- tests/test_schema.py
- docs/decisions/2026-09-27-ca-unit-and-area.md

## particle-sim 수정 파일

- README.md
- src/particle_sim/export/simcompare.py
- tests/unit/test_simcompare_export.py
- outputs/ca-area-fix-20260927/worker-report.md


### CA unit-family comparison (CA-2)

Area CA is converted before comparison to `canonical_length_unit^2` (default `um^2`),
including raw model/PCA channels, CA maxima and CAA. Per-area CA divides by ROI area
once and has unit `1`; legacy dimensionless CA retains `1/um^2` in the default configuration.
CAA uses `relative_index` for legacy CA and `relative_index*um^2` for area CA
(or the configured canonical area unit). This is an area-weighted relative index, not a probability.
Real datasets mixing dimensionless and area families are rejected with counts and sample IDs;
demo mode records a warning. Different area units belong to the same family after conversion.
Legacy CA's original confirmed rule is preserved; the particle-sim sub-rule is provisional.
Cross-family comparisons remain prohibited until a conversion basis is confirmed.
Pre-fix `particle-sim-*` exports with dimensionless CA must be re-exported with the fixed exporter
(real: error; demo: warning). Cards use the first metric complete for every group row in priority
`caa_actual`, `ca_area_max`, `ca_max`; otherwise they use the most observed metric,
with ties resolved in that priority order and missing values preserved.


## CA-2 implementation and acceptance evidence (2026-09-27)

Task `task_3a07eae7cfb1`, Dispatch `ctx_17e84dea0618`; sole editor, no nested workers.
Baseline branch `sim-compare-gaps-20260927`, HEAD `f691603`; adopted diff fingerprint
`23c7ffd040f57ab461e19c109b553ca806606877b0cfbf18f57cd7c56c852c27` verified before editing.

- B1: `schema.canonicalize_ca` is the shared in-place conversion helper; the adapter
  prepares loaded bundles (default um), and encoding/analysis prepare direct inputs using
  the configured canonical length. CAA also invokes the same helper and accepts an explicit
  canonical length unit. Repeated same-unit preparation does not divide or rescale twice.
  Baseline features inherit the canonical encoded CA channel without a baseline code change.
- A1: shared bundle preparation rejects dimensionless `particle-sim-*` provenance in real
  mode with re-export guidance; demo returns explicit warnings.
- A2: two nominal synthetic pilot runs use 100x100 and 50x50 um ROI. Their generated parquet
  files are assigned the same controlled raw CA pattern and a consistent ROI-normalized
  column, then exported through particle-sim (no CSV hand-writing). Adapter results give
  an exact 4x per-area ratio; exporter regression to the normalized column would give 16x.
- A3: cards select one metric for the group, using completeness and documented priority.
- A6: original HEAD legacy CA keys and confirmed status restored verbatim; provisional
  particle_sim and prohibited cross-family comparison rules added.
- A7: duplicate text check removed; valid_contract is null when there are no dimensionless curves.

Validation against this candidate:

| Gate | Result |
|---|---|
| `UV_NO_SYNC=1 uv run --project /Users/jonghyeonlee/git/particle-sim-compare pytest -o addopts="" -q -rs` | 190 passed, 56 skipped, 1 existing torch warning; skips are optional readers and opt-in exporter tests |
| Particle-sim venv Python, `PYTHONPATH=src`, reader tests + CLI tests + integration tests with `PARTICLE_SIM_ROOT` and `PARTICLE_SIM_PYTHON` set | 65 passed, no skips, 6.46s; both exporter tests executed |
| Demo `simcompare all --config /tmp/ca2-validation-20260927/demo.yaml` (output `/tmp/ca2-validation-20260927/demo-out`) | exit 0, all five stages completed |
| Chain-D copied config `/tmp/ca2-validation-20260927/chain-D.yaml`, output `/tmp/ca2-validation-20260927/chain-D-out` | expected exit 2, pre-fix rejection below |
| Particle-sim venv ruff `--isolated --select E4,E7,E9,F` on changed Python files | All checks passed |
| `git diff --check` | clean |

Chain-D message:

```text
오류: SYN_LAYOUT_2X2-ASSUMED_01-SOURCE_CNT1_OPEN-PAIR_E92E6334BE7DAE6E: pre-fix particle-sim export has dimensionless CA already normalized by ROI; re-export with the fixed particle-sim exporter
```

Implementation added/changed: `schema.py`, `adapters/example.py`, `dataset.py`, `analysis.py`,
`caa.py`, `cards.py`, `checks.py`, `tests/test_ca_unit_contract.py`,
`tests/test_particle_sim_export_integration.py`, README CA paragraph, data_contract, usage,
open_questions CA row, domain_rules CA block, and this decision record.
Adopted baseline changes in other allowed files were preserved.
No commits, branches, resets, stashes, installs, or writes to particle-sim/extractor were performed.
Independent Opus review and coordinator final verification remain outside this implementation Task.
