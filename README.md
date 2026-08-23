# Goyang Changneung Building Change Intelligence PoC

## 프로젝트 목적

대한민국 **고양 창릉 3기 신도시 지역**을 대상으로, 공공데이터만을 활용해
과거(T1)·현재(T2) 정사영상 간 건축물 변화를 탐지하고, 건물통합정보 및
건축물대장(행정정보)과 공간적으로 연계하여 **현장조사가 필요한 후보를
자동으로 선별**하는 의사결정지원 시스템의 Baseline을 구축한다.

이 PoC는 단순 데모가 아니라, 향후 SkyWatch 등 상용 위성영상을 동일
파이프라인에 투입해 운영 가능성을 검증하기 위한 **재사용 가능한 Change
Detection Pipeline**을 목표로 한다.

> 이 시스템은 불법건축물을 자동 판정하지 않는다. 영상에서 외관 변화가
> 탐지된 건축물을 행정·건축물 정보와 결합하여 현장확인이 필요한 후보를
> 선별하는 것이 목적이다.

## 업무 배경

국토안전관리원 → SkyWatch → LH 및 지자체로 이어지는 실사용 GeoAI Change
Intelligence 체계 확장을 염두에 두고, 우선 공공 항공정사영상과 공공 건축·
행정 데이터만으로 파이프라인의 기술적/사업적 타당성을 검증한다.

## Architecture

```text
과거 영상(T1) + 현재 영상(T2)
    -> 영상 전처리 및 정합
    -> Change Detection
    -> Change Polygon 생성
    -> 건물통합정보 / 건축물대장 공간연계
    -> 신축 / 철거 / 증축·개축 의심 / 기타 변화 분류
    -> 행정정보 기반 Validation
    -> 현장조사 우선순위 산정
    -> GeoPackage / GeoJSON / Feature Class
    -> ArcGIS Pro / ArcGIS Online 시각화
```

## 대상 변화 유형

```text
NEW_BUILDING
DEMOLITION
EXPANSION_OR_RECONSTRUCTION
OTHER_CHANGE
```

## Required Data / Data Sources

