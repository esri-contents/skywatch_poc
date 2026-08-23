# Building Change Intelligence PoC — 인수인계 문서

**작성일**: 2026-08-23
**저장소**: https://github.com/esri-contents/skywatch_poc
**AOI**: 경기도 고양시덕양구 창릉동 (행정동 경계, 10.99km²)
**T1/T2**: Sentinel-2 2022-05-17 / 2024-05-31

---

## 1. 프로젝트 목적

광역 위성/항공영상을 전수 분석해 건축물 변화 후보를 자동으로 줄이고,
건축·행정정보와 결합해 현장조사가 필요한 대상을 우선순위화하는
의사결정지원 파이프라인의 Baseline을 만드는 것.

향후 동일 파이프라인에 SkyWatch 등 상용 위성영상을 투입해 운영
가능성을 검증할 예정이라, 이번 결과는 데모가 아니라 **재사용 가능한
Baseline**이어야 한다는 게 원래 요구사항이었다.

> **이 시스템은 불법건축물을 자동 판정하지 않는다.** 자세한 내용은
> 7번 섹션 참고.

---

## 2. 확보한 데이터 — 전부 실제 공공데이터, 가짜 데이터 없음

| 데이터 | 출처 | 상태 | 비고 |
|---|---|---|---|
| AOI | vuski/admdongkor (통계청 SGIS 기반) | 확보 | 창릉동 행정동 경계, 10.99km². 정식 지구계 아닌 근사치 (6번 참고) |
| T1/T2 영상 | Sentinel-2 L2A (Microsoft Planetary Computer) | 확보 | 10m, 2022-05-17(구름 0.01%) / 2024-05-31(구름 2.1%) |
| 건물 footprint | VWorld WFS `lt_c_spbd` | 확보 | AOI 내 2,737개, 층수·PNU 포함 |
| 건축물대장 | 공공데이터포털 `BldRgstHubService` | 확보 | 4개 법정동 전량 3,615건, 사용승인일/연면적/주용도 |
| NGII 고해상 정사영상 | 국토정보플랫폼 | 미확보 | 로그인+GUI 전용 프로그램 필요, 자동화 불가 확인됨 |
| 정식 지구계 | 국토부 고시 제2021-1285호 | 부분 | 원본 PDF(398p) 확보, 필지 목록 확인. 완전 추출은 보류(6번) |

---

## 3. 파이프라인 구조

```text
T1(2022) + T2(2024) 원본 밴드
  → 재투영 + AOI clip + 밴드스택    (src/preprocessing/raster_preprocess.py)
  → 정합 검증(ECC)                  (src/preprocessing/alignment.py)
  → Change Detection                (src/change_detection/*.py)
    (pixel diff + SSIM + edge/texture 앙상블)
  → 후처리 + Polygon화              (src/change_detection/postprocess.py)
  → 건물 Overlay                    (src/buildings/overlay.py)
  → 건축물대장 검증(STEP 13)         (src/buildings/validation.py)
  → 변화유형 분류                    (src/buildings/classify.py)
  → 우선순위 점수화                  (src/scoring/priority.py)
  → GeoPackage / GeoJSON / CSV 출력
```

전체 진입점: `src/pipeline.py`의 `run_change_detection()`.
설정값(임계값, 가중치)은 전부 `config/config.yaml`에서 관리 — 하드코딩 없음.
`tests/`에 32개 pytest 단위테스트 작성, 전체 통과 확인.

### 재실행 방법 (내일 이어서 돌릴 때)

```bash
# Windows에서 PROJ 충돌 회피 필요 (README "Windows PROJ 충돌 주의" 참고)
PROJ_LIB=".venv/Lib/site-packages/rasterio/proj_data" GDAL_DATA="" \
python -c "
from src.pipeline import run_change_detection
run_change_detection(
    'data/processed/imagery/2022_stack.tif',
    'data/processed/imagery/2024_stack.tif',
    'data/aoi/changneung_test_aoi.gpkg',
    'data/processed/buildings/changneung_buildings_clipped.gpkg',
    t1_date='2022-05-17', t2_date='2024-05-31',
    out_dir='outputs',
    building_register_path='data/raw/building_register/changneung_title_info.json',
)
"
```

`data/raw/`, `data/processed/`, `.venv/`는 git에 커밋되지 않는다
(`.gitignore` 참고). 새 환경에서 이어받으면 `src/data/*.py` 스크립트를
순서대로(AOI → 건물 → Sentinel-2 → 건축물대장) 다시 실행해 원본
데이터를 재생성해야 한다.

---

## 4. 실행 결과 (2022-05-17 vs 2024-05-31)

