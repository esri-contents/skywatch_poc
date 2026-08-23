# PoC Summary - Goyang Changneung Building Change Intelligence

**AOI**: 경기도 고양시덕양구 창릉동 (행정동 경계, 10.99km²)
**T1**: Sentinel-2 L2A, 2022-05-17 (구름 0.01%)
**T2**: Sentinel-2 L2A, 2024-05-31 (구름 2.1%, 계절 일치)
**분석 좌표계**: EPSG:5186, 10m 해상도

## Funnel

```text
전체 건물 (VWorld 도로명주소건물, AOI 내)      2,737
  ↓
영상 변화 탐지 (Change Polygon, 후처리 완료)      33
  ↓
건물과 연계된 변화 (change_ratio > 0인 건물)      57
  ↓
행정정보 검증                                미실시 (건축물대장 미확보)
  ↓
현장확인 후보 (최종 변화 후보, 건물 미연계 포함)   76
    - HIGH priority                            35
    - MEDIUM priority                          40
    - LOW priority                              1
```

## 변화유형 분포

| 유형 | 건수 | 비고 |
|---|---|---|
| NEW_BUILDING | 32 | change_ratio >= 0.5 (건물 footprint 대부분이 변화로 덮임) |
| EXPANSION_OR_RECONSTRUCTION | 25 | 0 < change_ratio < 0.5 |
| OTHER_CHANGE | 16 | 건물 미교차 change (토지조성/도로 등 추정) |
| DEMOLITION | 3 | 건물 미교차 + 고신뢰 변화점수(>=0.6) - 구조물 소멸 추정 |

## 핵심 한계 (반드시 함께 읽을 것)

1. **행정정보 미검증**: 건축물대장(사용승인일/연면적/주용도)을 아직 확보하지
   못해 `administrative_uncertainty`를 전 후보에 대해 1.0(완전 불확실)으로
   고정했다. 즉 현재 HIGH/MEDIUM/LOW 등급은 "영상 변화 강도 + 건물 연관성"
   만으로 산정된 것이며, 인허가로 설명 가능한 변화를 걸러내지 못했다.
2. **건물 데이터가 단일 시점 스냅샷**: VWorld 건물 layer는 "현재" 상태만
   제공한다. T1(2022) 시점 건물 상태가 없어 NEW_BUILDING vs
   EXPANSION_OR_RECONSTRUCTION 구분은 change_ratio 크기에 기반한
   근사치이며, DEMOLITION 판정도 이미지 변화 강도만으로 추정한 것이다.
3. **Sentinel-2 10m 해상도**: 이번에 만들어진 33개 change polygon의 평균
   면적은 **7,351m²**로, 개별 단독주택(수십~수백m²) 단위 변화가 아니라
   대형 아파트단지/대규모 토지조성 규모의 변화만 잡혔다는 뜻이다. 이는
   "Sentinel-2 Baseline으로는 건물 단위 세밀 탐지가 불가능하다"는 가설을
   실측으로 뒷받침하며, `skywatch_requirements.md`의 핵심 근거가 된다.

## 산출물

```text
outputs/rasters/change_probability.tif
outputs/rasters/change_mask.tif
outputs/vectors/change_polygons.gpkg
outputs/vectors/building_change_results.gpkg / .geojson
outputs/reports/building_change_summary.csv
outputs/maps/changneung_change_detection_overview.png
outputs/maps/inspection_priority.png
```
