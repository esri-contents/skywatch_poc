# 단양 NDVI·BSI·필지 분석 — 데이터 출처 및 한계

## 1. 데이터 출처

| 자료 | 출처 | 기준/식별 | 비고 |
|---|---|---|---|
| Sentinel-2 L2A B02/B03/B04/B08 (10m) | Microsoft Planetary Computer STAC (기존 저장소 확보분) | `outputs/kr_rail_danyang/reports/scene_selection.json`의 연도별 `scene_id`·촬영일 | 이미 BOA offset 제거 상태(2022 이후 장면 1000 DN) |
| Sentinel-2 L2A B11 (20m) | 위와 **동일 scene_id**의 STAC item (`planetarycomputer.microsoft.com/api/stac/v1`) | `data/.../raw/sentinel2_swir/swir_provenance.json` | 촬영일·processing baseline이 기존 기록과 일치할 때만 저장. 무료 공개 카탈로그, 유료 주문 API 아님 |
| 연속지적도 | VWorld WFS `lp_pa_cbnd_bubun` (PNU·지번·주소 속성 보유) | 속성 `gosi_year/month` = 2025-01 (공시지가 기준 시점이며 지적경계 갱신일과 동일하다고 단정할 수 없음) | `lp_pa_cbnd_bonbun`은 `id/col_adm_se/bonbun`만 있어 PNU 연결 불가 → 커버리지 QA에만 사용 |
| 철도선 | OpenStreetMap (Overpass), `railway=rail` | `data/.../aoi/osm_railway.geojson` | **비공식**, 공식 철도사업구역/보상구역 아님 |
| 채석장 참조 | OpenStreetMap Overpass (`landuse=quarry`) | `data/.../reference/reference_provenance.json`(수집 시각 기록) | **비공인**, 자원봉사 기반. ODbL 1.0 © OpenStreetMap contributors. 허가/작업구역 전체를 감싼 거친 경계일 수 있음 |
| 분석 AOI | VWorld `LT_C_ADRI_INFO` 법정리 경계(매포리+하괴리) | `data/.../aoi/danyang_master_aoi.geojson` | 잠정 행정경계, 사업/보상구역 아님 |

`.env`의 API 키는 출력/기록하지 않는다.

## 2. 처리 원칙

- 같은 연도의 **다른 촬영일** 장면을 결합하지 않는다. B11은 반드시 기존 분석 장면과 동일한 scene_id.
- 반사율은 DN/10000만 적용한다. B11도 기존 10m 스택과 동일 규칙(baseline ≥ 04.00이면 +1000 DN 제거, SCL 마스크)으로 한 번만 보정해 저장하며, 파이프라인은 offset을 재적용하지 않는다.
- BSI/복합 분석은 B11 해상도인 **20m 격자**(EPSG:5186)에서 수행한다. 10m 밴드는 2×2 평균(4/4 유효)으로 집계하고 B11을 보간하지 않는다. 10m NDVI 결과는 별도로 유지된다.
- 무효(NoData/SCL 마스크/분모 < 0.001/범위 밖) 픽셀은 NaN이며 변화 없음으로 취급하지 않는다.
- 관측 간격이 약 2년이므로 변화 발생 연도는 추정하지 않고 최초 관측 구간만 기록한다.
- 필지 통계는 필지(AOI 내 부분)와 픽셀 사각형의 실제 교차 면적을 가중치로 쓴다.

## 3. 한계

1. NDVI와 BSI만으로 채석장·도로·농경지·건설현장·나지·시가지를 확정 구분할 수 없다. 모든 유형은 **후보**이다.
2. `PERSISTENT_EXPOSED_SURFACE`(노출 지속)에는 채석 외에 시가지·도로·산업시설 등 고BSI 비식생 지표가 포함된다.
3. 채석장 참조 폴리곤은 비공인이다. 폴리곤 면적·노출지 면적을 채석장 면적으로 해석하지 않으며, 시계열 경계 자료가 없어 **채석장 면적 변화는 산출하지 않았다**.
4. `POTENTIAL_QUARRY_EXPANSION`은 참조 폴리곤과의 공간적 연결(40 m, 2픽셀 이상)에 근거한 의심 후보이며 채석 활동의 확정이 아니다.
5. 식생 감소 면적 ⊃ 노출지 증가 면적(중복). 합산해 전체 변화 면적으로 해석하지 않는다. NDVI(10m)와 복합(20m) 면적은 해상도가 달라 같은 정확도로 비교하지 않는다.
6. 연도별 촬영일(DOY 118~129)·센서(S2A/S2B/S2C)·대기 차이에 의한 계통 편이는 안정 픽셀/전체 중앙값 기반 근사 보정이며 완전하지 않다(2018·2020 AOI 평균 NDVI가 낮음).
7. 농경지·벌채·산불·일시 나지·그림자·수분 영향은 외부 토지피복 자료 없이 구분할 수 없다(`UNCERTAIN_CHANGE`/후보로 남음). 수체는 MNDWI>0 플래그로만 제외한다.
8. 20m 픽셀 2개 미만(800㎡ 미만) 필지는 복합 판단을 보류한다(AOI 필지의 약 78%). 10m NDVI도 400㎡ 미만 필지는 자료 부족으로 처리된다.
9. 현재 시점의 연속지적도를 과거 영상에 중첩한 분석이며 과거 필지 분할·합병 이력을 재현하지 않는다.
10. 정답(참조 폴리곤/육안 판독) 자료가 없어 Precision/Recall/F1 등 정확도 지표를 산출하지 않았다.
11. 철도 거리·점수·가중치는 탐색용이며 행정 판단 기준이 아니다.
