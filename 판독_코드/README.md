# 편입 필지 지장물 판독 코드

중앙선 도담~영천 복선전철 매포 구간의 편입 필지 165곳에 대해, 편입되는 부분에 구조물이 있었는지를 2009·2015·2022년 고해상도 영상으로 판독합니다. 결과는 필지·시점별 '있음 / 없음 / 불확실 / 영상 없음'입니다.

## 1. 구성

| 단계 | 코드 | 입력 | 출력 |
|---|---|---|---|
| 0. 영상 좌표 변환 | `00_georeference.py` | `input/wayback_raw/*.jpg` | `input/wayback/*_5186.tif` |
| 1. 판독 준비 | `01_prepare.py` | 필지, 편입 용지, 영상, 촬영정보 | `output/targets.gpkg`, `taking_boundary.gpkg`, `chips/`, `sheets/`, `interpretation_template.csv` |
| 2. 판독 | `02_interpret.py` | `output/targets.gpkg`, 영상 | `output/interpretation_rule.csv`, `candidates.gpkg`, `compare_result.txt` |

설정(경로, 기준값)은 `config.py`에 있습니다.

## 2. 입력 (`input/`)

| 파일 | 내용 | 출처 |
|---|---|---|
| `parcels_ai_view.gpkg` | 필지 2,133개 (pnu, 지번, 지목) | VWorld 연속지적도 기반 위성 선별 산출물 |
| `kr_yongji_all.geojson` | 편입 용지 경계. 매포 구간은 `section = 1` | 국가철도공단 토지보상시스템 공개 지도 서비스 |
| `kr_thng_ref_dedup.geojson` | 공단 물건조사 지장물 (참고용) | 같은 서비스 |
| `wayback_raw/*.jpg` | 고해상도 영상 4장 | Esri World Imagery Wayback |
| `wayback/*_metadata.geojson` | 영상별 실제 촬영일 | Esri World Imagery Wayback |

| 판독 열 | 영상 버전 | 실제 촬영일 | 해상도 |
|---|---|---|---|
| `2009` | rel3026 | 2009-12-21 | 흑백 약 1m |
| `2015_1` | rel18063 | 2015-03-28(남쪽) / 2015-05-31(북쪽) | 약 1m |
| `2015_2` | rel10768 | 2015-05-31 | 약 1m |
| `2022` | rel64776 | 2022-11-06 / 2023-01-25 | 약 0.5m |

## 3. 출력 (`output/`)

| 파일 | 내용 |
|---|---|
| `targets.gpkg` | 판독 대상 165필지의 편입 부분 도형과 속성 (아래 표) |
| `taking_boundary.gpkg` | 분석 범위 안의 편입 용지 |
| `chips/` | 필지·시점별 영상 조각 660장. 빨간 선은 편입 부분, 흰 선은 필지 경계 |
| `sheets/` | 8필지씩 묶은 판독 시트 21장 (왼쪽부터 2009, 2015_1, 2015_2, 2022) |
| `interpretation_template.csv` | 직접 판독할 때 쓰는 빈 양식 |
| `interpretation_rule.csv` | 판독 결과 |
| `candidates.gpkg` | 판독 근거가 된 건물 후보 조각. `grade`가 `strong`이면 '있음', `weak`이면 '불확실'의 근거 |
| `compare_result.txt` | 기준 판독과의 비교 (`--compare` 실행 시) |

`targets.gpkg` 필드

| 필드 | 뜻 |
|---|---|
| `hist_id` | 판독 번호 (북쪽부터 M001~M165) |
| `pnu`, `ri`, `jibun`, `address`, `jimok` | 필지 정보 |
| `parcel_m2`, `taken_m2` | 필지 면적, 편입 면적(㎡) |
| `img_<시점>_date` | 실제 촬영일 |
| `valid_<시점>` | 편입 부분 중 영상이 덮는 비율. 0이면 영상 범위 밖 |

`interpretation_rule.csv` 예시

| hist_id | address | jimok | struct_2009 | struct_2015_1 | struct_2015_2 | struct_2022 |
|---|---|---|---|---|---|---|
| M002 | 우덕리 221-2 | 철도용지 | 있음 | 있음 | 있음 | 없음 |

## 4. 처리 방법

