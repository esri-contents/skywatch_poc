# 광명시흥 판독 Add-in — ArcGIS 라이선스 기술 검토서

- 대상: `arcgis_addin/GwangmyeongInterpretation` (C#, 1,629행) + Add-in이 실행하는 Python 판독 코드 `src/interpretation` 및 그 import 범위
- 기준 커밋: `bc9adc4` (Add-in 소스 최종 변경 `95980fb`, v1.1)
- 검토일: 2026-11-06 · 코드 수정 없음

근거 표기:
- **[문서]** Esri 공식 문서에서 직접 확인 (URL·인용 포함)
- **[코드]** 소스코드 분석으로 판단
- **[실측]** 이 검토 중 개발 PC에서 실제 실행해 확인
- **[이력]** 과거 커밋 기록에 남은 시험 결과 (이번 검토에서 재현하지 않음)
- **[공식 확인 필요]** 공식 근거를 찾지 못함

---

## 1. 프로젝트 구조와 실행 경로

### 1.1 구성 요소

| 구성 | 경로 | 실행 위치 | Esri 라이선스 관련성 |
|---|---|---|---|
| Add-in (C#, .NET 8, WPF) | `arcgis_addin/GwangmyeongInterpretation/*.cs`, `Config.daml` | ArcGIS Pro 프로세스 안 | Pro SDK API, 지오프로세싱 5종 |
| 분석 Python (Python 3.12 + 고정 패키지) | `src/interpretation/addin_run.py` 진입 | **Pro 밖 별도 프로세스** | 없음 (arcpy 미사용) |
| 형식 변환 보조 | `src/interpretation/gdal_convert.py` | Pro의 `arcgispro-py3\python.exe`로 별도 프로세스 | Pro 내장 GDAL만 사용, arcpy 미사용 |

### 1.2 프로젝트 파일·종속성 [코드]

- `GwangmyeongInterpretation.csproj` (v1.2): `net8.0-windows`, WPF. 참조는 NuGet `Esri.ArcGISPro.Extensions30` **3.5.0.57366**(Esri 공식 Pro 3.5 참조 어셈블리, 빌드 산출물에 복사되지 않음)뿐이며, 패키징은 빌드 PC의 Pro `Esri.ProApp.SDK.Desktop.targets`. 이 NuGet 패키지에는 Esri 약관 `EULA.txt`(Click-Through Master Agreement E204CW)가 들어 있음.
  - 빌드된 DLL의 참조: `System.Runtime 8.0`, `ArcGIS.Core`·`ArcGIS.Desktop.Framework`·`.Core`·`.Mapping` **13.5.0.0**(= Pro 3.5) [실측].
  - v1.1까지는 Pro 3.7 설치 폴더 어셈블리를 직접 참조(`net10.0-windows`)했고, `ArcGIS.Desktop.Catalog`·`Layouts`는 참조만 있고 호출 없었음.
- `Config.daml`: `desktopVersion="3.5.0"` (5행) → **Pro 3.5 이상에서 로드**(Esri: 3.x 안에서 상위 버전 호환). 모듈 `autoLoad="false"`(14행). 버튼 10개, 도킹 창 1개. 라이선스·확장 조건(`condition`) 지정 **없음**.
  - `AddInInfo version="1.2"`(5행).

### 1.3 실행 경로 (버튼 → 실제 호출)

| 리본 버튼 | 클래스 (파일:행) | Pro 안에서 하는 일 | 별도 프로세스에서 하는 일 |
|---|---|---|---|
| 판독 실행 | `RunButton` (Module1.cs:131-134) → `RunWindow.RunClick` (RunWindow.xaml.cs:119-139) | 입력 검사, 완료 후 `MapResults.AddAsync`(RunWindow.xaml.cs:224) | `python -u -m src.interpretation.addin_run --t0 … --t2 …` (Runner.cs:127) → 영상 정리(addin_run.py:220) → 화소 변화탐지(addin_run.py:249-250) → 판독(addin_run.py:259) → 보고서·붙임 1 SHP(addin_run.py:271) |
| 보고서 열기 / 결과 폴더 | Module1.cs:136-143 / 168-175 | `Process.Start`로 OS 기본 프로그램(Module1.cs:115) | — |
| 보고서 다시 만들기 | Module1.cs:145-155 → RunWindow.xaml.cs:152 | — | `--report-only` (addin_run.py:180) |
| 결과 레이어 추가 | Module1.cs:157-166 → MapResults.cs:64-105 | 그룹 레이어, GeoPackage·SHP·래스터 레이어, 렌더러, 정의 쿼리, 확대 | — |
| 실행 이력 | Module1.cs:177-180 → HistoryWindow.xaml.cs:109-115 | `MapResults.AddAsync` 재사용 | — |
| 표본 검수 | Module1.cs:182-185 → ReviewPaneView.xaml.cs:152-162 | 지도 확대(`MapView.ZoomTo`, 161행), CSV 저장(QaTable.cs:64-65) | — |
| 현장조사 내보내기 | Module1.cs:203-229 | **지오프로세싱 5종**(Survey.cs:56-77), 레이어 추가 | `--survey-export` → survey.py:87 `export` (xlsx + OpenFileGDB 쓰기 175·177행) |
| 현장조사 결과 가져오기 | Module1.cs:208-233 | 파일 선택 대화상자 | `--survey-import` (addin_run.py:176) |
| 필지 도면 PDF | Module1.cs:236-252 | 완료 메시지, PDF 열기 | `--sheets` → sheets.py:124 `export`, Pillow로 PDF 저장(143행) |

---

## 2. 사용 API·지오프로세싱 전수 목록

### 2.1 지오프로세싱 도구 (`Geoprocessing.ExecuteToolAsync`) — 전부 Survey.cs [코드]

호출부는 `Survey.Gp` 한 곳(Survey.cs:36-49, 실제 호출 41행). 다른 파일에는 `ExecuteToolAsync` 호출이 **없음**. 입력은 항상 Python이 만든 **파일 지오데이터베이스**(`survey.gdb`)의 `survey_points`.

| # | 도구 | 호출 위치 | 파라미터 | Basic | Standard | Advanced | 근거 |
|---|---|---|---|---|---|---|---|
| 1 | `management.CreateDomain` | Survey.cs:63 | gdb, 도메인명, 별칭, `TEXT`, `CODED` | Yes | Yes | Yes | [문서] https://doc.esri.com/en/arcgis-pro/latest/tool-reference/data-management/create-domain.html |
| 2 | `management.AddCodedValueToDomain` | Survey.cs:67 | gdb, 도메인명, 코드, 설명 | Yes | Yes | Yes | [문서] …/add-coded-value-to-domain.html |
| 3 | `management.AssignDomainToField` | Survey.cs:68 | FC, 필드, 도메인 | Yes | Yes | Yes | [문서] …/assign-domain-to-field.html |
| 4 | `management.AlterField` | Survey.cs:70, 73 | FC, 필드, 새 이름 `""`, 새 별칭 | Yes | Yes | Yes | [문서] …/alter-field-properties.html |
| 5 | `management.EnableAttachments` | Survey.cs:74 | FC | Yes | Yes | Yes | [문서] …/enable-attachments.html |

- 원래 URL `pro.arcgis.com/en/pro-app/latest/tool-reference/data-management/*.htm`은 2026-11-06 기준 `doc.esri.com/en/arcgis-pro/latest/…`로 301 이전됨. 페이지에 버전 표기는 없고 경로가 `latest`.
- **파라미터·입력 종류에 따라 라이선스가 달라진다는 기술은 5종 모두 없음** [문서]. 엔터프라이즈 gdb 관련 주의(Enable Attachments: "must be from a database connection established as the data owner")는 라이선스가 아닌 권한 조건이며, 이 Add-in은 파일 gdb만 씀.
- Alter Field 문서에 파일 gdb에서 **별칭만** 바꾸는 경우를 명시한 문장은 없음 — 동작은 [이력]상 개발 PC에서 확인됐다고 기록되어 있으나 이번 검토에서 재현하지 않음.
- 오류 처리(v1.2): 도구마다 실패·예외를 로그에 쓰고 목록으로 모아(Survey.cs:36-49), 하나라도 실패하면 내보내기 끝에 대화상자로 알림(Module1.cs:220-225, license 문구 안내 포함). v1.1까지는 로그에만 남고 CreateDomain·AddCodedValue 실패는 로그도 없었음.
- 필드 이름(v1.2): 결과 필드 `EXISTS`는 파일 gdb 예약어라 GDAL이 `EXISTS_`로 바꿔 써서 도메인 지정이 실패하던 버그를 `EXIST_YN`으로 고침.

### 2.2 Pro SDK API (지오프로세싱 외) [코드]

| API | 사용 위치 | 용도 | 라이선스 판단 |
|---|---|---|---|
| `Module`, `Button`, `DockPane`, `ProWindow`, `FrameworkApplication`, `MessageBox` | Module1.cs, RunWindow·HistoryWindow·ReviewPaneView | UI | Pro 프레임워크(모든 등급) |
| `QueuedTask.Run` | MapResults.cs:64, Survey.cs:66·74, ReviewPaneView.xaml.cs:158 | Pro 작업 스레드 | 〃 |
| `LayerFactory.CreateGroupLayer` / `CreateLayer` (GeoPackage·SHP·래스터·FGDB) | MapResults.cs:68, 60-61, 73, 77, 94; Survey.cs:78 | 레이어 추가 | 데이터 표시는 Pro 코어 기능 [코드 판단] |
| `SetRenderer`(`CIMUniqueValueRenderer`, `CIMSimpleRenderer`), `SymbolFactory`, `ColorFactory` | MapResults.cs:81, 86, 90, 95, 101; Survey.cs:88-92 | 기호 | 〃 |
| `SetDefinitionQuery`, `SetVisibility`, `SetTransparency` | MapResults.cs:82-102 | 표시 제어 | 〃 |
| `Map.RemoveLayers` | Survey.cs:71 | 이전 조사 레이어 제거 | 〃 |
| `MapView.ZoomTo`, `SpatialReferenceBuilder`, `EnvelopeBuilderEx` | MapResults.cs:104; ReviewPaneView.xaml.cs:160-161 | 화면 이동 | 〃 |

- 편집(`EditOperation`), 레이아웃(`LayoutFactory`), 게시·포털(`ArcGISPortalManager`), 라이선스 조회 API 사용 **없음** (grep 결과).
- Pro SDK 기본 지도 API별 라이선스 등급을 표로 밝힌 공식 문서는 찾지 못함. Esri 문서는 "Core functionality is available with a Basic license"라고 하고, Standard/Advanced 필요 기능 표에 레이어 추가·기호·정의 쿼리는 없음 [문서: https://doc.esri.com/en/arcgis-pro/latest/get-started/license-levels.html]. 그 표는 스스로 "general guide"라고 밝히므로 **최종 확인은 실행 시험으로** 함.

### 2.3 외부 프로세스·외부 프로그램 [코드]

| 호출 | 위치 | 내용 |
|---|---|---|
| 분석 Python | Runner.cs:123-150 (`RunAsync`), 127행 | `python -u -m src.interpretation.addin_run …`. 환경변수 `PROJ_LIB` 등 제거(Runner.cs:75-76). Python은 설정값→사용자 Python→PATH 순 탐색(Runner.cs:33-43), 필수 패키지 import 확인(Runner.cs:22, 58) |
| Pro Python (형식 변환) | Runner.cs:25-30, RunWindow.xaml.cs:133-134 → ingest.py:56-80, 74행 `subprocess.run([pro, "-I", gdal_convert.py, …])` | `osgeo.gdal`만 import (gdal_convert.py:15), `gdal.Translate`(56행). **arcpy import 없음** |
| OS 기본 프로그램 | Module1.cs:115 | 보고서 HTML·PDF·폴더 열기 |

### 2.4 Python 분석 경로의 Esri 의존성 [코드]

- `src.interpretation.addin_run`에서 출발해 **함수 내부 import까지 포함한 정적 import 전수 추적**(AST 분석) 결과: 도달 가능한 `src` 모듈 47개, **arcpy를 import하는 모듈 0개**. arcpy를 쓰는 `src/arcpy_pipeline/*`, `src/arcpy_project_runner*.py`는 Add-in 경로에서 도달하지 않음.
- `arcgis`(ArcGIS API for Python)도 도달 범위에 없음.
- 분석 라이브러리: rasterio(GDAL 3.12.4 내장 휠), geopandas/pyogrio, shapely, numpy, scipy, scikit-learn, scikit-image, OpenCV, rasterstats, esda/libpysal, matplotlib, Pillow, openpyxl 등 — **모두 오픈소스, Esri 라이선스 대상 아님**.
- 영상 비교·구조물 판독·필지 공간분석은 **자체 알고리즘**(OBIA 분할, 판독기 학습, 화소 변화탐지)이며 Spatial Analyst·Image Analyst 도구를 쓰지 않음.

### 2.5 외부 서비스·네트워크 [코드]

- 도달 범위에 `requests`, `pystac_client`, `planetary_computer`가 있으나 경로는 `src.data.project_acquisition`(13·15·18행)과 `src.data.download`(34행)이며, 판독 경로는 이 모듈에서 **`load_project`(YAML 읽기, project_acquisition.py:36)만** 가져다 씀.
- 다운로드 함수(`acquire_all`)는 `project_pipeline.run(acquire=True)`에서만 호출되는데(project_pipeline.py:199), Add-in 경로는 `run`이 아니라 `preprocess`·`analyze`를 직접 부름(addin_run.py:249-250) → **네트워크 호출 미실행**.
- C# 쪽 HTTP·포털 API 사용 없음.
- 결론: **ArcGIS Online/Enterprise 로그인·서비스·크레딧을 Add-in이 요구하지 않음** [코드]. 런타임 네트워크 차단 상태 실행은 [테스트 계획 L5]로 확인 예정.

### 2.6 결과물 생성 방식 [코드]

| 결과물 | 생성 위치 | 라이브러리 | Esri 라이선스 |
|---|---|---|---|
| 보고서 HTML | addin_run.py:180, 271 → `generate_interpretation_official` | matplotlib, 자체 HTML | 없음 |
| 붙임 1 SHP | deliver.py:50 `to_file(driver="ESRI Shapefile")` | geopandas/pyogrio(GDAL) | 없음 |
| 벡터 결과 GPKG | `interpretation/vectors/*.gpkg` | geopandas | 없음 |
| survey.gdb (FC 생성) | survey.py:175, 177 `driver="OpenFileGDB"` | GDAL OpenFileGDB 드라이버(쓰기 지원 `raw` 실측 확인) | 없음 — FC 생성은 Esri 도구가 아님 |
| survey.gdb 도메인·별칭·첨부 | Survey.cs:56-77 | Pro 지오프로세싱 5종 | Basic 이상 [문서] |
| 현장조사표 xlsx | survey.py | openpyxl | 없음 |
| 필지 도면 PDF | sheets.py:143 `Image.save(…, "PDF")` | Pillow | 없음 — Pro 레이아웃 미사용 |

검토 요청 항목 중 **"속성 갱신"(Pro 편집), "Pro 지도 레이아웃 기반 도면"은 구현되어 있지 않음**(v1.1에서 레이아웃 방식 제거).

---

## 3. 기능별 라이선스 매트릭스

| 기능 | 사용자 관점 동작 | 코드 | Esri API/도구 | 최소 등급 | 확장 | 근거·신뢰도 | 실행 시험 |
|---|---|---|---|---|---|---|---|
| Add-in 로딩·리본 | 탭·버튼 표시 | Config.daml | Pro 프레임워크 | Basic | 없음 | [코드] 라이선스 조건 없음. 높음 | Advanced에서 [이력] |
| 영상 정리(형식 변환) | ECW·MrSID·JPG 등을 0.25m GeoTIFF로 | ingest.py:56-80, gdal_convert.py | Pro 내장 GDAL (arcpy 없음) | 해당 없음 | 없음 | [실측] arcpy 라이선스 초기화 실패 상태에서도 ECW 열기·읽기 성공. 높음 | [실측] |
| 두 시기 정합·화소 변화탐지 | 신규·소멸 교차 확인 | project_pipeline.py, src/change_detection | 오픈소스 | 해당 없음 | 없음 | [코드] 높음 | Advanced PC에서 [이력] |
| 구조물 판독·필지 판정 | 조각화·판독기 학습·필지 집계 | src/interpretation/pipeline.py 외 | 오픈소스·자체 알고리즘 | 해당 없음 | 없음 | [코드] 높음 | [이력] |
| 보고서 HTML·붙임 1 SHP | 보고서·SHP·zip | addin_run.py:180·271, deliver.py:50 | 오픈소스 | 해당 없음 | 없음 | [코드] 높음 | [이력] |
| 결과 레이어 추가 | 지도에 그룹 레이어 | MapResults.cs:64-105 | LayerFactory, CIM 렌더러 | Basic(판단) | 없음 | [코드]+[문서 일반] 중상 | Advanced [이력] |
| 표본 검수 | 사진 보며 판정, 지도 이동 | ReviewPaneView.xaml.cs | MapView.ZoomTo | Basic(판단) | 없음 | [코드] 중상 | Advanced [이력] |
| 현장조사 내보내기 | xlsx + survey.gdb + 도메인·첨부 | survey.py export, Survey.cs:56-77 | 도구 5종 | **Basic** | 없음 | [문서] 5종 Basic: Yes. 높음 | **Basic 미시험** |
| 현장조사 결과 가져오기 | xlsx/gdb 읽어 보고서 반영 | survey.py:190 | 오픈소스 | 해당 없음 | 없음 | [코드] 높음 | [이력] |
| 필지 도면 PDF | 붙임 1 필지별 A4 | sheets.py:124-150 | Pillow | 해당 없음 | 없음 | [코드] 높음 | [이력] |

---

## 4. Esri 공식 문서 대조 결과

| 확인 사항 | 결과 | 출처 |
|---|---|---|
| 사용자 유형 ↔ 등급 | "Creator—This user type includes a Basic license." / "Professional—…Standard license." / "Professional Plus—…Advanced license and several extension products." | [문서] https://doc.esri.com/en/arcgis-pro/latest/get-started/about-licensing.html |
| 등급별 기능 | "Core functionality is available with a Basic license." Standard/Advanced 필요 기능 표에 도메인·첨부·레이어 표시 없음. 표는 "general guide" | [문서] https://doc.esri.com/en/arcgis-pro/latest/get-started/license-levels.html |
| 확장 | "Extensions are typically purchased and licensed separately… several extensions are included with the Professional Plus user type." 어떤 확장이 포함되는지는 이 페이지에 없음 | [문서] about-licensing / https://doc.esri.com/en/arcgis-pro/latest/get-started/use-extensions-in-arcgis-pro.html |
| 라이선스 형태 | Named User: 기관 계정 로그인 필요. Single Use: "does not require an ArcGIS organization login." | [문서] about-licensing |
| 개발 PC 등급 해석 | 설치 파라미터 `SOFTWARE_CLASS`: Viewer=Basic, Editor=Standard, **Professional=Advanced** | [문서] https://doc.esri.com/en/arcgis-pro/latest/get-started/arcgis-pro-installation-administration.html |
| ECW·MrSID | 둘 다 "Read-only". "ECW and IIQ raster support is now configurable as an optional installation component… you have the option to disable ECW and IIQ support during ArcGIS Pro installation." | [문서] https://doc.esri.com/en/arcgis-pro/latest/help/data/imagery/supported-raster-dataset-file-formats.html |
| Add-in 라이선스 | Pro SDK 문서에 Add-in 실행에 특정 Pro 등급이 필요하다는 기술 없음. SDK의 라이선스 가이드는 **제3자가 자체 라이선스를 붙이는 방법**(Configurable Extension)임 | [문서] https://github.com/Esri/arcgis-pro-sdk/wiki/ProGuide-License-Your-Add-in |
| Add-in 보안·서명 | Add-In Manager 옵션 3단계("Load only Esri provided addins" / "Require addins to be digitally signed by a trusted publisher" / "Load all addins without restrictions"), 상용 인증기관 인증서 필요 | [문서] https://github.com/Esri/arcgis-pro-sdk/wiki/ProGuide-Digitally-signed-add-ins-and-configurations |
| 크레딧 | 위 라이선스 문서에 크레딧 언급 없음 | — |
| 파일 gdb의 등급별 가능 범위 | 해당 Pro 문서 URL이 404 | **[공식 확인 필요]** (단, 도구 5종 개별 문서로 대체 확인됨) |
| Pro SDK의 등급 조회 API 이름·사용법 | 가져온 문서에서 찾지 못함 | **[공식 확인 필요]** (사전 점검 기능 구현 시 SDK API 참조에서 확인) |
| SDK 기반 Add-in 배포·상업적 이용 약관 | developers.arcgis.com SDK 페이지에 조건 없음, 하단 약관 링크(https://www.esri.com/legal/software-license) 미확인 | **[공식 확인 필요]** |

ArcGIS Maps SDK for .NET(Lite/Basic/Standard/Advanced 런타임 라이선스)은 이 프로젝트에서 **사용하지 않음** — 참조 어셈블리는 모두 ArcGIS Pro SDK(`ArcGIS.Desktop.*`, `ArcGIS.Core`)임.

---

## 5. 라이선스별 실행 가능성

판정: 가능 = 공식 근거와 코드 요구사항 충족 / 조건부 가능 / 불가능 / 검증 필요.

| 환경 | Add-in 로딩 | 주요 분석 | 결과물 생성 | 전체 정상 실행 |
|---|---|---|---|---|
| Creator / Basic | 가능 | 가능 | 가능 | **가능 (실행 검증 필요)** |
| Professional / Standard | 가능 | 가능 | 가능 | **가능 (실행 검증 필요)** |
| Professional Plus / Advanced | 가능 | 가능 ([이력] 실행 확인) | 가능 ([이력]) | **가능** |
| Basic + 별도 Extension | 가능 | 가능 | 가능 | 가능 (확장 미사용, Basic과 동일) |
| Standard + 별도 Extension | 가능 | 가능 | 가능 | 가능 (확장 미사용, Standard와 동일) |

모든 행의 공통 조건(라이선스와 별개, 충족 못 하면 "불가능"): Pro **3.5 이상**(3.5·3.6 실기 시험 전), Add-in 보안 설정 허용, 분석 Python 배포 패키지 설치, ECW 원본 사용 시 Pro의 ECW 지원 설치.

---

## 6. 개발 PC 실측 기록 (2026-11-06)

| 항목 | 결과 |
|---|---|
| Pro 버전 | 3.7.0 (빌드 1901) — `HKLM\SOFTWARE\ESRI\ArcGISPro` |
| 라이선스 | `HKCU\…\ArcGISPro\Licensing`: `SOFTWARE_CLASS=Professional`(= **Advanced**, 공식 매핑), `SingleUse_Installed=Professional`, `LICENSE_SERVER_PRO_ON_PREMISE=none` |
| 확장 목록 | 확인 불가 — Pro 밖 arcpy import 시 `RuntimeError: 제품 라이선스가 초기화되지 않았습니다` (Pro 앱 안 Python 창 또는 [라이선스] 화면에서 확인해야 함) |
| Pro GDAL ECW | `gdal 3.12.2e`, ECW·MrSID·JP2ECW 드라이버 있음, 원본 ECW 102,366×203,051×3 열기·읽기 성공, **arcpy 미로드 상태** |
| 분석 Python | GDAL 3.12.4(rasterio·pyogrio), OpenFileGDB 쓰기 지원 |
| 과거 시험 | 커밋 `95980fb`·`eefad4d`: 원본 ECW/JPG로 무캐시 전 과정 실행, 전 버튼 실행, 테스트 229개 통과 — **모두 이 Advanced PC** |
| survey.gdb 실물 | `D:\광명시흥_판독` 아래 실행 결과에 survey.gdb 없음 → 도메인·첨부 적용 결과를 이번 검토에서 직접 확인하지 못함 |

---

## 7. 확장 라이선스 의존성

- Spatial Analyst(`sa.*`), Image Analyst(`ia.*`), 3D Analyst(`3d.*`), Geostatistical Analyst 등 **확장 도구 호출 없음** — `ExecuteToolAsync`는 `management.*` 5종뿐이고, Python 경로에 arcpy 없음.
- 확장 체크아웃 코드(`CheckExtension`/`CheckOutExtension` 등) 없음.
- 결론: **확장 라이선스 불필요** [코드+문서].

---

## 8. 미확인 사항과 기술적 위험

| # | 항목 | 영향 | 대응 |
|---|---|---|---|
| R1 | Basic·Standard에서 실제 실행 안 해 봄 | 결론 확정 불가 | 테스트 계획 L1·L2 |
| R2 | (v1.2에서 해결) 지오프로세싱 실패가 로그에만 남던 문제 → 실패 목록을 대화상자로 알림 | 해결 | 테스트 시 대화상자·로그·gdb 확인 |
| R3 | Pro 설치 시 ECW 지원을 끌 수 있음 [문서] | ECW 원본 변환 실패("형식 변환 실패") | 고객 PC 사전 점검(테스트 계획 E2) |
| R4 | 3.5 이상 공용(`desktopVersion="3.5.0"`, .NET 8). 3.7(.NET 10)에서의 동작은 Esri 문서 근거("Add-ins deployed on ArcGIS Pro 3.0 - 3.6 will run on 3.7 … without re-compilation"), 3.5·3.6 실기 미시험 | 3.4 이하 PC에서 탭 미표시 | 고객 Pro 버전 확인, 3.5·3.6 PC에서 L0~L4 |
| R5 | Add-in 보안 정책(서명) | 기관 PC에서 탭 미표시 | 기관 인증서 서명 또는 정책 조정 |
| R6 | Pro 내장 GDAL(ECW 드라이버)을 Pro 외부 Python 프로세스에서 사용 | 기술적으로는 동작 [실측]. 약관상 허용 범위 미확인 | **[공식 확인 필요]** Esri 서면 문의 |
| R7 | 배포 패키지에 기초자료·2022 기준 영상 포함(README) | 자료 제공기관 재배포 조건 | 자료별 이용 조건 확인 |
| R8 | 분석 Python 오픈소스 패키지(약 90종) 라이선스 고지 | 상용 배포 시 고지 의무 | 패키지 라이선스 목록 작성 |
| R9 | Field Maps 사용 시 게시·현장 사용자 계정 | Add-in 밖 비용 | 별도 산정 |

### 라이선스 사전 점검 기능 제안 (코드 변경 안 함, 승인 필요)

1. Add-in 시작 시 또는 [판독 실행] 창의 [환경 확인] 버튼에서 Pro 버전·라이선스 등급을 읽어 표시 — 사용할 SDK API(예: `ArcGIS.Core.Licensing` 계열)의 정확한 이름·동작은 **[공식 확인 필요]**: Pro 3.7 SDK API 참조에서 확인 후 구현.
2. `gdal_convert.py`에 `--probe` 옵션을 두고 ECW·MrSID 드라이버 유무를 반환 → 실행 전 경고.
3. (v1.2에서 반영) 현장조사 내보내기에서 지오프로세싱 실패 시 오류 문구를 대화상자로 표시하고 license 문구 확인을 안내.
