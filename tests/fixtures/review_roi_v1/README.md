# 수작업 review ROI 원천과 기대값

원본 frame=(8,10), origin=(100,200) um, pitch=0.5 um이다. valid는 (0,0) 한 셀만 제외한다. case A의 AB와 case B의 AC 모두 d5에서 rows[2,6), cols[4,8)의 직사각형을 덮는다. distinct electrode count=3, 영역 16셀×0.25=4 um², full valid 면적=(80−1)×0.25=19.75 um²다. bbox=(102,201,104,203), centre=(103,202)를 수작업으로 expected.json에 썼다.

map 두 행은 G7b 및 full valid hole 때문에 blocked다. case A를 명시한 manual=(102.5,201.5)는 완전한 원본 asset 연결과 관측 위치를 갖고 ready다. case 없는 manual은 A를 자동 선택하지 않고 blocked다. 이 fixture는 합성 내부 형식이며 실제 원천/G4 crop 크기 승인이 아니다.
