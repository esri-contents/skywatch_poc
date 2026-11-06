"""경로와 상수 설정. 실행 전에 INPUT 경로만 환경에 맞게 바꿉니다."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "input"
WORK = ROOT / "output"   # 모든 결과물이 저장되는 폴더

# 1) 필지: 위성 선별 산출물(05_ai_handoff/parcels_ai_view.gpkg)
PARCELS = INPUT / "parcels_ai_view.gpkg"
# 2) 국가철도공단 토지보상시스템 공개 지도 서비스에서 받은 편입 용지(section 1 = 도담~영천 1공구)와 지장물
YONGJI = INPUT / "kr_yongji_all.geojson"
YONGJI_SECTION = "1"
THNG = INPUT / "kr_thng_ref_dedup.geojson"
# 3) Esri World Imagery Wayback 영상 (EPSG:5186 GeoTIFF) 과 버전별 촬영정보 메타데이터
EPOCHS = {  # 키: 판독 열 이름, 값: (영상 파일, 메타데이터 파일)
    "2009":   ("wayback_v2014-07-02_rel3026_z17_5186.tif",  "wayback_v2014-07-02_rel3026_z17_metadata.geojson"),
    "2015_1": ("wayback_v2019-04-24_rel18063_z17_5186.tif", "wayback_v2019-04-24_rel18063_z17_metadata.geojson"),
    "2015_2": ("wayback_v2018-01-31_rel10768_z17_5186.tif", "wayback_v2018-01-31_rel10768_z17_metadata.geojson"),
    "2022":   ("wayback_v2023-08-31_rel64776_z18_5186.tif", "wayback_v2023-08-31_rel64776_z18_metadata.geojson"),
}
IMAGERY = INPUT / "wayback"

CRS = 5186                  # 모든 면적·거리 계산 좌표계 (Korea 2000 / Central Belt 2010)
MIN_TAKEN_M2 = 30.0         # 판독 대상: 편입 면적 30㎡ 이상
MIN_VALID_RATIO = 0.0       # 편입 부분 중 영상이 덮는 비율이 이 값 이하이면 '영상 없음'
CHIP_PAD_M = 12.0           # 영상 조각 여백(m)
CHIP_PX = 200               # 영상 조각 크기(px)
CUTOFF = "2015-01-02"       # 보상기준일: 국토교통부고시 제2014-884호(관보 제18421호)

# 0단계(00_georeference.py): Wayback 다운로더가 저장한 JPG(EPSG:3857 타일 모자이크)의 좌상단 좌표와 화소 크기
# 다운로더 화면에 표시된 값(줌 17: 1.194329m, 줌 18: 0.597164m)입니다.
RAW_JPG = INPUT / "wayback_raw"
JPG_GEOREF = {  # 줌: (화소 크기 m, 좌상단 화소 중심 X, Y) in EPSG:3857
    "z17": (1.194329, 14285163.939324, 4443130.982996),
    "z18": (0.597164, 14285163.640742, 4442978.407522),
}


# 2단계(02_interpret.py): 판독 기준표를 수치로 옮긴 규칙의 기준값
RULE = dict(
    res_m=0.5,            # 판독 격자 크기(m)
    ring_m=3.0,           # 그림자를 찾는 둘레 띠 폭(m)
    area_max=1500,        # 조각 최대 면적(㎡), 이보다 크면 지붕이 아닌 넓은 면으로 봄
    exg_max=0.01,         # 식생지수(ExG) 상한: 이보다 크면 식생
    lstd_max=4,           # 지붕면 밝기 균일성: 조각 안 L 표준편차 상한
    contrast_min=10,      # 주변 대비 밝기 차이 하한
    rect_lo=0.65, rect_hi=1.3,   # 최소 외접 사각형 대비 면적 비율 (직선 윤곽)
    solidity_min=0.75,    # 볼록도 하한
    elong_max=4,          # 장단축비 상한 (선로, 도로, 화차 제외)
    shadow_dL=25,         # 그림자 화소: 조각보다 L이 이만큼 어두움
    shadow_frac=0.1,      # 둘레 띠 중 그림자 화소 비율 하한
    contrast_strong=15,   # 그림자가 약해도 이 대비 이상이면 그림자 조건 충족으로 봄
    area_min_strong=30,   # '있음' 최소 면적(㎡)
    area_min_weak=12,     # '불확실' 후보 최소 면적(㎡)
    fin_strong=0.7,       # '있음'은 조각의 70% 이상이 편입 부분 안
)
