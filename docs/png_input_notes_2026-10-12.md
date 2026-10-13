# 실제 DL1 PNG 입력 — 2026-10-12 정정

최신 사용자 설명으로 이전 기록을 대체한다. 구현은 아직 변경하지 않았다.
DL1 먼저 수정 → 사용자 폐쇄망 테스트 → DL2 후속 적용 순서다.

## 확인된 입력

- Hotspot은 DL1 입력에 쓰이지 않는다.
- 모델명 폴더 → 전극명 폴더 → layer별 binary PNG 구조다.
- 파일명 예: `1-pxl.png`, `2-SD2.png`.
- 각 이미지에는 해당 전극의 해당 layer segment가 합쳐져 있다.
- 전극 검정 패턴 / 흰 배경이라는 앞선 설명은 유지한다.
- 이미지는 **2×2 영역에 상/하/좌/우 각각 500 pixel을 추가**한 범위다.
  해상도는 **0.1 µm/pixel**이다. 이전 0.5 및 0.05 값을 적용하지 않는다.
  각 방향 추가 폭은 50 µm다. 추가 영역이 주변 패턴인지 빈 padding인지는 미확정이다.
- 이물 크기는 **지름**이다. 이를 DL1 입력에 추가한다는 의미는 아니다.
- 최초의 불량-전극-layer pair 폴더 및 images/1.png, 2.png 설명은
  DL1 원천 구조로 사용하지 않는다.

```text
<모델명>/
└── <전극명>/
    ├── 1-pxl.png
    └── 2-SD2.png
```

## 연결 정보

사용자는 모델명과 함께 JSON 연결 정보를 전달하거나 CNT 이미지를 제공할 수 있다고 설명했다.
원천 JSON 형식과 CNT 파일 배치는 아직 미확정이다.

현재 DL1 data.py/load_manifest 및 docs/data_contract.md 확인 결과:

- 제품 manifest JSON에 connections와 connectivity_observation을 받는다.
- 연결 항목: connection_id, kind(CNT/VIA), endpoints, mask, observation.
- endpoint 두 개는 각각 element_id(전극×layer 항목)와 원본 xy_um 좌표를 참조한다.
- 연결 mask가 없으면 mask:null 및 observation:missing으로 명시한다.
  이 경우에도 endpoint 참조와 좌표는 필수다.
- 선택적 제품 전체 cnt mask도 받으며 이미지 채널에 사용한다.
- CNT 이미지만으로 endpoint 관계를 추론하지 않는다. 관계가 없으면
  connections:[] 및 connectivity_observation:unavailable로 표시한다.
- 현재 mask loader는 NPY 전용이므로 PNG/폴더 입력 지원 변경이 필요하다.
- 기존 계약은 whole-unit 입력이다. 2×2에서 한 unit으로 자동 변환하지 않는다.

## 추가 확인 사항

1. 파일명 숫자 접두부는 **위에서 아래로 매긴 layer 순서**다. `1-pxl.png`는 최상부 pxl layer다.
   전극별 번호가 아닌 모델 전체 공통 stack 순번인지와 CNT layer 순서 표기 방식은 확인 필요하다.
2. 전극/layer 이미지들의 크기·원점·방향 동일 여부와 미관측 영역 유무.
3. 2×2 전체를 비교 대상으로 사용할지, 한 unit을 선택할지 및 선택 경계.
4. 연결 JSON에 연결 전극/layer뿐 아니라 연결 위치 좌표도 제공 가능한지.
5. CNT 이미지의 위치, polarity, layer별 분리 여부 및 전극과의 좌표 대응.
6. 모델명과 product_id/lineage_id/design_version 대응.

실제 grid/최소 선폭, 학습 label/split 및 인수 기준은 기존 pending 항목으로 남는다.

## 후속 사용자 설명 및 설계 제안 (구현 미승인/미완료)

- 사용자는 전체 mask와 layer 순서를 통해 연결 JSON 자동 생성을 원한다.
- 사용자 설명: 상부는 CNT 위치 바로 위 전극이며, 동일 좌표의 하부 전극 패턴과 연결된다.
- CNT가 위치한 layer가 다양하므로 CNT별 stack 위치 식별이 필요하다.
- 하부 후보가 여러 layer에 존재할 때 가장 가까운 전극에서 끝나는지,
  지정 layer까지 관통하는지 또는 복수 연결인지 아직 확정되지 않았다.
