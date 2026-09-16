#!/usr/bin/env bash
# 4단계: 내 자료로 학습 → 좌표 → 분석 → 평가를 실행한다. 3단계 점검을 먼저 끝낸다.
# 사용법: bash scripts/4_analyze_my_data.sh [설정 파일]   (기본: configs/real.yaml)
# 자료나 설정을 바꿨다면 이 스크립트를 다시 실행한다(학습부터 다시 한다).
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIG="${1:-configs/real.yaml}"
if [[ ! -f "$CONFIG" ]]; then
  echo "오류: 설정 파일 $CONFIG 이(가) 없습니다. 먼저 scripts/3_check_my_data.sh를 보세요." >&2
  exit 1
fi

for step in train embed analyze evaluate; do
  echo "== $step"
  uv run simcompare "$step" --config "$CONFIG" > /dev/null
done

OUT="$(uv run python -c "from simcompare.config import load_config; print(load_config('$CONFIG').output_dir)")"
echo
echo "완료. 결과 폴더: $OUT"
echo "아래 순서로 열어 보세요:"
echo "  1) $OUT/figures/rank_shift.svg      크기·Fab 반영 전후 위험 순위 변화"
echo "  2) $OUT/figures/ca_curves.svg       이물 크기별 CA 곡선"
echo "  3) $OUT/review_cards.json           검토 카드(대표·반대 사례, 검토 후보, 전문가 확인 필요)"
echo "  4) $OUT/neighbors.csv               비슷한 사례(reuses_risk_inputs=False인 공간 우선)"
echo "  5) $OUT/figures/loo_stability.svg   제품 하나에 결론이 기대는지"
echo "  6) $OUT/evaluation.json             평가(보지 않은 제품에서 경향 반복, 그룹 제외 검증)"
echo "읽는 법: docs/usage.md 4장, docs/walkthrough.pdf 9장"