| 데이터 | 필요 이유 | 공식 출처(확인됨) | 확보 방법 | 상태 |
|---|---|---|---|---|
| 2022/2024년 Sentinel-2 위성영상 (T1/T2, 1차 자동화 소스) | Change Detection 입력 (자동화 Baseline) | [Microsoft Planetary Computer STAC](https://planetarycomputer.microsoft.com/api/stac/v1) (Sentinel-2 L2A) | **자동 완료**: `src/data/download_satellite.py`, API Key 불필요. T1=2022-05-17(구름 0.01%), T2=2024-05-31(구름 2.1%, 계절 일치) 선택해 B02/B03/B04 실제 다운로드 진행/완료 | **확보 진행/완료** (`data/raw/imagery/2022`, `2024`) |
| 2022/2024년 고양 창릉 정사영상 (T1/T2, 고해상 병행 트랙) | 건물 단위 정밀 탐지용 (Sentinel-2 10m로는 개별주택 신축 탐지 어려움) | [국토지리정보원 국토정보플랫폼](http://map.ngii.go.kr/ms/map/NlipMap.do?tabGb=total) | **수동**: 회원가입/로그인 후 통합검색 → 정사영상 선택 → 전용 대용량 파일전송 프로그램으로 다운로드. GUI 전용이라 자동화 불가 확인됨 (TIFF, 도시지역 12cm/일반지역 25cm, 2010년 이후 촬영분만 제공) | **미확보 (선택적, 병행 진행)** |
| 건물 footprint (도로명주소건물) | 건물 단위 Overlay/분류 | [VWorld WFS `lt_c_spbd`](https://www.vworld.kr/) (도로명주소건물, GetCapabilities로 확인) | **자동 완료**: `src/data/download.py::download_vworld_wfs_layer` (bbox 4분할 재귀 페이징으로 STARTINDEX 상한 1000 우회) + `src/data/build_buildings.py`로 AOI clip. AOI 내 2,737개 건물 확보. 속성: 층수(지상/지하), PNU, 도로명주소 등 — **연면적/사용승인일/주용도 등 상세 건축물대장 속성은 없음** | **확보 완료** (`data/processed/buildings/changneung_buildings_clipped.gpkg`) |
| 건축물대장 / 인허가 정보 (사용승인일, 주용도, 연면적 등) | 행정정보 Validation, 신축/증축 판별 근거 | [국토교통부_건축HUB_건축물대장정보 서비스](https://www.data.go.kr/data/15134735/openapi.do) (공공데이터포털) | **자동 완료**: `https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo` (표제부). AOI 내 4개 법정동 전량 페이징 수집 → 3,615건. `src/buildings/validation.py`로 건물 footprint과 PNU(산여부 제외 9자리) 조인 → **64.7%(1,771/2,737) 매칭**, STEP 13 행정정보 검증에 실제 반영됨 | **확보 완료** (`data/raw/building_register/changneung_title_info.json`) |
| 고양 창릉 AOI (행정동 경계 기반 근사치) | AOI 정의 | [vuski/admdongkor](https://github.com/vuski/admdongkor) (통계청 SGIS 행정동 경계 기반 오픈데이터, ver20240701) | **자동 완료**: `src/data/build_aoi.py` — 경기도 고양시덕양구 창릉동(행정동) 단독 사용. 면적 10.99km²로 공식 사업면적(8.12km²)과 같은 자릿수 → 창릉지구가 창릉동 안에 포함된다는 정황과 일치 | **확보 완료** (`data/aoi/changneung_test_aoi.gpkg`) |

**AOI 관련 주의**: 이는 사용자 요청("창릉지구가 소속된 행정동 다 포함")에 따라 행정동 경계를
근사 AOI로 사용한 것이며, 정식 지구계(지형도면고시)보다 넓은 근사치다. 정식 지구계는 여전히
[국토교통부 고시 제2021-1285호 등](http://www.eum.go.kr/web/gs/gv/gvGosiDet.jsp?seq=517617)의
"지위도면" PDF에서 확보 가능하며, 확보 시 이 AOI를 교체한다. **임의로 눈대중 좌표를 만든 적은 없다** —
행정동 폴리곤은 통계청 SGIS 기반 실제 좌표다.

자세한 데이터 확보 요청은 [`outputs/reports/data_inventory.csv`](outputs/reports/data_inventory.csv) 및
대화 내 데이터 요청 항목을 참고한다. **실제 데이터가 로컬에 준비되기 전까지
Change Detection 결과는 생성하지 않는다.**

## Directory Structure

```text
changneung-change-poc/
├── config/                 # config.yaml, paths.yaml - 파라미터/경로 설정
├── data/
│   ├── raw/                # 원본 데이터 (imagery, buildings, building_register, boundaries)
│   ├── interim/            # 전처리 중간 산출물
│   ├── processed/          # 정합/정규화 완료 데이터
│   └── aoi/                # 분석 대상 지역(AOI) 경계
├── src/
│   ├── data/                # 다운로드, 검증, 메타데이터
│   ├── preprocessing/       # Raster 전처리, 정합, 정규화, Clip
│   ├── change_detection/    # Baseline, spectral/structural 변화탐지, 후처리
│   ├── buildings/           # Overlay, 분류, 행정정보 Validation
│   ├── scoring/              # 현장조사 우선순위 산정
│   ├── evaluation/           # 정확도 지표, 리포트
│   └── utils/
├── notebooks/               # 분석/검증/시각화 전용 (핵심 로직은 src/에 위치)
├── outputs/                  # rasters, vectors, maps, figures, reports
└── tests/
```

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # 이후 API Key 입력
```

GDAL/Rasterio는 OS에 따라 별도 바이너리 설치가 필요할 수 있다
(Windows: OSGeo4W 또는 conda-forge 권장).

**Windows PROJ 충돌 주의**: 이 개발 환경에서는 시스템 전역 `PROJ_LIB`
환경변수가 PostgreSQL/PostGIS의 구버전 `proj.db`를 가리키고 있어
rasterio의 재투영(`calculate_default_transform` 등)이 `CRSError`로
실패하는 문제가 실측 확인되었다. 재투영이 필요한 스크립트를 실행할 때는
venv에 번들된 proj 데이터를 명시적으로 가리켜야 한다:

```bash
PROJ_LIB=".venv/Lib/site-packages/rasterio/proj_data" GDAL_DATA="" \
  python -m src.preprocessing.raster_preprocess
```

## Execution

```bash
python -m src.pipeline \
  --t1 data/raw/imagery/2022 \
  --t2 data/raw/imagery/2024 \
  --buildings data/raw/buildings/buildings.gpkg \
  --aoi data/aoi/changneung_test_aoi.gpkg
```

**현재까지 실제 데이터로 end-to-end 실행 완료** (STEP 1~14):
- AOI, Sentinel-2 T1/T2, 건물 footprint 확보 (위 데이터 표 참고)
- T1/T2 Raster 전처리: 재투영+AOI clip+밴드 스택, 두 시기 grid 완전 일치 확인
- Baseline Change Detection (pixel diff + SSIM + edge/texture 앙상블) →
  후처리(형태학적 연산+최소면적) → Polygon화 → 건물 Overlay → 규칙기반 분류
  → Priority Scoring → GPKG/GeoJSON/CSV export

**실행 결과 (2022-05-17 vs 2024-05-31, AOI 10.99km², 건축물대장 Validation 포함)**:

```text
전체 건물 2,737개 -> Change Polygon 33개 -> 건물 연계 변화 57개
-> 건축물대장 매칭 67.7%(1,854건, PNU 1,771 + 도로명주소 보강 83)
   그중 1건은 사용승인일이 T1~T2 사이로 "설명됨"(신축 확정)
-> 최종 변화 후보 76개 (NEW_BUILDING 20 / EXPANSION_OR_RECONSTRUCTION 37 /
   OTHER_CHANGE 16 / DEMOLITION 3)
-> HIGH 32 / MEDIUM 43 / LOW 1
```

건축물대장이 매칭된 건물은 change_ratio 휴리스틱 대신 실제 사용승인일로
신축/기존 여부를 확정한다 (`src/buildings/classify.py`). 자세한 내용은
[`outputs/reports/poc_summary.md`](outputs/reports/poc_summary.md) 참고.

**중요 - HIGH 32건을 육안 검수한 결과**: 실제로 겹치는 change_polygon
(현장) 기준으로는 **11곳뿐**이다(2개 대형 현장이 21건/66%를 차지).
"건물 수"와 "현장 수"는 다르다 - `building_change_results.gpkg`의
`site_id` 컬럼(`src/buildings/overlay.py`에서 추가)으로 실제 현장 수를
group-by해서 셀 수 있다. 전체 76건도 서로 다른 현장은 33곳이다.
**현장조사 안내 시 건물 개수가 아니라 site_id 기준 현장 수로 말해야
같은 공사장을 여러 번 방문시키는 일을 막을 수 있다.**

자세한 내용과 한계는 [`outputs/reports/poc_summary.md`](outputs/reports/poc_summary.md),
SkyWatch 확장 근거는 [`outputs/reports/skywatch_requirements.md`](outputs/reports/skywatch_requirements.md) 참고.

STEP 8(정합 오차 정량화, `src/preprocessing/alignment.py`) 실행 결과
displacement=1.26m(0.126px), ecc_score=0.979로 정합 양호 확인.
STEP 13(행정정보 Validation, `src/buildings/validation.py`)도 실제 건축물대장
데이터로 실행 완료. STEP 23 Human Validation Sample도 실행됨
(`outputs/reports/human_validation_sample.csv`, 66건).

ArcGIS Online 발행 스크립트(계정/Publisher 권한 필요)는 아직 미구현이다.

## Outputs

```text
outputs/rasters/change_probability.tif
outputs/rasters/change_mask.tif
outputs/vectors/change_polygons.gpkg
outputs/vectors/building_change_results.gpkg
outputs/vectors/building_change_results.geojson
outputs/reports/building_change_summary.csv
outputs/reports/data_inventory.csv
outputs/reports/skywatch_requirements.md
outputs/maps/*.png
```

## Known Limitations

- 건축물대장(연면적/사용승인일/주용도 등 상세 행정 속성)은 아직 미확보.
  VWorld 건물 레이어(`lt_c_spbd`)에는 이 속성이 없어 별도 API로 PNU 기준
  보강이 필요하다 (data.go.kr 15134735, 엔드포인트/승인 확인 대기 중).
- VWorld WFS는 쿼리당 STARTINDEX 상한이 1000(최대 2000건/bbox)으로 실측
  확인되었다. `download_vworld_wfs_layer`는 이를 bbox 4분할 재귀로
  우회하지만, 매우 밀집된 지역에서는 재귀 깊이가 늘어나 호출 수가
  증가할 수 있다.
- **Sentinel-2 10m 해상도가 실제로 병목임을 실측 확인**: 실행 결과 change
  polygon 평균 면적이 7,351m²로, 개별 단독주택 단위 변화는 잡히지 않는다
  (`outputs/reports/skywatch_requirements.md` 참고). 후처리 최소면적
  threshold 10/25/50m²는 1픽셀(100m²)보다 작아 현재 해상도에서는 사실상
  무의미하다.
- Microsoft Planetary Computer의 서명된 다운로드 URL(SAS 토큰)은 약
  1시간 후 만료된다. `download_sentinel2_bands`는 밴드마다 재서명하고
  이미 받은 파일은 건너뛰도록 수정해 긴 다운로드 도중 끊겨도 이어받기
  가능하다.
- 국토지리정보원 고해상 정사영상은 로그인 + 전용 GUI 프로그램 전용으로
  확인되어 스크립트 자동화가 불가능하다 (`src/data/download.py`의
  `download_imagery`는 명시적으로 `NotImplementedError`를 발생시킨다).
- **Sentinel-2(10m) 해상도 한계**: 개별 단독주택 단위 신축/철거/증축은
  1~수 픽셀 수준이라 안정적 탐지가 어렵다. 대형 아파트단지·대규모
  토지조성 등 큰 변화 위주로 우선 검증하고, 건물 단위 정밀 탐지는
  NGII 고해상 영상(병행 확보 중) 또는 향후 SkyWatch 상용 영상으로
  보완한다. 이 한계는 SkyWatch Benchmark 근거 자료로 활용한다.
- `config/config.yaml`의 `satellite.search_bbox_wgs84`는 이제 실제 AOI
  (`data/aoi/changneung_test_aoi.gpkg`)를 버퍼한 "장면 검색용" 범위다.
  최종 Clip/분석에는 이 bbox가 아니라 AOI 폴리곤 자체를 사용해야 한다.
- 좌표계(EPSG) 및 정합 오차 허용치는 원본 데이터를 확인한 뒤 근거를
  기록하여 확정한다 (`config/config.yaml` 참고, 현재 TBD).

## Next Steps

1. 필요 데이터 확보 (아래 "지금 필요한 데이터" 참고)
2. Data Inventory 작성 (Phase 1)
3. AOI 확정 (2~4 km² Test AOI)
4. Raster 전처리 및 정합 (Phase 2)
5. Baseline Change Detection (Phase 3) 이후 STEP 10~18 순차 진행

## SkyWatch Benchmark Plan

Baseline PoC 완료 후 동일 AOI/파이프라인에 상용 위성영상(SkyWatch 등)을
투입하여 다음을 비교한다.

```text
Spatial resolution / Temporal resolution
Building change recall / Precision
Minimum detectable change size
False positive rate
Acquisition latency
Cost per km²
Operational scalability
```

세부 요구사항은 `outputs/reports/skywatch_requirements.md` (실제 PoC 결과
확보 후 작성 예정)에 정리한다.