판독 준비 (`01_prepare.py`)
1. 필지와 편입 용지를 겹쳐 편입 부분을 계산하고, 편입 면적 30㎡ 이상인 필지를 고릅니다.
2. 북쪽부터 번호를 붙이고, 영상 메타데이터에서 위치별 실제 촬영일을 가져옵니다.
3. 편입 부분 중 영상이 덮는 비율을 계산하고, 영상 조각과 판독 시트를 만듭니다.

판독 (`02_interpret.py`)

편입 부분 주변 영상을 0.5m 격자로 읽고, Felzenszwalb 그래프 분할로 비슷한 색·밝기의 화소를 조각으로 묶습니다. 조각마다 아래 세 항목을 계산합니다. 각 항목의 근거는 7장에 있습니다.

| 항목 | 조건 (`config.py`의 `RULE`) |
|---|---|
| 지붕면 | 식생지수 ExG ≤ 0.01, 밝기 표준편차 ≤ 4, 주변과 밝기 차이 ≥ 10 |
| 직선 윤곽 | 최소 외접 사각형 대비 면적 0.65~1.3, 볼록도 ≥ 0.75, 장단축비 ≤ 4 |
| 그림자 | 둘레 3m 안에 밝기가 25 이상 어두운 화소 10% 이상, 또는 주변 대비 ≥ 15 |

필지·시점의 판독 결과는 아래 기준으로 정합니다.

| 결과 | 조건 |
|---|---|
| 있음 | 세 항목을 모두 만족하는 30㎡ 이상 조각이 있고, 그 조각의 70% 이상이 편입 부분 안 |
| 불확실 | 지붕면·윤곽만 만족하거나, 12~30㎡이거나, 경계에 걸친 조각만 있음 |
| 없음 | 해당 조각 없음 |
| 영상 없음 | `valid` = 0 |

## 5. 실행

Python 3.10 이상. PowerShell이나 터미널에서 이 폴더로 이동해 입력합니다.

```
pip install -r requirements.txt
python 00_georeference.py
python 01_prepare.py
python 02_interpret.py --compare interpretation.csv
```

`--compare`를 빼면 판독만 합니다. 결과는 `output/`에 덮어씁니다.

다른 구간에 쓸 때는 `input/` 자료를 바꾸고 `config.py`의 파일 이름, `YONGJI_SECTION`, `JPG_GEOREF`를 맞춘 뒤 실행합니다.

## 6. 기준 판독과 비교

`interpretation.csv`(데모 보고서에 쓴 판독 결과)와 비교한 결과입니다. 165필지 × 4시점 = 660칸.

| 기준 \ 코드 | 있음 | 불확실 | 없음 | 영상 없음 |
|---|---|---|---|---|
| 있음 (9) | 4 | 1 | 4 | 0 |
| 불확실 (40) | 0 | 4 | 36 | 0 |
| 없음 (603) | 0 | 32 | 571 | 0 |
| 영상 없음 (8) | 0 | 0 | 0 | 8 |

- 전체 일치율 88.9%, 필지별 구조물 유무 일치율 97.0%
- 코드의 '있음' 4칸은 모두 기준과 일치합니다. 기준의 '있음' 9칸 중 4칸을 찾았고, 놓친 5칸은 대부분 2009년 흑백 영상입니다.
- 기준값을 이 자료에 맞춰 정했으므로 다른 구간에서는 일부 필지로 다시 확인한 뒤 써야 합니다.

## 7. 방법의 근거

### 7-1. 판독 항목: 영상 판독 요소

항공사진·위성영상 판독은 색조(tone), 크기(size), 형태(shape), 질감(texture), 패턴(pattern), 그림자(shadow), 위치(site), 연관(association) 같은 판독 요소로 대상을 식별합니다(Estes et al., 1983; Lillesand et al., 2015). 이 코드는 건물 식별에 쓰이는 요소 중 다음 넷을 수치로 계산합니다.

| 판독 요소 | 코드의 계산값 |
|---|---|
| 색조 | 조각 안 밝기의 균일성(표준편차), 주변과의 밝기 차이 |
| 형태 | 최소 외접 사각형 대비 면적, 볼록도, 장단축비 |
| 크기 | 조각 면적 (있음 30㎡ 이상, 불확실 12㎡ 이상) |
| 그림자 | 조각 둘레의 어두운 화소 비율 |

### 7-2. 조각 단위 분석: 객체 기반 영상분석(OBIA)

