# F2 — rewrite docs/usage.md: concise, split ETX vs Windows. Risk tier NORMAL (docs only). Worker: Sonnet 5.5 medium. FOREGROUND validation.
Base: HEAD c7a2829 + uncommitted F1 (keep). Read docs/plan/followups/F1_report.md (behaviour notes to fold in).
User request (Korean): "usage.md를 깔끔하게 정리, ETX와 Windows 나눠서 간결하게".
Ownership: docs/usage.md (rewrite), docs/usage_reference.md (new: detailed reference), links that point to moved anchors (README.md,
configs/etx/README.md, scripts/build_etx_stage_runners.py text + rebuilt runners ONLY if a header line references usage.md sections),
tests ONLY if a test asserts usage.md text (move the asserted text, don't weaken the assertion). No code changes.
Target shape of docs/usage.md (Korean, short; aim ≤ ~200 lines, plain sentences, no internal plan jargon like B3/Q3/T5):
1. 무엇을 하나 + 6회차 흐름 한 표 (회차, 명령, 입력, 출력, 다음 회차 전 수작업).
2. ## ETX (Linux 서버): 준비물(Python 3.8+, numpy, Pillow), 실행기 6개 교체·EDIT 블록(JOBS/BASE_DIR), 회차별 실행 순서와 회차 사이 수작업
   (pairs.csv 복사, 3회차 뒤 import 결과 배치와 sha256 갱신), 처음부터 다시 할 때 옮길 폴더 목록, 예상 시간·메모리(1,682 case 기준 실측 요약),
   자주 나는 오류와 대처(oversized_real_operation → max_operation_bytes, output exists → 폴더 옮기기, expected_case_folder_missing 의미).
3. ## Windows 폐쇄망 (CMD): 오프라인 설치(번들 zip, verify, install_offline.cmd), 실행 예시 명령, Windows에서 다른 점만.
4. ## 공통: 종료 코드(0/2/3), 결과 파일 읽는 법, 결과 교체 보장(실패 시 이전 결과 유지/복구) 한 단락, 설정 키 요약 표(기본값).
5. 자세한 계약·옵션·내부 동작은 docs/usage_reference.md로 이동 — 기존 usage.md 내용을 잃지 말 것(모든 옵션·오류 코드·스키마 설명 보존,
   F1 반영해 오래된 문장 수정). 사실 확인은 코드/configs/etx/bundles JOBS와 대조(추측 금지); 숫자는 docs/plan/*/T5_benchmark.md에서 인용.
Acceptance: every option/error code mentioned in the old usage.md appears in usage.md or usage_reference.md (write a quick check script, report
missing=0); internal links resolve; full pytest green except tests/test_handoff.py. No commit. worker_done: line counts old/new, check result.
