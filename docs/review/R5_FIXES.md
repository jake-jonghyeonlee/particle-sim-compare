# R5 수정 및 검증 기록

기준 C HEAD: a756b6c9e5f3f0108b51683c8f9e3626bf579485. 작업 저장소에서는 커밋하지 않는다. DL1 7f9d659 / DL2 e46a44e는 읽기 전용이다. 실제 데이터, network download, remote, pretrained weights를 사용하지 않는다.

| Finding | 상태 | 수정 또는 문서 범위 |
| --- | --- | --- |
| B1 | fixed | archive에 이미 있는 directory도 dirs_exist_ok로 overlay하여 dirty/committed 상태 독립; throwaway clone의 commit 전후 검증 |
| B2 | fixed | seal/read 양쪽에서 float64 cosine을 6자리 rounding, absolute tolerance 1e-9로 index score와 대조; dl2_scores_do_not_match_index; 실제 DL2 train/index/search A/B 재현과 항상 실행하는 fixture negative |
| N1 | fixed | build/check가 manifest SHA256 출력; --expect-manifest-sha256 대조; commit 변조, file+hash 동시 변조 모두 외부 기대 hash로 거부 |
| N2 | fixed | 파일 inventory에서 도출한 directory inventory도 정확히 검사; 빈 .venv/와 unexpected/nested 반례 |
| N3 | fixed | Python >=3.11.4를 생성 전 검사; macOS 3.9 재현은 clear error/출력 없음; offline은 각 env 실제 interpreter/version 기록 |
| N4 | documented | Python connect/connect_ex/create_connection/getaddrinfo를 상속 non-isolated 자식에만 patch; UDP sendto/gethostbyname/_socket/python -I/비-Python 제외; OS firewall은 별도 |
| N5 | fixed | offline 기존 non-empty --out 거부, 기존 validation.json 보존 |
| N6 | fixed | handoff README의 pre-commit verification record 삭제; 증거는 이 review 문서에만 기록 |
| N7 | documented | 명령 옆에 DL1 hash↔asset/design 대응은 사용자 주장임을 명시; 같은 product의 다른 case 주장 및 중복 binding의 기존 동작은 변경 안 함; 수용 반례도 테스트 |
| N8 | fixed | joined primary/supplementary 분리, primary_cohort/expanded/coverage 보존; 실제 cohort 파일 SHA256 pin과 content 대조 |
| N9 | fixed | rejected 필수 키; 누락은 dl2_rejected_field_required |
| N10 | fixed | seal/context는 기존 out을 --force 없이 거부, 실패 시 삭제 안 함; JSON은 임시 파일→replace, context validation 뒤 NPZ 생성/오류 cleanup; read의 stale error output 제거 의미는 유지 |
| N11 | fixed | map 대표 diameter가 없으면 numeric_status=unavailable, representative_diameter_group_unavailable; joined에도 상태/사유 전달 |
| N12 | fixed/documented | 모든 설정 변수, demo zero ROI/validate exit 3, DL2 config wrapper, G6 pending --relations/평가 보류 절차 명시 |
| N13 | documented | 이전 1_install…5_check scripts를 bundle 내부에서 실행하지 말 것; inventory/network 이유 명시 |
| N14a | fixed | non-synthetic asset reference의 semantics 누락은 explicit_value_semantics_required; synthetic 기본은 보존 |
| N14b/c | documented | NPY/PNG 대 BMP/TIF container 차이, DL1 reader polarity 부재는 G2/layout polarity pending; 기능 추가 안 함 |

B2의 한계: native search는 checkpoint fingerprint를 직접 제공하지 않는다. 재계산은 supplied index와의 수치 일치를 보장한다. 다른 모델이 같은 rounded scores를 내면 원래 producer 모델을 구별할 수 없다. expected는 검사 index 자신이 아닌 승인 checkpoint 및 별도 보관한 training/index stdout에서 얻는다.

N12(d)에 대한 좁은 정정: DL2 현재 read_config는 document.get('config', document)를 사용하므로 nested synthetic.example.json도 직접 읽을 수 있다. 문서에 wrapper와 선택적 추출을 명시했고 실제 CLI 회귀는 A wrapper/B flat 모두 실행한다. 그 외 finding에 이견은 없다.

기존 test 변경: B1 copytree의 committed 기대를 바로잡았고, N8의 combined matches 참조를 primary/supplementary로 바꿨다. reseal fixture helper는 N10의 명시적인 force, N8의 cohort를 넘긴다. 수치 expected는 줄이거나 바꾸지 않았다. Native DL search/index JSON은 수정하지 않았고 sidecar와 cohort 및 fixture generator만 갱신했다.

## 실행 증거

임시 clone commit은 시험용이며 승인 release commit이 아니다. 실제 G1/G2/G4/G5/G6, real labels/targets, conduction proxy와 공개 weights/licence는 pending이다.

작업 저장소에서 실행:

```sh
.venv/bin/python -m pytest -p no:warnings tests/test_dl_results.py tests/test_handoff.py
# 44 passed in 17.62s
.venv/bin/python -m pytest -p no:warnings
# 523 passed, 2 skipped in 36.21s
git diff --check
# exit 0
```

