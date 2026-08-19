# PyMOL 에서 AF3 결과를 pLDDT 색으로 보는 스크립트
# 만든 것: af3_visualize.py
#
# 쓰는 법
#   pymol pymol_색칠.pml
# 또는 PyMOL 을 먼저 띄운 뒤
#   @pymol_색칠.pml
#
# 왜 이 색이 맞는가
#   AF3 가 쓴 mmCIF 의 B_iso_or_equiv 열에 원자별 pLDDT(0~100)가 그대로 들어 있다.
#   cAbLys3_1MEL: 원자 2101개 확인, 최대 차 0.0100 (mmCIF 소수 2자리 반올림 범위). B_iso_or_equiv = pLDDT 로 확정 [반올림 차이 2개]
#   그래서 B값을 그대로 색 기준으로 쓰면 EBI AlphaFold DB 와 같은 색이 된다.
#
# 색 기준 (AlphaFold DB 와 같다)
#   90 이상  파랑    매우 높음. 골격도 측쇄도 믿을 만하다
#   70~90    하늘    높음. 골격은 믿을 만하다
#   50~70    노랑    낮음. 조심해서 봐라
#   50 미만  주황    매우 낮음. 무질서 영역일 가능성이 크다

reinitialize
set assembly, ""
set cartoon_transparency, 0
bg_color white

load ../cplx_out/cAbLys3_1MEL/cAbLys3_1MEL_model.cif, cAbLys3_1MEL

hide everything
show cartoon

# pLDDT 색칠
set_color af3_vhigh, [0.051, 0.341, 0.827]
set_color af3_high,  [0.396, 0.796, 0.953]
set_color af3_low,   [1.000, 0.859, 0.075]
set_color af3_vlow,  [1.000, 0.490, 0.271]

# 주의: PyMOL 선택 문법에는 '>=' 가 없다. 'b >= 50' 은
#   Error: b > = 50<--
# 으로 죽는다 (실측). 그래서 낮은 색부터 칠하고 위에 덧칠하는 방식을 쓴다.
# 아래 4줄은 순서가 중요하다. 바꾸지 마라.
color af3_vlow,  all
color af3_low,   b > 50
color af3_high,  b > 70
color af3_vhigh, b > 90

# 낮은 신뢰 구간만 따로 보고 싶을 때 (주석을 풀어라)
# select low_conf, b < 70
# show sticks, low_conf

set ray_opaque_background, 0
orient
zoom all, 2

# 스펙트럼으로 보고 싶으면 위의 color 4줄을 지우고 이 줄을 써라
# spectrum b, orange_yellow_cyan_blue, minimum=0, maximum=100

print "AF3 pLDDT 색칠 완료. 파랑=확실, 주황=불확실."
