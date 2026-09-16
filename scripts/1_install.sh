#!/usr/bin/env bash
# 1단계: 설치. 처음 한 번만 실행한다(인터넷 필요).
# 사용법: bash scripts/1_install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "오류: uv가 없습니다. 먼저 설치하세요: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

uv sync
uv run simcompare --help >/dev/null

echo
echo "설치 완료."
echo "다음: bash scripts/2_demo.sh   (더미 데이터로 전체 흐름 실행)"
