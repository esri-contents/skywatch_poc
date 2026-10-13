# AI 연계 전달 패키지

대상: 단양군 매포읍 매포리·하괴리 필지 2,133개 (EPSG:5186, 지리 좌표는 geojson에서 4326)

| 파일 | 용도 |
|---|---|
| `parcels_ai_view.csv` | **질의용 표**(영문 필드 48개, UTF-8 BOM). AI가 조회할 데이터 |
| `parcels_ai_view.gpkg` / `.geojson` | 같은 표 + 필지 경계(지도 표시·Portal 발행용) |
| `data_dictionary.csv` | 필드 사전(한글 별칭, 설명, 단위, 값 목록) — AI 컨텍스트/필드 별칭으로 사용 |
| `sample_questions.csv` | 예시 질문 13개와 **실제 데이터로 계산한 기대 답변**(테스트 기준) |
| `answering_rules.md` | 답변 규칙·금지 표현(시스템 프롬프트 초안) |

## 연계 방법별 안내

**방법 1. Portal MCP 연계 (토큰 비용 고객사 부담, 가이드만 제공)**
- `parcels_ai_view.geojson`(또는 gpkg)을 Portal에 호스팅 피처 레이어로 발행한다. 기존 코드 `src/publish/arcgis_online.py`의 `publish_geojson_layer`를 참고할 수 있다(Portal 로그인 정보는 환경변수, 저장소에 기록하지 않음).
- 발행 시 `data_dictionary.csv`의 한글 별칭을 필드 별칭으로 지정하고, 레이어 설명에 `answering_rules.md` 요지를 넣는다.
- MCP 클라이언트의 시스템 지침에 `answering_rules.md`를 넣는다.

**방법 2. AI API 연계 (토큰 비용 한국에스리 부담)**
- 표 전체를 프롬프트에 넣지 않는다(CSV 약 1,266 KB). 질의를 조건식(필터)으로 바꾸는 도구 호출(tool/function calling)로 조회하고 결과 행만 모델에 전달하면 토큰 비용이 작다.
- 모델에는 `data_dictionary.csv`와 `answering_rules.md`만 항상 제공한다.
- `sample_questions.csv`를 회귀 테스트로 사용한다(질문 → 기대 답변과 숫자 일치).

## 주의
- 이 자료는 위성영상(10~20m) 기반 **1차 선별 자료**다. 지장물의 위치·형태·규모나 연도별 이력은 확인하지 못한다.
- 철도선(OSM)·채석장 참조(OSM)는 비공식 자료다.
- 상세 한계: `docs/ndvi_bsi_data_sources_and_limitations.md`, 검증 결과: `docs/ndvi_bsi_validation_report.md`.
