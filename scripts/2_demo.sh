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
