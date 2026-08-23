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
행정정보 검증 (건축물대장 표제부, PNU+도로명주소 이중 조인 67.7%)
    - 사용승인일이 T1~T2 사이 (설명됨)             1
    - 등록은 있으나 시점 불일치                    25
    - 건축물대장 미등록/미매칭                     50
  ↓
현장확인 후보 (최종 변화 후보, 건물 미연계 포함)    76
    - HIGH priority                             32
    - MEDIUM priority                           43
    - LOW priority                               1
```

**STEP 13가 실제로 결과를 바꾼 사례**: 건축물대장 매칭 결과 1건(useAprDay=
2024-05-13)은 사용승인일이 T1~T2 사이에 있어 `administrative_uncertainty=0.1`
(행정적으로 설명됨)로 낮아졌고, priority_score가 낮아져 HIGH에서 MEDIUM으로
내려갔다. 또한 건축물대장 근거가 있는 건물(사용승인일이 T1 이전)은 change_ratio
휴리스틱 대신 실제 등록 정보로 EXPANSION_OR_RECONSTRUCTION으로 확정 분류된다 -
예: 1984/1993/1996/2017년에 사용승인된 건물들이 여기 해당(사람이 실제로
"신축"이 아니라 "기존 건물의 변화"임을 확인할 수 있는 근거). 이것이 이
파이프라인의 핵심 가치다: 영상만으로는 구분할 수 없는 "합법적으로 신축/증축된
건물"과 "설명되지 않는 변화"를 행정정보로 걸러낸다.

## 변화유형 분포

| 유형 | 건수 | 판정 근거 |
|---|---|---|
| NEW_BUILDING | 20 | 건축물대장 매칭 시 사용승인일이 T1~T2 사이(확정, 1건) 또는 change_ratio>=0.5 휴리스틱(미매칭 건물, 19건) |
| EXPANSION_OR_RECONSTRUCTION | 37 | 건축물대장 매칭 시 사용승인일이 T1 이전(확정, 다수) 또는 change_ratio<0.5 휴리스틱 |
| OTHER_CHANGE | 16 | 건물 미교차 change (토지조성/도로 등 추정) |
| DEMOLITION | 3 | 건물 미교차 + 고신뢰 변화점수(>=0.6) - 구조물 소멸 추정 |

(건축물대장 매칭으로 확정 판정된 비율은 `has_register_match`/`match_method`
컬럼으로 결과 파일에서 직접 확인 가능. 매칭 안 된 건물만 여전히 change_ratio
휴리스틱에 의존한다.)

## 핵심 한계 (반드시 함께 읽을 것)

1. **PNU+도로명주소 이중 조인도 67.7%까지만 커버**: VWorld 건물 layer의 PNU
   필드는 산여부(0/1) 자리가 건축물대장 표제부와 체계적으로 다르다(VWorld는
   대부분 1/2, 표제부는 대부분 0) - 이 자리를 제외한 9자리로 조인(64.7%).
   추가로 도로명코드+건물본번/부번 기반 조인을 보강해 67.7%(1,854/2,737)까지
   끌어올렸다. 남은 32.3%는 건축물대장 미등록(공사 중 등)이거나 두 키
   모두에서 설명 안 되는 표기 차이로 추정되며, 원인을 완전히 규명하지는
   못했다 - 매칭 실패 건은 "행정정보 없음"으로 보수적으로 처리한다
   (administrative_uncertainty=1.0).
2. **건물 데이터가 단일 시점 스냅샷**: VWorld 건물 layer는 "현재" 상태만
   제공한다. 건축물대장이 매칭된 건물은 사용승인일로 신축/기존 여부를
   확정할 수 있지만(위 참고), 매칭 안 된 35% 정도는 여전히 change_ratio
   휴리스틱에 의존하며, DEMOLITION 판정도 이미지 변화 강도만으로 추정한 것이다.
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