| 단계 | 수치 |
|---|---|
| 정합 오차 (ECC) | displacement 1.26m (0.126px), ecc_score 0.979 |
| 변화 픽셀 비율 | 1.95% |
| Change Polygon | 33개 (평균 면적 7,351m²) |
| 건물과 연계된 변화 | 57개 건물 (전체 2,737개 중) |
| 건축물대장 매칭 | 67.7% (1,854/2,737 — PNU 1,771 + 도로명주소 보강 83) |
| 최종 후보 (건물 기준) | 76건 |
| **최종 후보 (현장 기준, site_id)** | **33곳** |

### 변화유형 분류

| 유형 | 건수 | 판정 근거 |
|---|---|---|
| NEW_BUILDING | 20 | 1건은 사용승인일(2024-05-13)로 확정, 나머지는 change_ratio≥0.5 휴리스틱 |
| EXPANSION_OR_RECONSTRUCTION | 37 | 다수가 사용승인일(1984~2017년 등)로 확정 — 신축 아닌 기존 건물임을 증명 |
| OTHER_CHANGE | 16 | 건물 미교차, 토지조성/도로 등 추정 |
| DEMOLITION | 3 | 건물 미교차 + 고신뢰 변화점수, 철거 추정(약한 근거) |

### 현장조사 우선순위

| 등급 | 건물 수 | 실제 현장 수 |
|---|---|---|
| HIGH | 32 | **11** |
| MEDIUM | 43 | 28 |
| LOW | 1 | — |

---

## 5. 육안검수로 발견한 것

HIGH 32건의 T1/T2 이미지 chip을 전부 직접 열어봤다. 10m 해상도라
개별 픽셀을 "이건 확실히 건물이다"로 단정하긴 어려웠지만, 훨씬 중요한
패턴을 발견했다:

> **HIGH 32건 → 실제로 겹치는 change_polygon(현장)은 11곳뿐.**
> 대형 현장 2곳(추정: 공사 중인 대단지)이 각각 건물 11개, 10개에 걸쳐
> 있어 21건(66%)을 차지했다. 같은 공사장이 건물 footprint 여러 개에
> 걸쳐 중복 집계된 것이다.

`overlay.py`에 `site_id` 컬럼을 추가해 이제 `building_change_results.gpkg`를
`site_id`로 group-by하면 실제 현장 수를 바로 셀 수 있다.

**현장조사 안내 시 반드시 건물 개수(76, 32)가 아니라 site_id 기준
현장 수(33, 11)로 말해야 한다.** 그렇지 않으면 같은 공사장을 여러 번
방문시키게 된다.

---

## 6. 한계 6가지와 실제로 극복한 정도

| # | 한계 | 대응 결과 |
|---|---|---|
| 1 | AOI가 정식 지구계 아님(행정동 근사) | **부분 극복** — 국토부 고시 원본 PDF(398p) 확보, 붙임1 필지 목록 확인 + VWorld 연속지적도로 필지별 폴리곤 조회 가능함을 검증. 단 파싱 품질·구버전(2021년 2차) 문제로 완전 추출은 보류 |
| 2 | Sentinel-2 10m 해상도 | **근본적 한계**, 극복 불가(무료 소스 한계) — 대신 실측 근거(평균 변화면적 7,351m²)로 SkyWatch 비교자료화 |
| 3 | NEW/EXPANSION 구분이 휴리스틱 | **극복** — 건축물대장 사용승인일로 실제 근거 판정 대체(67.7% 커버, 나머지만 휴리스틱) |
| 4 | PNU 조인 64.7%만 매칭 | **개선** — 도로명주소 보강 조인 추가로 67.7%까지 개선. 원인 완전 규명은 못함 |
| 5 | Ground Truth 없음 | **보완** — Human Validation Sample 63건 준비 + Claude 육안검수(HIGH 32건 → 실제 11곳 발견, 5번 참고) |
| 6 | notebooks/tests 비어있음 | **극복** — pytest 32개 작성, 전체 통과 확인 |

---

## 7. "불법 여부" 관련 정리 (중요, 반드시 숙지)

> **이 시스템은 불법건축물을 판정하지 않는다.** HIGH/MEDIUM/LOW는
> "영상 변화가 크고 + 건물과 겹치고 + 보유한 행정정보로는 설명 안 됨"
> 이라는 뜻일 뿐이다. "설명 안 됨"은 실제 위반일 수도, 우리 조인(67.7%)이
> 그 건물의 허가 이력을 놓친 것일 수도 있다.

실제로 경기도 "위반건축물 현황" 공개데이터를 확인했으나 연도·분기별
집계 통계(288행)뿐이고, 주소·건물 단위 목록은 공개 API로 제공되지
않는다(개인 재산권 관련 정보로 추정). 즉 **"이 건물이 위반이다"를
자동으로 알려주는 공개 데이터 자체가 없다.** 실제 위반 여부 확인은
고양시 도시균형개발과 등에 개별 열람 신청 또는 현장조사로만 가능하다.

---

## 8. 산출물 파일 목록

