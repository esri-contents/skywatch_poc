# 광명시흥 판독 Add-in — 라이선스별 실행 검증 테스트 계획

- 목적: [기술 검토서](ARCGIS_ADDIN_LICENSE_TECHNICAL_AUDIT.md)의 코드·문서 기반 결론("Basic으로 전 기능 실행, 확장 불필요")을 실제 라이선스 환경에서 확정한다.
- 대상: Add-in v1.2 배포 패키지(`build_dist.ps1 -Zip` 산출물), ArcGIS Pro 3.5 이상(3.5·3.6·3.7 공용 빌드)
- 현재까지 실행 증거: Advanced PC 1대뿐. 이 계획의 L1이 통과해야 "Basic 확정"으로 보고할 수 있다.

## 1. 테스트 환경

| ID | 사용자 유형 / 등급 | 라이선스 형태 | 확장 | 필수 여부 |
|---|---|---|---|---|
| ENV-B | Creator / **Basic** | Named User 또는 Single Use | **모두 해제** | **필수** |
| ENV-S | Professional / Standard | 〃 | 모두 해제 | 권장 |
| ENV-A | Professional Plus / Advanced | 〃 | 기본값 | 대조군(선택) |

공통: Windows 10/11 x64, ArcGIS Pro **고객이 쓰는 버전(3.5.x / 3.6.x / 3.7.x)** — 버전마다 L0·L1·L2를 한 번씩 돌리면 버전 호환도 함께 확인된다, 출력 드라이브 여유 3GB 이상, 입력 = 저장소의 2022 ECW + 2024 JPG(개발 PC와 같은 파일, 결과 비교용).

**등급 바꾸는 방법**
- Named User: 기관 포털 관리자가 시험 계정의 사용자 유형을 Creator로 지정하고 ArcGIS Pro 라이선스를 Basic으로, 확장 할당 모두 해제 → Pro 재로그인.
- Single Use: ArcGIS Pro 라이선스 관리 화면에서 Basic 인증 파일로 인증.
- 확인: Pro [설정] > [라이선스]에서 "ArcGIS Pro Basic", 확장 목록 "라이선스 없음"을 화면 캡처.

## 2. 사전 점검 (모든 환경)

| ID | 확인 | 방법 | 합격 기준 |
|---|---|---|---|
| E1 | Pro 버전 | Pro [설정] > [정보] 또는 `HKLM\SOFTWARE\ESRI\ArcGISPro` 의 `RealVersion` | 3.5 이상 |
| E2 | ECW·MrSID 지원 | PowerShell: `& "C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" -I -c "from osgeo import gdal; print(gdal.GetDriverByName('ECW') is not None, gdal.GetDriverByName('MrSID') is not None)"` | `True True` (ECW 원본을 쓸 경우) |
| E3 | 라이선스 등급 | Pro [설정] > [라이선스] 캡처. Pro 안 [Python 창]에서 `arcpy.ProductInfo()` | ENV-B에서 `ArcView`(Basic) 표시 |
| E4 | 확장 | 같은 화면 캡처, Python 창에서 `arcpy.CheckExtension("Spatial")`, `"ImageAnalyst"`, `"3D"` | ENV-B·S에서 모두 `Unavailable` 또는 미할당 |
| E5 | Add-in 보안 설정 | Pro [설정] > [Add-In Manager] > [옵션] | 이 Add-in이 로드 가능한 설정이거나 서명됨 |
| E6 | 배포 패키지 설치 | `install.ps1` 실행 후 `C:\GwangmyeongInterpretation\python\python.exe` 존재 | 존재 |

> 참고: Pro **밖**에서 arcpy를 import하면 "제품 라이선스가 초기화되지 않았습니다" 오류가 날 수 있다(개발 PC에서 확인). E3·E4는 Pro 앱 안의 Python 창에서 실행한다.

## 3. 라이선스별 시나리오

각 시나리오를 ENV-B에서 반드시, ENV-S·ENV-A에서 가능하면 수행한다.

| ID | 시나리오 | 절차 | 예상 결과 | 합격 기준 |
|---|---|---|---|---|
| L0 | Add-in 로딩 | `.esriAddinX` 설치 → Pro 시작 → 지도 프로젝트 열기 | 리본에 "광명시흥 판독" 탭, 버튼 10개 | 탭·버튼 표시, Add-In Manager에 오류 없음 |
| L1 | 판독 실행(무캐시) | [판독 실행] → T0=2022 ECW, T2=2024 JPG, 날짜 입력, 새 출력 폴더 → [환경 확인] → 실행 | 5단계 진행 후 보고서 열림, 결과 레이어 추가 | 종료 코드 0, 보고서 HTML·붙임 1 SHP·GPKG 생성, 경고 창에 "화소 변화탐지 실패" 없음, 수치가 ENV-A 결과와 같음(필지 수·독립 미등재 수·신규 A·소멸 A·B) |
| L2 | 현장조사 내보내기 | [현장조사 내보내기] | xlsx·survey.gdb 생성, "사진 첨부 사용 설정 완료" 로그, "현장조사 대상" 레이어 | 로그에 "실패" 문구 0건, 아래 G1~G3 확인 통과 |
| L3 | 표본 검수 / 레이어 / 이력 | [표본 검수]에서 1·2·3 판정 몇 건, [결과 레이어 추가], [실행 이력]에서 레이어 다시 열기 | 지도 이동, CSV 저장, 그룹 레이어 추가 | 오류 대화상자 없음, `qa_labels.csv`에 판정 저장 |
| L4 | 보고서 재작성·현장조사 가져오기·필지 도면 PDF | [보고서 다시 만들기] → 조사표 xlsx 일부 작성 후 [현장조사 결과 가져오기] → [필지 도면 PDF] | 새 보고서(시각 접미사), Ⅳ-3 절, PDF | 각 산출물 생성, Pro 종료 없음 |
| L5 | 오프라인 실행 | 네트워크 차단(Single Use 또는 Named User 오프라인 체크아웃) 상태에서 L1(캐시 사용 가능) | 정상 완료 | 네트워크 없이 완료 → 온라인 서비스 의존 없음 확정 |

