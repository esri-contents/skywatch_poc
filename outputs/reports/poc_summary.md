# PoC Summary - Goyang Changneung Building Change Intelligence

**AOI**: 경기도 고양시덕양구 창릉동 (행정동 경계, 10.99km²)
**T1**: Sentinel-2 L2A, 2022-05-17 (구름 0.01%)
**T2**: Sentinel-2 L2A, 2024-05-31 (구름 2.1%, 계절 일치)
**분석 좌표계**: EPSG:5186, 10m 해상도

## 정합 검증 (STEP 8)

ECC(Enhanced Correlation Coefficient) 기반 T1->T2 평행이동 추정 결과:

```text
dx=0.076px  dy=0.101px  displacement=0.126px (1.26m)  ecc_score=0.979
```

기준(1픽셀=10m) 대비 충분히 작아 별도 정합 없이 Change Detection을 진행해도
무방하다고 판단. 참고: T1/T2가 같은 Sentinel-2 UTM 타일(T52SCG)에서 독립적으로
재투영되었음에도 이 정도로 잘 맞는 것은 위성 데이터의 고정 타일 그리드 덕분이며,
항공사진(NGII 등)처럼 촬영마다 좌표가 달라지는 소스에서는 이 단계가 훨씬
중요해진다.

## Funnel

```text
전체 건물 (VWorld 도로명주소건물, AOI 내)      2,737
  ↓
영상 변화 탐지 (Change Polygon, 후처리 완료)      33
  ↓
건물과 연계된 변화 (change_ratio > 0인 건물)      57
  ↓
행정정보 검증 (건축물대장 표제부, PNU 매칭 64.7%)
    - 사용승인일이 T1~T2 사이 (설명됨)            1
    - 등록은 있으나 시점 불일치                   24
    - 건축물대장 미등록                          51
  ↓
현장확인 후보 (최종 변화 후보, 건물 미연계 포함)   76
    - HIGH priority                            32
    - MEDIUM priority                          43
    - LOW priority                              1
```

**STEP 13가 실제로 결과를 바꾼 사례**: 건축물대장 매칭 결과 1건은 사용승인일이
T1~T2 사이에 있어 `administrative_uncertainty=0.1`(행정적으로 설명됨)로
낮아졌고, 그 결과 해당 건물의 priority_score가 낮아져 HIGH에서 MEDIUM으로
내려갔다. 이것이 이 파이프라인의 핵심 가치다: 영상만으로는 구분할 수 없는
"합법적으로 신축/증축된 건물"과 "설명되지 않는 변화"를 행정정보로 걸러낸다.

## 변화유형 분포

| 유형 | 건수 | 비고 |
|---|---|---|
| NEW_BUILDING | 32 | change_ratio >= 0.5 (건물 footprint 대부분이 변화로 덮임) |
| EXPANSION_OR_RECONSTRUCTION | 25 | 0 < change_ratio < 0.5 |
| OTHER_CHANGE | 16 | 건물 미교차 change (토지조성/도로 등 추정) |
| DEMOLITION | 3 | 건물 미교차 + 고신뢰 변화점수(>=0.6) - 구조물 소멸 추정 |

## 핵심 한계 (반드시 함께 읽을 것)

1. **PNU 조인의 산여부 불일치**: VWorld 건물 layer의 PNU 필드는 산여부(0/1)
   자리가 건축물대장 표제부 응답과 체계적으로 다르다(VWorld는 대부분
   1/2, 표제부는 대부분 0). 이 값을 무시하고 법정동+본번+부번 9자리로만
   조인했으며, 그 결과 64.7%(1,771/2,737)만 매칭되었다. 나머지 35.3%는
   건축물대장 미등록(공사 중 등)이거나 지번 표기 차이로 추정된다 -
   원인을 완전히 규명하지 못했으므로 매칭 실패 건을 "행정정보 없음"으로
   보수적으로 처리했다(administrative_uncertainty=1.0).
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