- 비교 범위는 2×2 core 또는 여백 포함 입력 모두 가능하다고 사용자가 설명했다.
- 현재 DL1은 전체 frame을 공통 비율로 resize+pad하며 원본 endpoint 좌표를 보존한다.
  제품 간 동일한 기능 위치를 자동 정합하거나 검색하는 기능을 의미하지 않는다.

제안: 원본 전체 frame과 core bbox를 보존하고, 원본 해상도에서 CNT 연결 후보를
추출한 뒤 core 기준 비교 좌표로 변환한다. 모든 layer에 동일 변환을 적용한다.
정확히 각 방향 500 pixel 여백이면 core bbox는 반개구간
[x0,y0,x1,y1]=[500,500,W-500,H-500]이다. 제품별 정규화 좌표와
원본 pixel/µm 좌표를 함께 보존한다. 전체 입력 비교를 선택한다면 그것과 일치하는
frame/좌표 계약을 사용하며, core crop은 아직 결정 사항이 아니다.

자동 생성은 CNT별 layer 순서와 접촉 규칙이 확정된 경우에만 관계로 채택한다.
CNT component와 전극의 실제 교집합 안에서 endpoint를 선택하고,
중복 후보/접촉 없음/다중 하부 후보는 명시적 모호 상태로 보고한다.
component 중심점이 전극 위에 있다는 가정이나 모든 하부 겹침을 연결한다는 가정을 하지 않는다.
추론 근거(원본 mask, CNT layer, 후보 전극, 규칙)를 보존하며 전기적 연결 검증으로 표시하지 않는다.

## 추가 확정 — CNT 선택 및 주변 영역

- CNT는 하나의 연결 상대를 가진다. 하부 후보 layer가 여러 개이면 CNT layer에서
  가장 가까운 layer를 선택한다. 사용자는 아래부터 쌓는 공정이라고 설명했다.
- CNT도 stack 순번을 가진 파일명이며 예시는 `006_OCNT.png`다.
  전극 예시의 하이픈과 CNT 예시의 underscore를 모두 고려해야 한다.
- 상하좌우 500 pixel은 빈 padding이 아니라 실제 전극 패턴이 있는 영역이다.
- 동일 layer에서 여러 전극이 겹치는 경우의 해소 규칙은 아직 미확정이다.
- 중앙 2×2 비교 제안은 원본의 각 가장자리 500 pixel을 비교 view에서 제외한다는 뜻이다.
  사용자의 crop 승인은 아직 없으며 주변 패턴을 임의로 제거하지 않는다.
- 고정 grid에 맞추는 현재 resize+pad는 물리 해상도 보존 방식이 아니다.
  원본은 0.1 µm/pixel을 유지하지만 모델 입력의 유효 µm/pixel은 제품별 배율에 따라 달라진다.
  좌표 역변환 가능성과 물리 길이/미세 형상 보존은 별개다.
- 현 단계는 설명 및 요구사항 확인이다. crop/전체 범위 비교와 물리 스케일 유지 전략의
  최종 결정 및 DL1 구현은 아직 진행하지 않았다.

## 최종 확인 및 DL1 구현 명세 초안

사용자 확인:
- CNT 이미지는 `<모델명>/cno/`에 있다. 흰 배경 / 검정 패턴이다.
- 숫자는 모델 공통 stack의 위→아래 순서이며 아래로 갈수록 커진다.
- 모델 내 모든 이미지의 가로·세로 크기, 원점, 방향은 동일하다.
- 승인된 비교 방식은 주변 500 pixel을 포함한 전체 입력의 공통 resize+pad와
  원본 해상도에서 추출한 CNT 연결 관계의 병용이다. 자동 crop이나 타일 비교는 하지 않는다.

### 1. 원천 탐색과 정규화