실제 DL2 CLI 회귀는 sibling .venv로 seed 7/11의 checkpoint A/B를 각각 train/index/search하여 B search를 A index에 seal할 때 dl2_scores_do_not_match_index를 확인했다. 순수 fixture negative도 seal/read 양쪽을 검증했다. 두 skip은 전체 suite의 기존 조건부 skip이며 이 환경에서 실제 DL2 회귀는 실행됐다.

로컬 throwaway clone `/tmp/simcompare-r5-acceptance/particle-sim-compare`에 변경 파일을 복사하고, sibling 저장소를 읽기 전용 입력으로 연결했다. 아래 명령은 해당 clone에서 커밋 전과 후에 각각 실행했다.

```sh
PYTHONPATH=/tmp/simcompare-r5-acceptance/particle-sim-compare/src /Users/jonghyeonlee/git/particle-sim-compare/.venv/bin/python -m pytest -p no:warnings
# commit 전: 523 passed, 2 skipped in 35.70s
git add .
git -c user.name=R5Fixture -c user.email=r5-fixture@example.invalid commit -m 'R5 test-only candidate'
# clone commit: 30dbc6e1f77c5484519bea7fe728f81015d3b3a8
PYTHONPATH=/tmp/simcompare-r5-acceptance/particle-sim-compare/src /Users/jonghyeonlee/git/particle-sim-compare/.venv/bin/python -m pytest -p no:warnings
# commit 후: 523 passed, 2 skipped in 35.48s
```

원래 작업 저장소 HEAD는 a756b6c9e5f3f0108b51683c8f9e3626bf579485 그대로이며 커밋하지 않았다. DL1/DL2의 작업 트리도 clean이다. 시험용 clone의 커밋은 이 문서의 최종 실행 증거 추가 전 상태로, 테스트 대상 코드와 fixtures는 동일하다.

bundle 생성과 검사:

```sh
bash /tmp/simcompare-r5-acceptance/particle-sim-compare/scripts/build_handoff.sh /tmp/simcompare-r5-acceptance/bundle
# exit 0; dirty_test_only=false; weights=[]
# manifest_sha256=71038b515411dc9adf795922e1b40d8f2eb6e78bf9979775eba79138523e9b38
bash scripts/check_handoff.sh /tmp/simcompare-r5-acceptance/bundle
# exit 0; 같은 manifest_sha256 출력
bash scripts/check_handoff.sh /tmp/simcompare-r5-acceptance/bundle --expect-manifest-sha256 71038b515411dc9adf795922e1b40d8f2eb6e78bf9979775eba79138523e9b38
# exit 0; 같은 manifest_sha256 출력
```

bundle commit은 C=30dbc6e1f77c5484519bea7fe728f81015d3b3a8, DL1=7f9d659a014d7f5e3aaf4b63118d0ca51774b75f, DL2=e46a44e0afbe94cd3f7a8d0264ee02f3076cf44b이다. 테스트는 edited commit과 edited file+edited hash를 외부 manifest hash로 거부하고, 빈 unexpected directory도 거부한다.

offline 실행은 원래 작업 저장소에서 다음처럼 수행했다. dependency 설치나 download는 하지 않았다.

```sh
COMPARE_PYTHON="$PWD/.venv/bin/python" DL1_PYTHON=/Users/jonghyeonlee/git/particle-sim-dl1/.venv/bin/python DL2_PYTHON=/Users/jonghyeonlee/git/particle-sim-dl2/.venv/bin/python bash scripts/validate_offline.sh /tmp/simcompare-r5-acceptance/bundle --out /tmp/simcompare-r5-acceptance/offline --expect-manifest-sha256 71038b515411dc9adf795922e1b40d8f2eb6e78bf9979775eba79138523e9b38
# exit 0; status=available; 27 steps
# validation: /private/tmp/simcompare-r5-acceptance/offline/validation.json
# compare Python 3.11.15; DL1 Python 3.12.13; DL2 Python 3.12.13
COMPARE_PYTHON="$PWD/.venv/bin/python" DL1_PYTHON=/Users/jonghyeonlee/git/particle-sim-dl1/.venv/bin/python DL2_PYTHON=/Users/jonghyeonlee/git/particle-sim-dl2/.venv/bin/python bash scripts/validate_offline.sh /tmp/simcompare-r5-acceptance/bundle --out /tmp/simcompare-r5-acceptance/offline
# exit 2; offline output directory must be empty; 기존 validation.json 보존
```

27단계는 bundle integrity, 세 환경의 interpreter/import/help, numeric demo/validate/compare/map, fixture DL1/DL2 inference와 seal/read를 포함한다. numeric validate의 exit 3은 demo의 의도된 partial 상태로 기대값이며 나머지 단계는 exit 0이다. network 차단의 범위는 N4에 적은 Python socket guard 범위이며 OS 전체 차단을 검증했다는 뜻은 아니다. unsupported Python 3.9의 생성 전 실패와 무출력 반례도 test_handoff에서 통과했다.
