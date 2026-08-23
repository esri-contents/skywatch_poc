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
| 2022/2024년 Sentinel-2 위성영상 (T1/T2, 1차 자동화 소스) | Change Detection 입력 (자동화 Baseline) | [Microsoft Planetary Computer STAC](https://planetarycomputer.microsoft.com/api/stac/v1) (Sentinel-2 L2A) | **자동 완료 확인**: `src/data/download_satellite.py`, API Key 불필요. 검색 테스트 결과 2022년 71건/2024년 47건 확인 (10m, B02/B03/B04/B08) | **자동 검증됨 (다운로드 실행 전)** |
| 2022/2024년 고양 창릉 정사영상 (T1/T2, 고해상 병행 트랙) | 건물 단위 정밀 탐지용 (Sentinel-2 10m로는 개별주택 신축 탐지 어려움) | [국토지리정보원 국토정보플랫폼](http://map.ngii.go.kr/ms/map/NlipMap.do?tabGb=total) | **수동**: 회원가입/로그인 후 통합검색 → 정사영상 선택 → 전용 대용량 파일전송 프로그램으로 다운로드. GUI 전용이라 자동화 불가 확인됨 (TIFF, 도시지역 12cm/일반지역 25cm, 2010년 이후 촬영분만 제공) | **미확보 (선택적, 병행 진행)** |
| 건물통합정보 (건물 footprint + 속성) | 건물 단위 Overlay/분류 | [국토교통부_GIS건물통합정보(WMS/WFS)](https://www.data.go.kr/data/15123970/openapi.do) (공공데이터포털) | **자동 가능**: data.go.kr 활용신청 후 인증키로 WFS 호출 (`src/data/download.py::download_vworld_wfs_layer` 를 이 서비스 엔드포인트로 교체 필요 - 정확한 typename은 활용가이드 확인 후 반영) | **미확보 (Key 필요)** |
| 건축물대장 / 인허가 정보 (표제부, 사용승인일, 주용도, 연면적 등) | 행정정보 Validation | [국토교통부_건축HUB_건축물대장정보 서비스](https://www.data.go.kr/data/15134735/openapi.do) (공공데이터포털) | **자동 가능**: data.go.kr 활용신청 후 동일 인증키(`DATA_GO_KR_API_KEY`)로 REST 호출 (`src/data/download.py::download_data_go_kr`) | **미확보 (Key 필요)** |
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

- 현재 건물통합정보, 건축물대장, AOI 경계 데이터가 로컬에 없어 Phase 1
  (데이터 검증) 이전 단계다. Sentinel-2 영상은 자동 검색까지 검증됨.
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