`<입력 root>/<모델명>/<전극명>/<순번>-<layer>.png` 및
`<입력 root>/<모델명>/cno/<순번>_<CNT layer>.png`를 읽는다.
예시: 전극 `1-pxl.png`, `2-SD2.png`, CNT `006_OCNT.png`.
숫자 접두부 뒤 첫 `-` 또는 `_`를 구분자로 사용하고 나머지를 layer명으로 보존한다.
순번은 정수로 해석하며 원래 파일명도 provenance에 남긴다.
`cno`는 CNT 전용 디렉터리로 분류한다. 같은 stack 순번의 상충된 layer명,
중복 전극×layer asset, 해석 불가능한 PNG 이름은 오류로 보고한다.

흑백 PNG를 검정=1, 흰색=0인 내부 binary mask로 변환한다.
회색/반투명 등 미확정 표현을 임의 threshold하지 않고 파일별 오류로 보고한다.
PNG mode별 실제 지원 범위는 fixture로 확인한다. 모든 mask shape가 동일해야 한다.
원본 전체 shape와 pitch=0.1 µm/pixel을 기록한다.
모델별 로컬 원점은 [0,0]으로 정의하며 외부 절대 설계 좌표로 주장하지 않는다.
원본 좌표계에서 pixel 중심 (x,y)의 물리 좌표는
[(x+0.5)*0.1, (y+0.5)*0.1] µm이다.
core bbox=[500,500,W-500,H-500]는 참고 메타데이터로만 저장한다.
W,H가 1000보다 커야 유효한 중앙 영역이 존재한다.
미관측 영역 정보가 없다면 완전 관측 가정의 사용 여부를 import 보고서에 명시한다.

### 2. CNT 연결 추출

리사이즈 전 원본 binary mask에서 CNT별 접촉 영역을 분리한다.
초기 component 규칙은 4-neighbour를 제안값으로 기록한다. 대각선 접촉의 의미는
실제 fixture로 확인하며 이 구현 선택을 숨기지 않는다.
CNT component C와 겹치는 전극을 위/아래로 분류한다.
위쪽은 CNT 순번보다 작은 순번 중 가장 가까운 겹침 layer,
아래쪽은 큰 순번 중 가장 가까운 겹침 layer를 후보로 선택한다.
단, 사용자 표현인 '바로 위 전극'의 구현은 가까운 겹침 전극이라는 해석이므로
중간 layer를 건너뛰는 사례가 있으면 검증 보고서에 드러내고 확인한다.
각 component는 최대 한 쌍의 endpoint 관계만 만든다.
같은 선택 layer의 복수 전극, endpoint 누락, 위/아래가 같은 element,
분할되지 않은 CNT 영역에 여러 연결이 포함된 경우는 모호/미해결로 남긴다.

사용자의 동일 좌표 조건을 지키기 위해 C & upper_mask & lower_mask의
공통 교집합이 있어야 관계를 채택한다. 위/아래가 CNT의 다른 위치에서만
겹치는 경우에는 자동 확정하지 않는다.
대표 좌표는 공통 교집합 내부의 결정론적 픽셀을 선택한다
(예: 교집합 centroid에 가장 가까운 픽셀, 동률은 y/x 순).
두 endpoint 모두 이 좌표를 참조하며 mask는 원본 CNT component다.
다른 하부 후보는 선택하지 않되 선택 이유와 후보 목록을 보고서에 보존한다.
전기적 연결이 검증됐다는 상태를 생성하지 않는다.

### 3. 내부 계약 연결

기존 DL1 typed Product / Element / Connection에 연결하는 source adapter를 둔다.
제품 ID는 모델 폴더명, 전극 ID는 전극 폴더명으로 원천 값을 보존한다.
layer명과 stack 순번은 분리해 보존한다. 서로 다른 모델의 role/layer 별칭을
자동으로 같은 의미로 합치지 않는다.
lineage_id/design_version은 사용자 metadata 또는 명시적 mapping으로 받으며
폴더명에서 임의 추정하지 않는다. lineage 기반 학습 split도 자동 승인하지 않는다.

연결 추출 provenance에는 algorithm/version, mask hash, stack 순서, 후보/선택 이유,
대표 좌표와 unresolved 목록을 기록한다. 기존 strict manifest에 임의 필드를
추가하지 않고 versioned source report/sidecar로 분리하거나 명시적인 계약 변경을 한다.
관계가 일부만 해결되면 partial, 해결된 관계가 없으면 unavailable로 보고한다.
자동 추출의 완료와 전기적 검증을 구분한다.
기존 unit_size_um을 단일 unit 크기처럼 잘못 표기하지 않도록 전체 입력 frame의
물리 범위와 중앙 2×2/단일 unit 의미를 분리하는 계약 변경을 검토한다.

