#!/usr/bin/env bash
# reference+target 수치 비교와 단독 제품 지도. 학습/embedding을 실행하지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."
CONFIG_PATH="${1:-configs/real.yaml}"
if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "설정 파일이 없습니다: $CONFIG_PATH" >&2
  exit 2
fi
for step in compare electrode-map; do
  rc=0
  uv run simcompare "$step" --config "$CONFIG_PATH" || rc=$?
  if [[ "$rc" == 3 ]]; then
    echo "$step: 부분 결과입니다. unavailable 값은 null이며 0이 아닙니다." >&2
  elif [[ "$rc" != 0 ]]; then
    exit "$rc"
  fi
done
