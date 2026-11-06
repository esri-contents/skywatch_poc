# 광명시흥 필지 구조물 판독 Add-in — ArcGIS 라이선스 검토 요약

- 대상: ArcGIS Pro Add-in `GwangmyeongInterpretation` v1.2 (ArcGIS Pro 3.5 이상 공용 빌드 — 3.5·3.6·3.7. 라이선스 관련 코드는 v1.1(`bc9adc4`)과 같음)
- 검토일: 2026-11-06
- 근거: 소스코드 전수 조사 + Esri 공식 문서(도구별 Licensing information) + 개발 PC 실제 확인
- 상세: [기술 검토서](ARCGIS_ADDIN_LICENSE_TECHNICAL_AUDIT.md) · [고객환경 테스트 계획](ARCGIS_ADDIN_LICENSE_TEST_PLAN.md)

## 1. 결론

> 본 ArcGIS Pro Add-in의 소스코드와 Esri 공식 라이선스 요건을 검토한 결과, 전체 필수 기능 실행을 위해 필요한 최소 라이선스는 **ArcGIS Pro Basic(Creator 사용자 유형)** 이며, 추가로 **Spatial Analyst·Image Analyst·3D Analyst 등 확장 라이선스**가 **불필요**한 것으로 판단됩니다. 다만 **Basic 라이선스 환경에서의 실제 실행(특히 [현장조사 내보내기]의 지오프로세싱 5종)과 고객 PC의 ECW 지원 설치 여부**는 실제 라이선스 환경에서 추가 검증이 필요합니다.

이 결론은 **코드 분석과 공식 문서로 확정**한 것이며, **Basic 환경에서 실제로 돌려 본 것은 아닙니다.** 지금까지의 모든 실행 시험은 개발 PC(ArcGIS Pro 3.7 **Advanced**, 레지스트리 `SOFTWARE_CLASS=Professional`)에서 했습니다.

## 2. 왜 Basic으로 충분하다고 보는가

| 구분 | Add-in이 쓰는 것 | 라이선스 요구 |
|---|---|---|
| 판독·변화탐지·필지 판정·보고서·SHP·PDF | Add-in이 띄우는 **별도 Python 프로세스**(rasterio, OpenCV, scikit-learn 등 오픈소스). arcpy·Esri 도구를 **전혀 쓰지 않음** | Esri 라이선스 대상 아님 |
| ECW·MrSID 형식 변환 | Pro에 들어 있는 GDAL(`osgeo.gdal`)만 사용, arcpy 미사용 | Esri 라이선스 확인 없이 동작함을 개발 PC에서 실측 확인 |
| 결과 레이어 추가·기호·지도 이동 | Pro SDK 기본 지도 API | Pro 코어 기능(모든 등급) |
| 현장조사 gdb 마무리 | 지오프로세싱 5종: Create Domain, Add Coded Value To Domain, Assign Domain To Field, Alter Field, Enable Attachments | **5종 모두 공식 문서상 Basic: Yes** |

- 상위 등급(Standard/Advanced)이 필요한 코드는 **찾지 못했습니다.**
- 확장(Extension) 도구 호출은 **한 건도 없습니다.**
- ArcGIS Online/Enterprise 서비스 호출, 게시, 크레딧을 쓰는 기능은 **Add-in 안에 없습니다.**

## 3. 사용자 유형별 사용 가능 여부

Esri 공식 문서: Creator = Basic, Professional = Standard, Professional Plus = Advanced.

| 사용자 유형 (라이선스 등급) | Add-in 로딩 | 판독·보고서·SHP·PDF | 현장조사 gdb 설정 | 종합 판정 |
|---|---|---|---|---|
| Creator (Basic) | 가능* | 가능 | 가능(문서 근거, 실행 미검증) | **가능 — Basic 실행 검증 필요** |
| Professional (Standard) | 가능* | 가능 | 가능(문서 근거, 실행 미검증) | **가능 — 실행 검증 필요** |
| Professional Plus (Advanced) | 가능* | 가능(개발 PC 실행 확인) | 가능(문서 근거) | **가능** |
| 위 + 별도 확장 | 확장은 쓰이지 않음 | 차이 없음 | 차이 없음 | 확장 구매 효과 없음 |