### L2 상세 확인 (지오프로세싱 5종 = Basic 판정의 핵심)

| ID | 확인 | 방법 | 합격 |
|---|---|---|---|
| G1 | 도메인 | Pro 카탈로그에서 survey.gdb 우클릭 > [도메인] | `D_EXISTS`, `D_USE`, `D_REG`와 코드값 존재 |
| G2 | 필드 도메인·별칭 | `survey_points` [필드] 보기 | EXIST_YN·USE_TYPE·REGISTERED에 도메인, 한글 별칭(구조물 실재 등) |
| G3 | 첨부 | `survey_points__ATTACH` 테이블·관계 클래스 존재 | 존재 |

## 4. 라이선스 오류 확인 방법

1. **Add-in 실행 창 로그**: Survey.cs는 도구 실패 시 `management.<도구> 실패: <오류>`를 로그에 쓰고 계속 진행하며, 끝에 실패 목록을 대화상자로 보여 준다(v1.2). 대화상자를 캡처하고, 로그 전체를 복사해 보관하고 "실패", "license", "라이선스" 문구를 검색한다.
2. **Pro 지오프로세싱 기록**: [분석] > [기록(History)]에서 5종 도구의 성공/실패와 메시지 확인. 라이선스 부족 시 Esri는 보통 "ERROR 000824: The tool is not licensed." 형태의 메시지를 낸다 — 문구는 **[공식 확인 필요]**, 실제 메시지를 캡처해 기록.
3. **분석 Python 단계**: Esri 라이선스를 쓰지 않으므로 라이선스 오류가 나면 안 된다. 실패 시 `@@ERROR` 줄과 `run_info.json`을 보관하고 라이선스 외 원인(패키지·디스크·영상)으로 분류한다.
4. **형식 변환 실패**: "형식 변환 실패" 메시지는 ECW 지원 미설치(E2)를 먼저 의심한다.

## 5. 증거 수집

환경마다 폴더 하나(`license_test_<ENV>_<날짜>`)에 다음을 모은다.

- Pro [라이선스] 화면, [정보] 화면 캡처 (E1·E3·E4)
- E2 명령 출력
- 시나리오별: 실행 창 로그 전문(텍스트), 완료 화면 캡처, 산출물 목록(`dir /s` 출력)
- L1: `run_info.json`, 보고서 HTML 첫 화면 캡처
- L2: 지오프로세싱 기록 캡처, G1~G3 화면 캡처
- 이상 발생 시: 오류 대화상자·메시지 원문, `%APPDATA%\GwangmyeongInterpretation\settings.json`

결과표 양식:

| 환경 | E1-E6 | L0 | L1 | L2 (G1-G3) | L3 | L4 | L5 | 비고 |
|---|---|---|---|---|---|---|---|---|
| ENV-B |  |  |  |  |  |  |  |  |
| ENV-S |  |  |  |  |  |  |  |  |
| ENV-A |  |  |  |  |  |  |  |  |

## 6. 최종 판정 기준

| 결과 | 판정 | 보고 문구 |
|---|---|---|
| ENV-B에서 L0~L4 모두 합격 | **Basic 확정** | "ArcGIS Pro Basic(Creator) 이상, 확장 불필요 — 실행 검증 완료" |
| ENV-B에서 L2만 지오프로세싱 라이선스 오류, ENV-S 합격 | 판독·보고서는 Basic, 현장조사 gdb 설정은 Standard | 필수/선택 기능을 나눠 보고(현장조사 gdb 설정은 엑셀 조사표로 대체 가능한 선택 기능) |
| ENV-B·S 모두 L2 실패, ENV-A 합격 | 현장조사 gdb 설정만 Advanced | 위와 같이 나눠 보고하고 Esri에 문서 불일치 문의 |
| 라이선스 외 원인 실패(E2·E5·패키지) | 라이선스 판정에 영향 없음 | 설치·환경 요건으로 별도 기재 |
| L5 합격 | 온라인 서비스·크레딧 불필요 확정 | — |

판정이 문서와 다르게 나오면(예: Basic에서 "not licensed") 공식 문서 URL과 실제 메시지를 함께 Esri 기술지원에 문의하고, 응답 전까지 보수적으로 상위 등급을 기재한다.