### 4. 모델 준비와 결과

전체 frame의 모든 전극/CNT/valid에 동일한 종횡비 보존 resize+pad를 적용한다.
원본 좌표 및 실제 적용된 x/y resize 크기, padding을 저장한다.
원본 pitch는 0.1로 유지하고 모델 grid의 유효 pitch는 구분해 기록한다.
단순 축소 배율의 반올림 차이까지 좌표 변환에 반영한다.
기존 전극별 self/CNT union/rest/valid 및 endpoint 특징 경로를 활용한다.
물리 크기 conditioning은 기본 off를 유지한다.
고정 grid 숫자는 실제 이미지 shape/최소 패턴 및 메모리 검증 후 선택하며 추정하지 않는다.
축소 시 전극 소실/bridge 분리 검사를 유지한다.
전체 제품 유사도 검색이 대응 위치 검색을 제공한다고 주장하지 않는다.

### 5. 범위 및 인수 기준

첫 구현 대상은 DL1 PNG source adapter, 연결 추출기, import 검증 CLI/보고서,
기존 학습·검색으로의 연결, 사용 문서다. DL2 및 compare 수치 계산은 변경하지 않는다.
기존 NPY 계약 경로와 기존 checkpoint 호환성은 보존하거나 명시적으로 버전 거부한다.
실제 PNG 샘플 없이 실제 데이터 호환/검색 성능을 완료로 표시하지 않는다.

필수 합성 검증:
- PNG polarity, 순번 zero padding 및 두 파일명 구분자, shape mismatch/중복/회색 거부.
- 여러 하부 layer 중 가장 가까운 실제 겹침 layer만 선택.
- CNT/상부/하부 공통 교집합이 없는 경우 및 같은 layer 복수 후보는 미해결.
- 주변 500 pixel의 실제 패턴 보존과 원본 좌표→grid→원본 왕복.
- 입력 열거 순서와 관계없이 동일한 관계/identity 생성.
- 연결 추출 후 기존 DL1 입력 검증과 synthetic search 경로 통과.
- 실제 검색 품질은 폐쇄망 결과로 별도 평가.

이 문서는 구체화된 명세 초안이다. DL1 실행 코드 변경, Orca Run 시작,
독립 검토 또는 실데이터 검증이 수행되었다는 뜻은 아니다.

## 2026-10-13 정정 — cno 번호와 CNT 위치 (사용자)

앞 절의 "CNT도 stack 순번을 가진 파일명(`006_OCNT.png`)"과 "숫자는 모델 공통 stack 순서"는
**cno 폴더에는 해당하지 않는다.** 아래가 최신 사용자 설명이다.

- cno 폴더 안의 파일은 1부터 따로 번호가 매겨져 있다. 이 1, 2, 3 순서에는 **의미가 없다.**
- 중요한 것은 번호 뒤의 CNT 이름이다. 예: `1-OCNT.png`.
- CNT layer 종류는 세 가지다: `OCNT`, `GCNT`, `PCNT`. 모델마다 있는 종류와 개수가 다르다.
- 전극 layer와 CNT layer의 상하 순서는 파일명으로 알 수 없으므로 **순서 파일을 별도 입력으로 받는다**
  (사용자 결정). 겹침에서 자동 추정하는 방식은 채택하지 않는다.
- 이미지 해상도는 hotspot을 포함해 0.1 µm/pixel이다. 실제 CNT에는 대각선으로만 붙은 접촉이 없다.
  폐쇄망 호스트는 Windows다.

확인이 필요한 것: 한 모델에 같은 CNT 이름의 파일이 둘 이상 있을 수 있는지, 전극 layer 이름이
모델 사이에 같은지(순서 파일 하나로 모든 모델을 덮을 수 있는지).

현재 DL1 `feat/png-input`(`e67a8b2`)은 cno 번호를 모델 공통 순번으로 가정하므로 이 자료에서
`conflicting stack` 오류가 난다. 수정 전이다.