\* 공통 전제(라이선스 등급과 무관): **ArcGIS Pro 3.5 이상**(3.5·3.6·3.7 공용 빌드, 3.5·3.6 실기 시험은 아직 안 함), Pro 옵션의 Add-in 보안 설정이 이 Add-in을 허용(또는 기관 인증서로 서명), ECW 원본을 쓰는 경우 Pro 설치 시 **ECW 지원을 끄지 않았을 것**.

## 4. 고객기관 배포 권장 라이선스

- **최소·권장: ArcGIS Pro Basic(Creator) 1식 — 판독 작업 PC 대수만큼.** 확장 라이선스 추가 구매 불필요.
- 라이선스 형태(Named User / Single Use)는 무관합니다. 다만 Named User는 Pro 자체 실행에 기관 ArcGIS 계정 로그인이 필요합니다(Add-in 요구사항이 아님).
- 현장에서 **ArcGIS Field Maps로 조사**하려면 survey.gdb를 ArcGIS Online/Enterprise에 게시해야 합니다. 이 게시와 현장 사용자 계정은 **Add-in 범위 밖의 별도 요건**이며, 엑셀 조사표만 쓰면 필요 없습니다.

## 5. 확정된 사항 / 검증 필요 사항

**확정(코드·공식 문서·실측 근거)**
1. Add-in이 실행하는 Esri 지오프로세싱 도구는 5종이며, 공식 문서에 5종 모두 Basic·Standard·Advanced 모두 "Yes"로 표기되어 있음.
2. 판독 분석 경로(Python)에 arcpy·확장 도구 호출이 없음(정적 import 전수 추적).
3. Pro의 GDAL로 ECW 원본(102,366×203,051 화소)을 arcpy 없이 열고 읽을 수 있음(개발 PC 실측).
4. Add-in 실행 중 외부 웹서비스 호출 코드가 실행되지 않음(관련 라이브러리는 import만 됨).

**검증 필요**
1. Basic·Standard 라이선스 PC에서 전체 버튼 실행(특히 현장조사 내보내기) — [테스트 계획](ARCGIS_ADDIN_LICENSE_TEST_PLAN.md) 시나리오 L1·L2.
2. 고객 PC의 Pro 버전(3.5 이상, 3.5·3.6에서 실제 로딩·실행), ECW 지원 설치 여부, Add-in 보안 정책.
3. 배포·상업적 이용 조건(Esri 약관), Pro에 포함된 GDAL ECW 드라이버를 Pro 외부 Python에서 쓰는 것의 약관상 허용 여부 — **Esri 서면 확인 필요**.

## 6. 추가 비용·리스크

| 항목 | 비용 발생 | 비고 |
|---|---|---|
| 확장 라이선스 | 없음 | 확장 기능을 쓰지 않음 |
| ArcGIS Online 크레딧 | Add-in 자체는 없음 | Field Maps용 게시·저장은 기관 Online 저장 크레딧을 쓸 수 있음(Add-in 범위 밖) |
| Pro 버전 | 3.5 미만 PC는 업그레이드 필요 | 3.5 이상 공용 빌드 |
| 코드 서명 | 기관 정책에 따라 인증서 비용 | Esri 문서: 상용 인증기관 인증서 필요 |
| 동봉 자료 | 확인 필요 | 배포 패키지에 기초자료·2022 기준 영상 포함 → 자료 제공기관 재배포 조건 확인 |

## 7. 권고

1. 고객 납품 전 **Basic(Creator) 라이선스 PC 1대에서 테스트 계획 L1을 실제로 수행**하고 결과를 증적으로 남깁니다. 통과하면 "Basic으로 전 기능 실행"을 확정 결론으로 보고할 수 있습니다.
2. 계약서·제안서에는 "ArcGIS Pro 3.5 이상, Basic(Creator) 이상, 확장 불필요"로 기재하되, 1번 검증 전까지는 "검증 예정"을 함께 표기합니다.
3. Esri 한국 대리점에 **(a) SDK 기반 Add-in 고객 배포·상업적 이용 조건, (b) Pro 내장 GDAL(ECW) 외부 사용 허용 여부**를 서면으로 문의합니다.
4. (승인 시) Add-in에 실행 전 라이선스·환경 사전 점검 기능을 넣는 것을 검토합니다(코드는 이번 검토에서 변경하지 않았음).
