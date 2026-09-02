# SkyWatch 요구사항 (PoC 실측 결과 기반)

이 문서는 Goyang Changneung Building Change Intelligence PoC의 Sentinel-2
Baseline 실행 결과를 근거로, 동일 파이프라인에 SkyWatch 상용 위성영상을
투입할 때 요청해야 할 요구사항을 정리한다.

## 왜 상용 영상이 필요한가 (실측 근거)

- AOI(창릉동, 10.99km²)에서 Sentinel-2(10m) 기반으로 검출된 change
  polygon은 69개, **평균 면적 6,169m²**였다(robust CVA 적용 후 재측정,
  2026-09-01). 이는 대형 아파트단지/대규모
  토지조성 규모만 잡히고, 개별 단독주택 단위(수십~수백m²)의 신축·증축은
  10m 픽셀 안에 묻혀 원천적으로 분리되지 않는다는 뜻이다.
- 후처리 최소면적 threshold(config.yaml: 10/25/50/100m²) 중 10~50m²는
  Sentinel-2 한 픽셀(100m²)보다 작아 애초에 존재할 수 없는 조건이었다 -
  즉 현재 해상도에서는 이 threshold들이 무의미하다.
- 반대로 이 결과는 "광역 스크리닝(대규모 변화 1차 필터링)에는 무료
  공개영상이 유효하다"는 것도 함께 보여준다 - SkyWatch 상용영상은
  이 1차 필터링 이후 정밀 확인 단계에 투입하는 게 비용효율적일 수 있다.

## Target AOI

- 1차: 고양시덕양구 창릉동 (10.99km², `data/aoi/changneung_test_aoi.gpkg`)
- 확장: 고양 창릉 공공주택지구 정식 지구계 확보 시 그 경계로 교체
  (본 PoC의 AOI는 행정동 경계 근사치이며 정식 지구계보다 넓다)

## Required GSD (공간해상도)

- 개별 단독주택 단위 신축/증축 탐지가 목표이므로 최소 **1m 이하**, 가능하면
  **0.5m 이하(서브미터)** 권장. Sentinel-2(10m) 대비 최소 10~20배 세밀해야
  이번 PoC가 놓친 소규모 변화를 잡을 수 있다.

## Archive Imagery Dates

- T1 후보: 2022년 (본 PoC와 비교 가능하도록 2022년 상반기~중반, 가급적
  5~6월 - 현재 Sentinel-2 T1과 계절을 맞춰야 식생/광량 차이로 인한
  오탐을 줄일 수 있다)
- T2 후보: 2024년 하반기 또는 가장 최근 (신축 완료분 반영)

## Recent Imagery Requirement

- 현장조사 우선순위 산정이 목적이므로 최근 3개월 이내 최신 영상 확보 가능
  여부 확인 필요.

## Cloud Cover Requirement

- 본 PoC는 구름량 3% 미만 장면만 사용(T1: 0.01%, T2: 2.1%). 상용영상도
  동일 기준(5% 미만) 권장.

## Off-nadir / Orthorectification

- 건물 옆면이 겹쳐 보이는 오프나디르 영상은 건물 footprint 오차를
  유발하므로 낮은 off-nadir(가급적 10도 이내) 또는 정사보정
  (orthorectified) 완료본 요청.

## Metadata / Format

- GeoTIFF (COG 권장), 밴드 구성(RGB+NIR 이상), 촬영일시, GSD, 좌표계
  명시 필요. 본 PoC 파이프라인은 EPSG:5186로 재투영하는 전처리 단계를
  이미 갖추고 있어 원본 CRS는 무관하다.

## API Availability

- 이번 PoC의 Sentinel-2 소스(Microsoft Planetary Computer STAC)처럼
  프로그래밍 방식 검색/다운로드가 가능한 API 필요. `src/data/download_satellite.py`
  구조를 그대로 재사용할 수 있도록 STAC 또는 이에 준하는 REST API 선호.

## Minimum Order Area / PoC Imagery Support

- AOI(10.99km²) 규모의 PoC용 소량 주문/평가판 지원 여부 확인 필요.

## Commercial Pricing Structure

- $/km², 아카이브 vs 신규 촬영(taskiing) 가격 차이, 최소 주문 면적,
  다중 시기(멀티템포럴) 할인 여부 확인 필요.

## Benchmark 비교 계획 (확보 후 실행)

```text
Spatial resolution      Sentinel-2 10m  vs  SkyWatch(요청 예정)
Building change recall  (본 PoC 69개 polygon 대비 재계산)
Precision               (Human Validation Sample 대비 재계산)
Minimum detectable size 6,169m²(현재 평균) vs (목표: 수십m² 단위)
False positive rate     (Human Validation Sample 확보 후)
Acquisition latency     비교 예정
Cost per km²            비교 예정
```
