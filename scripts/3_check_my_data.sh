#!/usr/bin/env bash
# 3단계: 내 자료를 점검만 한다(자료를 고치지 않음). 경고를 읽고 자료를 보완한 뒤 4단계로 간다.
# 사용법: bash scripts/3_check_my_data.sh [설정 파일]   (기본: configs/real.yaml)
# 설정 파일은 configs/real.example.yaml을 복사해 만든다.
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIG="${1:-configs/real.yaml}"
if [[ ! -f "$CONFIG" ]]; then
  echo "오류: 설정 파일 $CONFIG 이(가) 없습니다." >&2
  echo "  cp configs/real.example.yaml configs/real.yaml 후 data_dir 등을 실제 값으로 바꾸세요." >&2
  exit 1
fi

uv run simcompare validate-data --config "$CONFIG"

OUT="$(uv run python -c "from simcompare.config import load_config; print(load_config('$CONFIG').output_dir)")"
echo
echo "점검 완료. 확인할 파일: $OUT/validation.json"
echo "  - warnings: 빠진 자료, 실제 크기 없음, 픽셀 대응 확인 불가 등"
echo "  - confounding: Fab과 제품군이 겹쳐 효과를 나눌 수 없는지"
echo "  - caa_applicability: CAA를 계산할 수 없는 사례와 이유"
echo "각 항목의 의미: docs/usage.md 4.1절"
echo "문제가 없으면: bash scripts/4_analyze_my_data.sh $CONFIG"
