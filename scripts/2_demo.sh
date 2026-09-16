#!/usr/bin/env bash
# 2단계: 더미(가짜) 데이터를 만들고 점검 → 학습 → 좌표 → 분석 → 평가를 한 번에 실행한다.
# 결과는 연구 근거가 아니라 "도구가 이렇게 동작한다"를 확인하는 용도다.
# 사용법: bash scripts/2_demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."

uv run simcompare all --config configs/demo.yaml --demo-out demo_data > /dev/null

echo
echo "완료. 결과 폴더: outputs/demo/"
echo "아래 순서로 열어 보세요 (docs/walkthrough.pdf의 그림과 비교):"
echo "  1) outputs/demo/figures/rank_shift.svg      브라우저로 열기 - 크기·Fab 반영 전후 위험 순위 변화"
echo "  2) outputs/demo/figures/ca_curves.svg       브라우저로 열기 - 이물 크기별 CA 곡선"
echo "  3) outputs/demo/figures/loo_stability.svg   브라우저로 열기 - 제품 하나를 빼도 결과가 유지되는지"
echo "  4) outputs/demo/review_cards.json           불량 종류별 검토 카드"
echo "  5) outputs/demo/validation.json             데이터 점검 경고"
echo "각 파일의 의미: docs/usage.md 4장"
