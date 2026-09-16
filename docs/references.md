# 참고 방법과 본 구현의 차이

이 문서는 설계 아이디어의 출처를 기록한다. 아래 논문을 **재현 구현한 것이 아니다.**

## Ngiam et al., Multimodal Deep Learning (ICML 2011)
<https://ai.stanford.edu/~ang/papers/icml11-MultimodalDeepLearning.pdf>

- 참고한 점: 모달리티별 encoder로 표현을 만든 뒤 공유 표현으로 결합하고, 여러 입력을 복원하는 bimodal autoencoder 구조. 일부 입력이 없는 상황에서도 공유 표현을 얻는다는 문제 설정.
- 본 구현의 변경점:
  - RBM/deep belief 사전학습, 한쪽 입력을 0으로 두는 데이터 증강을 쓰지 않는다. 대신 입력별 관측 마스크와 presence gating을 사용하고 누락값을 손실에서 제외한다.
  - 오디오·비디오가 아닌 layout·hotspot 이미지, CA 곡선, Fab 분포, 형상 수치의 5개 입력을 작은 CNN/MLP로 다룬다.
  - 목적은 교차 모달리티 예측이 아니라 제품 간 비교·유사 사례 검색용 특징이다.

## Wang et al., On Deep Multi-View Representation Learning (ICML 2015)
<https://proceedings.mlr.press/v37/wangb15.html>

- 참고한 점: 복원 기반(split/deep autoencoder)과 상관 기반(DCCA/DCCAE) 다중 뷰 표현을 비교하는 관점, 그리고 PCA 같은 단순 기준선과의 비교 필요성.
- 본 구현의 변경점: DCCA/DCCAE의 상관 목적함수는 구현하지 않았다(후속 후보). 복원 기반 오토인코더와 "명시적 특징 + 표준화 + PCA" 기준선을 같은 검색 절차로 비교하게 했다. 구조측(z_struct)·Fab측(z_fab) 특징을 분리해 출력하지만 이는 뷰 간 상관 최대화나 인과적 분리가 아니다.

## Wu et al., Characterizing and Overcoming the Greedy Nature of Learning in Multi-modal DNNs (ICML 2022)
<https://proceedings.mlr.press/v162/wu22d.html>

- 참고한 점: 다중 모달 학습이 학습이 쉬운 한 모달리티에 의존하는 탐욕적 경향이 있으며, 모달리티별 기여를 따로 관찰해야 한다는 경고.
- 본 구현의 변경점: 논문의 conditional utilization rate나 balanced multi-modal learning 알고리즘은 구현하지 않았다. 대신 (1) 모달리티별 손실을 샘플 평균 후 가중 합산해 픽셀 수가 많은 이미지가 수치 입력을 압도하지 않게 하고, (2) 모달리티별 손실을 따로 기록하며, (3) 이미지 단독·수치 단독·전체 구성을 설정으로 비교하게 했다.

## 후속 후보 (첫 버전 미구현)

- MOFA+ (Argelaguet et al., Genome Biology 2020): 다중 뷰 요인분석으로 뷰별 분산 설명 비율을 해석하는 선형 기준선.
- DCCAE (Wang et al., 2015): 뷰 간 상관 목적을 추가한 표현 학습.
- GNN: 전극·layer 연결 구조를 그래프로 표현하는 방법. 전극 역할 대응과 layer 대응이 확정된 뒤 검토.

## 기타 사용 기법

- 표준화 + PCA, KMeans: scikit-learn 기본 구현.
- 제품 단위 균형 샘플링: 제품별 표본 수 역수 가중(단순 inverse-frequency).
- Spearman 순위 상관: pandas 구현.