### 벡터 (ArcGIS Online 업로드 대상)

| 파일 | 좌표계 | 용도 |
|---|---|---|
| `outputs/vectors/building_change_results.geojson` | WGS84 | 최종 76건 결과 — **AGOL 업로드 1순위** |
| `outputs/vectors/change_polygons.geojson` | WGS84 | 변화탐지 원 폴리곤 33개 |
| `outputs/vectors/changneung_aoi.geojson` | WGS84 | AOI 경계 |
| `outputs/vectors/changneung_buildings.geojson` | WGS84 | 건물 footprint 2,737개 (참고용, 2.4MB) |
| `outputs/vectors/*.gpkg` (동일 레이어) | EPSG:5186 | ArcGIS Pro 작업용 (분석 좌표계 유지) |

### 래스터

| 파일 | 내용 |
|---|---|
| `data/processed/imagery/2022_stack.tif` / `2024_stack.tif` | T1/T2 4밴드 스택 (B02/B03/B04/B08) |
| `outputs/rasters/change_probability.tif` | 변화확률 (0~1) |
| `outputs/rasters/change_mask.tif` | 이진 변화마스크 |

### 리포트

| 파일 | 내용 |
|---|---|
| `outputs/reports/poc_summary.md` | Funnel, 한계, 육안검수 결과 상세 |
| `outputs/reports/skywatch_requirements.md` | SkyWatch 비교 요구사항 (실측 근거 포함) |
| `outputs/reports/human_validation_sample.csv` | 사람 검수용 표본 63건 |
| `outputs/reports/data_inventory.csv` | 보유 데이터 메타데이터 전수 조사 |
| `outputs/reports/building_change_summary.csv` | 최종 결과 속성 CSV (geometry 제외) |

---

## 9. 내일 ArcGIS Online 업로드 가이드

1. **로그인 → Content → New item → Your device**에서
   `building_change_results.geojson`을 업로드한다. "Publish this file
   as a hosted feature layer"를 선택하면 자동으로 Feature Layer가 생성된다.
2. 같은 방식으로 `change_polygons.geojson`, `changneung_aoi.geojson`도
   업로드한다. 건물 전체 레이어(`changneung_buildings.geojson`,
   2,737개)는 필요할 때만 — 용량이 커서(2.4MB) 굳이 항상 켜둘 필요는 없다.
3. 래스터는 `2022_stack.tif` / `2024_stack.tif` / `change_probability.tif`를
   각각 업로드해 **Tile Layer**로 publish한다. 4밴드 스택은 밴드 조합
   (3,2,1 = R,G,B)을 렌더러에서 지정하면 실제 컬러로 보인다.
4. **심볼로지 설정**: `building_change_results` 레이어를 두 벌
   추가해서 하나는 `change_type` 필드로 카테고리 심볼(4색), 다른
   하나는 `inspection_priority` 필드로 3단계 심볼(HIGH 빨강/MEDIUM
   주황/LOW 노랑)을 적용하면 목적별로 스위치할 수 있다.
5. **Web Map으로 묶기**: 2022 영상(배경) → 2024 영상 → AOI 경계 →
   change_polygons → building_change_results 순서로 레이어를 쌓고,
   Web Map으로 저장 후 공유한다.
6. 팝업(Pop-up) 설정 시 `change_type`, `priority_score`, `site_id`,
   `classification_note`, `useAprDay` 필드를 노출하면 현장조사자가
   바로 판단 근거를 볼 수 있다.

> GeoJSON 파일은 좌표계 문제(EPSG:5186 → WGS84 미변환)를 오늘
> 발견해서 이미 수정했다. 지금 리포에 있는 `.geojson` 파일들은 전부
> WGS84로 정상 저장되어 있어 그대로 업로드하면 된다.

---

## 10. 남은 일 / 필요한 결정

| 항목 | 필요한 것 |
|---|---|
| 정식 지구계 완전 추출 | 398페이지 필지 목록 파싱 + VWorld 지적도 조회 + union (별도 세션 권장) |
| NGII 고해상 정사영상 | map.ngii.go.kr 수동 로그인 및 다운로드 (사용자 액션) |
| ArcGIS Online 발행 자동화 | ArcGIS API for Python 스크립트는 미작성 — AGOL 계정/Publisher 권한 확인되면 진행 가능 |
| Human Validation Sample 실제 검수 | 준비된 63건 CSV를 사람이 열어서 manual_class/is_correct 채우기 |

---

## 참고 링크

- 결과 갤러리(이미지): https://claude.ai/code/artifact/52cdd24a-90f0-450d-bf4e-4c1d9a915aee
- 인수인계 문서(웹, 이 문서의 원본): https://claude.ai/code/artifact/f5ce108a-4c8f-427f-876b-70ca457fd35e
- 저장소: https://github.com/esri-contents/skywatch_poc