고해상도 영상에서는 건물 하나가 여러 화소로 이루어지므로, 화소를 하나씩 분류하기보다 먼저 영상을 의미 있는 조각(객체)으로 나눈 뒤 조각의 분광·형태·문맥 특성으로 분류하는 방식이 쓰입니다(Blaschke, 2010). 이 코드는 분할로 조각을 만든 다음, 조각별로 색조·형태·크기·그림자를 계산해 판단합니다.

### 7-3. 분할: Felzenszwalb 그래프 분할

Felzenszwalb & Huttenlocher(2004)는 화소를 노드, 이웃 화소 간 색 차이를 간선 가중치로 둔 그래프에서, 두 영역 사이의 차이가 각 영역 내부의 차이보다 클 때만 경계로 남기는 방식으로 영역을 합칩니다. 축척 값 k가 클수록 큰 영역이 만들어지고, 분할 전에 가우시안 평활(σ)을 하며, 최소 크기보다 작은 영역은 이웃과 합칩니다. 이 코드는 scikit-image 구현을 쓰며 k(scale) = 60, σ = 0.6, 최소 크기 6㎡로 둡니다.

### 7-4. 식생 제외: 초과녹색지수(ExG)

Woebbecke et al.(1995)은 가시광 RGB 영상에서 식물과 흙·잔재물을 구분하는 색 지수를 비교했고, 초과녹색지수 ExG = 2g - r - b를 제안했습니다. 여기서 r, g, b는 R, G, B를 R + G + B로 나눈 색좌표입니다. 이 코드는 같은 식으로 ExG를 계산해, 조각 평균 ExG가 0.01을 넘으면 식생으로 보고 지붕 후보에서 뺍니다. 2009년 흑백 영상은 r = g = b라 ExG가 0이므로 이 조건이 적용되지 않습니다.

### 7-5. 그림자: 건물과 그림자의 인접 관계

Irvin & McKeown(1989)은 항공사진에서 건물이 그림자를 드리운다는 관계를 이용해 건물 후보를 확인하고 높이를 추정했습니다. Ok, Senaras & Yuksel(2013)은 고해상도 위성영상에서 먼저 그림자를 찾고, 그림자와 건물 사이의 방향 관계로 건물이 있을 만한 영역을 만든 뒤 GrabCut으로 건물 영역을 분할했습니다. 이 코드는 두 연구의 기본 관계, 즉 건물 조각 옆에 어두운 그림자 영역이 붙어 있다는 점만 씁니다. 태양 방향은 고려하지 않고 조각 둘레 3m 띠 전체에서 어두운 화소 비율을 봅니다.

### 7-6. 기준값

문헌에서 가져온 것은 판독 요소와 처리 기법이고, 밝기 차이 10, 장단축비 4, 그림자 비율 10% 같은 기준값은 이 구간 자료에 맞춰 정한 값입니다. 다른 구간에 쓸 때는 일부 필지를 직접 판독해 `--compare`로 확인하고 `RULE`을 조정해야 합니다.

### 참고문헌

- Blaschke, T. (2010). Object based image analysis for remote sensing. ISPRS Journal of Photogrammetry and Remote Sensing, 65(1), 2-16.
- Estes, J. E., Hajic, E. J., & Tinney, L. R. (1983). Fundamentals of image analysis: Analysis of visible and thermal infrared data. In R. N. Colwell (Ed.), Manual of Remote Sensing (2nd ed., Vol. 1, pp. 1039-1040). American Society of Photogrammetry.
- Felzenszwalb, P. F., & Huttenlocher, D. P. (2004). Efficient graph-based image segmentation. International Journal of Computer Vision, 59(2), 167-181.
- Irvin, R. B., & McKeown, D. M. (1989). Methods for exploiting the relationship between buildings and their shadows in aerial imagery. IEEE Transactions on Systems, Man, and Cybernetics, 19(6), 1564-1575.
- Lillesand, T. M., Kiefer, R. W., & Chipman, J. W. (2015). Remote Sensing and Image Interpretation (7th ed.). Wiley.
- Ok, A. O., Senaras, C., & Yuksel, B. (2013). Automated detection of arbitrarily shaped buildings in complex environments from monocular VHR optical satellite imagery. IEEE Transactions on Geoscience and Remote Sensing, 51(3), 1701-1717.
- Woebbecke, D. M., Meyer, G. E., Von Bargen, K., & Mortensen, D. A. (1995). Color indices for weed identification under various soil, residue, and lighting conditions. Transactions of the ASAE, 38(1), 259-269.
