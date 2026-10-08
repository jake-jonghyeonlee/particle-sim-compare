#!/usr/bin/env bash
# numeric_config_v1 입력 점검. 실제 adapter는 G2 pending이며 입력 불가를 보고한다.
set -euo pipefail
cd "$(dirname "$0")/.."
CONFIG_PATH="${1:-configs/real.yaml}"
if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "설정 파일이 없습니다: $CONFIG_PATH (configs/real.example.yaml 참고)" >&2
  exit 2
fi
rc=0
uv run simcompare validate-data --config "$CONFIG_PATH" || rc=$?
if [[ "$rc" == 3 ]]; then
  echo "부분 점검 결과입니다. 출력의 status/reasons와 gate를 확인하세요." >&2
elif [[ "$rc" != 0 ]]; then
  exit "$rc"
fi
