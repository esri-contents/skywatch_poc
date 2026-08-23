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

| 데이터 | 필요 이유 | 공식 출처(우선순위) | 확보 방법 | 상태 |
|---|---|---|---|---|
| 2022년 고양 창릉 정사영상 (T1) | Change Detection 입력 | 국토지리정보원 국토정보플랫폼 > 공공데이터포털 > VWorld | 수동 다운로드 (자동 API 미확인) | **미확보** |
| 2024년 고양 창릉 정사영상 (T2) | Change Detection 입력 | 상동 | 수동 다운로드 | **미확보** |
| 건물통합정보 (건물 footprint) | 건물 단위 Overlay/분류 | VWorld 오픈API / 공공데이터포털 | API 자동 수집 가능 (Key 필요) | **미확보** |
| 건축물대장 / 인허가 정보 | 행정정보 Validation | 공공데이터포털 (data.go.kr) | API 자동 수집 가능 (Key 필요) | **미확보** |
| 고양 창릉 사업지구 경계 | AOI 정의 | LH / 국토부 지구단위계획 공고 등 공식 출처 | 자동화 확인 안 됨 | **미확보** |

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

## Execution

```bash
python -m src.pipeline \
  --t1 data/raw/imagery/2022 \
  --t2 data/raw/imagery/2024 \
  --buildings data/raw/buildings/buildings.gpkg \
  --aoi data/aoi/changneung_test_aoi.gpkg
```

파이프라인은 아직 실제 데이터가 없어 실행할 수 없다 (Phase 1 데이터
확보 후 STEP 7부터 순차 구현).

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

- 현재 실제 항공정사영상, 건물통합정보, 건축물대장 데이터가 로컬에
  없어 Phase 1(데이터 검증) 이전 단계다.
- 국토지리정보원 정사영상에 대한 자동 다운로드 API는 아직 확인되지
  않았다 (`src/data/download.py`의 `download_imagery`는 미구현 상태로
  명시적으로 예외를 발생시킨다).
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
