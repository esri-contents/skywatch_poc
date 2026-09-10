# LH 요구사항 반영 현황 (자동 생성)

- 생성 시각: 2026-09-08T15:35:04
- 이 문서는 매 실행마다 다시 생성된다 - 과거 실행 기록이 아니라 **이번 실행의 실제 결과**다.

| 순위 | 요구 기능 | 상태 | 비고 |
|---|---|---|---|
| 1 | 토지 및 건축물 변화 확인 | 완료 |  |
| 2 | 불법·무허가 개발 의심지역 식별 | 완료 | 법적 위반 판정이 아니라 "행정정보로 설명되지 않는 변화"의 등급화다. 경기도 위반건축물 공개데이터는 분기별 집계뿐이라 건물 단위 정답이 존재하지 않는다(handoff.md 7번 참고).
 |
| 3 | 보상 기준일 전후의 토지·건물 변화 확인 | 완료 | 보상 기준일(공람공고일)을 config에서 받아 그 전후로 시계열을 쪼개고, 기준일 이후 발생한 변화 중 건축물대장 근거가 없는 건을 등급화한다.
 |
| 4 | 사업지구 전체의 개발 진행 상황 모니터링 | 부분 | 단일 시기만 제공됨 - 다시기 비교 없이는 '진행 추세'를 알 수 없음 |
| 5 | 현장조사 대상 지역 선별 및 우선순위 지정 | 완료 |  |
| 6 | 필요한 시점에 최신 위성영상 확보 | 완료 | 아카이브 조회는 완료. 신규촬영(tasking)은 상용 계약 필요 - tasking_request.md 참고 |
| 7 | 과거 영상과 최신 영상을 비교해 검토 대상 자동 추출 | 완료 | 본 실행 자체가 T1/T2 자동 비교 파이프라인이다 |
| 8 | 변화탐지 결과를 지적도·건축물 등 LH 업무데이터와 결합 | 완료 |  |
| 9 | 기존 드론·현장조사 자료가 없는 과거 시점의 기록 보완 | 부분 | 과거 시점 아카이브 가용성만 확인 (아래 REQ06 참고), 실제 소급분석은 미실행 |
| 10 | Web Map 등을 통한 결과 공유와 보고서 작성 지원 | 완료 | ArcGIS Pro PDF 및 Enterprise Web Map 발행 완료 |
| 11 | 후보지역을 대상으로 ArcGIS Reality 기반 3D 정밀검토 | 부분 | 정밀검토 대상 선정·임시 3D·촬영계획까지 완료. 실제 Reality 처리는 고해상 촬영 확보 후 별도 실행 |

## 상세 실행 결과

```
REQ01: {'status': 'ok', 'change_polygons': 70, 'valid_pixels': 109903, 'changed_pixels': 5374, 'changed_pct': 4.8898, 'threshold_method': 'fixed', 'used_threshold': 0.5, 'mean_scores': {'spectral': 0.2045, 'structural': 0.1143, 'edge_texture': 0.2548}}
spatial_statistics: {'I': 0.330306, 'z_score': 10.176872, 'p_value': 0.0, 'n': 189, 'k': 8, 'conceptualization': 'K_NEAREST_NEIGHBORS'}
REQ03: {'status': 'ok', 'baseline_date': '2019-05-07', 'PRE_BASELINE': 60, 'UNKNOWN': 9, 'POST_BASELINE_UNVERIFIED': 118, 'POST_BASELINE_PERMITTED': 2}
REQ02: {'status': 'ok', 'NONE': 112, 'B_MODERATE': 61, 'C_WEAK': 16}
REQ08: {'status': 'ok', 'linked_candidates': 158}
REQ05: {'status': 'ok', 'candidate_count': 189, 'site_count': 92, 'reduction_pct': 51.3}
REQ04: {'status': 'partial', 'reason': "단일 시기만 제공됨 - 다시기 비교 없이는 '진행 추세'를 알 수 없음"}
REQ09: {'status': 'partial', 'reason': '과거 시점 아카이브 가용성만 확인 (아래 REQ06 참고), 실제 소급분석은 미실행'}
REQ11: {'status': 'partial', 'reason': '정밀검토 대상 선정·임시 3D·촬영계획까지 완료. 실제 Reality 처리는 고해상 촬영 확보 후 별도 실행'}
REQ06: {'status': 'ok', 'latest_available': '2026-08-04', 'latest_age_days': 35, 'note': '아카이브 조회는 완료. 신규촬영(tasking)은 상용 계약 필요 - tasking_request.md 참고'}
REQ10: {'pdf_report': 'ok', 'web_map': 'ok', 'web_map_url': 'https://portal.esrikr.com/portal/home/item.html?id=ff154e02af614609b7bea69fd4f2826c', 'status': 'ok'}
REQ07: {'status': 'ok', 'note': '본 실행 자체가 T1/T2 자동 비교 파이프라인이다'}
```
