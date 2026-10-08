# T6 손계산 oracle

이 expected는 실행 결과나 피시험 함수에서 생성하지 않았다. comparison.json은 리포트의 의미 있는 필드를 선택한 projection이고 summary.csv는 summary CSV의 첫 9개 열 projection이다. 나머지 계약/rounding/coverage 필드는 별도 시험에서 확인한다.

reference AB={0,1,2}, AC={2,3}, DE={1,3,4}, EF={4,5}, vertical AB={0}. target AB={0,1}, AC={1,2}, DE={0,3,4}, EF={4,5}, vertical AB={0}. 모든 pitch=1 um, 활성은 d=5에만 있으며 synthetic per-bin density는 d5=1 um^-2, 나머지 관측 0이다. 두 vertical d1/d2는 근거 있는 N/A다. source frame은 reference 1x6, target 2x3으로 서로 다르며 overlay하지 않는다.

reference unit(6,1), panel(60,10): N=100; target unit(3,2), panel(30,10): N=50. 따라서 panel line=400/150, pixel=200/150, transfer=200/50, delta=-250/-50/-150이다. 공유 bucket 반복 링크는 면적을 나누거나 중복 합산하지 않는다.
