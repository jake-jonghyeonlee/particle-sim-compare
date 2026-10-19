#!/usr/bin/env bash
# 합성 수치 비교와 독립 지도/검토 위치 내보내기. 학습 호출 없음.
set -euo pipefail
cd "$(dirname "$0")/.."
run_partial() {
  local rc=0
  uv run simcompare "$@" || rc=$?
  if [[ "$rc" == 3 ]]; then
    echo "부분 결과: 상태와 사유를 확인하세요." >&2
  elif [[ "$rc" != 0 ]]; then
    return "$rc"
  fi
}
uv run simcompare demo-data --out demo_data
run_partial compare --config demo_data/config.yaml
run_partial electrode-map --config demo_data/config.yaml
run_partial export-review-rois --config demo_data/config.yaml --out demo_data/review_rois
echo "수치 결과: demo_data/numeric_outputs/ · 위치: demo_data/review_rois/review_rois.json"
# 통합 HTML 보고서: 데모 frame은 작아 창 20 µm가 clipping되므로 1 µm 창으로 대응 위치를 비교한다.
cat > demo_data/pairs.json <<'JSON'
{"schema_version": "roi_correspondence_v1",
 "producer": {"kind": "manual", "version": "1", "commit": null, "created_at": "2026-10-19"},
 "pairs": [{"pair_id": "P1",
   "a": {"product_id": "reference", "design_version": "synthetic_design", "frame_id": "reference_frame", "center_xy_um": [102.5, 200.5],
         "analysis_layer": "M2", "failure_type": "lateral_short",
         "endpoints": [{"electrode_id": "A", "role": "gate", "actual_layer": "M2"}, {"electrode_id": "B", "role": "data", "actual_layer": "M2"}]},
   "b": {"product_id": "target", "design_version": "synthetic_design", "frame_id": "target_frame", "center_xy_um": [101.5, 200.5],
         "analysis_layer": "M2", "failure_type": "lateral_short",
         "endpoints": [{"electrode_id": "A", "role": "gate", "actual_layer": "M2"}, {"electrode_id": "B", "role": "data", "actual_layer": "M2"}]},
   "source": {"kind": "manual", "labelled_by": "designer", "labelled_at": "2026-10-19"}, "status": "ready", "reasons": []}]}
JSON
run_partial compare-windows --config demo_data/config.yaml --correspondence demo_data/pairs.json --window-sizes 1 --sort-diameter-um 5 --out demo_data/windows
run_partial report --compare-dir demo_data/numeric_outputs --map-dir demo_data/numeric_outputs --windows-dir demo_data/windows \
  --rois-dir demo_data/review_rois --out demo_data/report --rank-window-um 1
echo "통합 보고서: demo_data/report/report.html"
