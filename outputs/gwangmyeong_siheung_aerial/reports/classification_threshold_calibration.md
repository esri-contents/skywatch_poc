# 광명시흥 분류 임계값 민감도 분석

- 임시 운영값: `change_ratio_new_building_min=0.5`
- 정답 라벨: 없음. 따라서 정확도 보정 완료가 아니라 **민감도 분석과 임시 운영값 선정**이다.
- 기존 분류기 예측값은 정답으로 사용하지 않았다. 수동 검수표는 동일 PNU/공간그룹이 calibration과 validation에 겹치지 않도록 분리했다.
- 건물 후보 수와 `site_id` 기반 중복 제거 현장 수는 서로 다른 지표다.
- `new_building_candidates`/`expansion_candidates`는 각 threshold에서 change_ratio를 다시 적용해 재계산한 값이라 threshold에 따라 단조 변화한다. 반면 `high_priority_candidates`/`unique_field_sites`/`direction_mismatch_or_review`는 실제 운영 실행(threshold=0.5) 1회에서 나온 값을 그대로 표시한 것이라 threshold marginal 값에서는 변하지 않는다 - 이 세 지표는 threshold sweep이 아니라 우선순위·현장통합·방향성 로직 자체의 결과다.

## 임시값 실행 요약

| threshold | comparison | new_building_candidates | expansion_candidates | high_priority_candidates | unique_field_sites | direction_mismatch_or_review |
|---|---|---|---|---|---|---|
| 0.5 | t0_t2 | 50 | 4282 | 166 | 4285 | 23 |

## 전체 후보값 비교

| threshold | comparison | new_building_candidates | expansion_candidates | high_priority_candidates | unique_field_sites | direction_mismatch_or_review |
|---|---|---|---|---|---|---|
| 0.3 | t0_t2 | 116 | 4216 | 166 | 4285 | 23 |
| 0.4 | t0_t2 | 78 | 4254 | 166 | 4285 | 23 |
| 0.5 | t0_t2 | 50 | 4282 | 166 | 4285 | 23 |
| 0.6 | t0_t2 | 31 | 4301 | 166 | 4285 | 23 |
| 0.7 | t0_t2 | 18 | 4314 | 166 | 4285 | 23 |
| 0.8 | t0_t2 | 6 | 4326 | 166 | 4285 | 23 |

운영 확정 전 `manual_validation_sample.csv`의 사람 판독 라벨을 채운 뒤 site/PNU 그룹 단위로 독립 검증해야 한다.