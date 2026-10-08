# numeric_v2 합성 시험 fixture

manifest와 두 CSV는 채택된 T1 문서 예제를 복사했다. 3 case × 32 지름이며 91 observed, 1 missing, 4 N/A다. 원본 ID·case failure_type 및 pending G1/G2/G7a/G7b를 유지한다. 실자료가 아니다.

`mask_bool.npy`, `mask_01.npy`, `mask_255.npy`, `mask.png`는 각각 bool, 정수 0/1, 정수 0/255, grayscale PNG 인코딩의 같은 2×2 mask다. 좌상단과 우하단 셀이 활성이다. pitch 0.5 um이면 사람이 계산한 CA는 2×0.25=0.5 um^2, valid 면적은 1 um^2다. fixture 배열을 변경하거나 expected 값을 측정 함수로 산출하지 않는다.

원천 asset 참조는 `{path, sha256, value_semantics: binary}`로 지정한다. `source_ca`가 있을 때 `{value, unit, frame_id, valid_hash, basis: full_mask_valid}`를 명시해야 하며 valid_hash는 canonical bool valid를 uint8 C-order bytes로 직렬화한 SHA-256이다. 동일 frame·valid domain만 보존을 대조한다. 원천 CA에 맞추어 mask를 rescale하지 않는다.

T2 reader는 synthetic canonical 형식만 실행 수락 범위다. 실자료 mode 및 기존 1.x 계약은 명시 오류이며 density 변환과 unit/panel 환산은 다음 Task와 사용자 게이트에 남긴다. 재생성을 위한 입력 hash 범위는 T1과 같고 `adapters.numeric.source_payload_hash`가 제공한다.
