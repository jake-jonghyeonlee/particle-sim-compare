#!/usr/bin/env bash
# particle-sim 결과 디렉터리를 simcompare 계약으로 점검만 한다(결과 파일을 고치지 않음).
# 사용법: bash scripts/5_check_particle_sim_run.sh RUN_DIR [RUN_DIR ...] [옵션]
#   --product-config 제품manifest.yaml   전극 역할·제품군·Fab ID (없으면 placeholder와 경고)
#   --mode real|demo                    simcompare 검사 모드 (기본 real)
#   --ca nominal|mean                   CA 곡선 값 (mean은 확률 run의 ca_mean_um2 / ROI 면적)
#   --mapping 대응표.yaml               unit 대응 규칙 추가·덮어쓰기
#   --output 보고서.json                JSON 보고서
#   --strict                            미지원 unit(예: CNO)이 있으면 실패
# parquet·zarr 읽기용 pyarrow·zarr는 이 실행에만 임시로 추가한다(프로젝트 의존성은 바꾸지 않음).
set -euo pipefail
# 현재 디렉터리를 유지해 상대 경로 인자가 그대로 통하게 한다.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $# -eq 0 ]]; then
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
fi

uv run --project "$ROOT" --with pyarrow --with zarr python "$ROOT/scripts/check_particle_sim_run.py" "$@"
