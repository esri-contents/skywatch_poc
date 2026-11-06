# 광명시흥 필지 구조물 판독 - ArcGIS Pro Add-in

ArcGIS Pro 3.7 리본에 **광명시흥 판독** 탭을 추가한다. 항공사진 두 장(기준·비교 시기)을 넣으면 `src/interpretation`의 판독 전 과정을 돌려 공공기관 보고서 HTML(A4)·붙임 1 SHP·결과 레이어를 만든다.

## 리본

**판독** 그룹

| 버튼 | 하는 일 |
|---|---|
| 판독 실행 | 영상·촬영일·출력 폴더를 받아 실행(처음 25분 안팎, 같은 영상 재실행은 재사용으로 3분 안팎). 진행·로그 표시, 취소 가능. 변화탐지 실패·정합 이상은 끝날 때 주의 창으로 알림. 출력 기본값은 여유 공간이 가장 큰 드라이브의 `\광명시흥_판독`(실행당 1~2GB, 3GB 미만이면 시작 전에 멈춤) |
| 보고서 열기 | 현재 결과의 보고서 HTML을 브라우저로 연다 |
| 보고서 다시 만들기 | 검수·현장조사 입력을 반영해 보고서(와 붙임 1 SHP)만 다시 만든다 |
| 결과 레이어 추가 | 붙임 1 필지(SHP)·필지 판독 상태·미등재·신규/소멸(A·B)·판독 덩어리·두 영상을 지도에 그룹으로 추가 |
| 결과 폴더 | 산출물 폴더를 연다 |
| 실행 이력 | 출력 상위 폴더의 지난 실행(날짜·입력 영상·소요 시간·필지·독립 미등재·신규 A·소멸 A·B·재사용 여부) 목록. 보고서·레이어·폴더를 다시 열거나 "현재 결과"로 고른다 |

**검수·현장조사** 그룹

| 버튼 | 하는 일 |
|---|---|
| 표본 검수 | 도킹 창에서 검수 표본(`qa/review1/*.jpg`)을 한 장씩 보며 구조물 맞음·아님·판단 불가(단축키 1·2·3, ←/→). 판정마다 `qa_labels.csv`의 label·note·judge(판독자)를 바로 저장, 지도가 표본 위치로 따라감, 다 끝나면 보고서 다시 만들기 |
| 현장조사 내보내기 | 붙임 1(U001…)·신규 A(A01…)·소멸 A·B(G01…)를 근거 영상이 붙은 `현장조사표.xlsx`(결과 칸 드롭다운)와 Field Maps용 `survey.gdb`(survey_points·survey_areas)로. gdb에는 Pro 지오프로세싱으로 코드값 도메인·한글 별칭·사진 첨부를 설정하고 지도에 올린다. 다시 내보내면 이전 조사표·gdb는 지우지 않고 `_날짜시각`을 붙여 보관 |
| 현장조사 결과 가져오기 | 작성한 엑셀 또는 Field Maps에서 동기화한 gdb를 읽어 `survey/survey_results.csv`로 정리하고 보고서에 "Ⅳ-3 현장조사 결과"(구분별 현장 정밀도, 물건조서 누락 의심 = 실재 + 미등재, 용도)와 붙임 1 "현장" 열을 넣는다 |
| 필지 도면 PDF | 붙임 1 필지마다 A4 1쪽(위 기준 시기, 아래 비교 시기, 필지 정보 칸, 축척·방위)을 분석 Python(`sheets.py`)이 그려 `필지도면_붙임1_<생성 시각>.pdf` 한 파일로(320쪽 약 3분). Pro 레이아웃·지도 시리즈로 내보내던 v1.0은 Pro가 종료되는 문제가 있어 바꿨다 |

## 사용설명서

`docs/`에 한국어·영어·중국어 사용설명서(docx)가 있다 - 입력 자료, 설치, 버튼별 사용법, 산출물, 유의사항, 문제 해결.
다시 만들려면 `docs/src`에서 `npm install` 후 `node build.js ..`(그림은 `img/`, 문장은 `content_*.js`).

## 산출물(실행 폴더 `<출력 상위 폴더>/<실행 시각>/`)

- `interpretation/reports/…_official.html` 보고서, `appendix1_unregistered_parcels/`(.shp·.csv·필드설명.txt)와 같은 이름의 `.zip` - **보고서 붙임 1과 같은 목록·연번(SEQ)**.
  보고서 다시 만들기·현장조사 가져오기는 둘 다 `_YYYYMMDD_HHMMSS`를 붙여 새로 쓴다(열려 있거나 Pro가 잠근 이전 파일은 그대로). `run_info.json`의 `result`가 가장 최근 것을 가리킨다
- `interpretation/vectors/*.gpkg`, `reports/parcel_interpretation.csv`, `evidence/`, `qa/review1/`, `survey/`
- `run_info.json`(입력 영상 해시·정합 결과·소요 시간·주요 수치), `project.yaml`(이 실행의 설정)

## 구조

```
Pro(Add-in, C#) ──하위 프로세스──▶ 분석 Python: python -u -m src.interpretation.addin_run ...
                                    ├ 1·2 영상 정리(ingest.py) ──형식 변환만──▶ Pro Python(arcgispro-py3) gdal_convert.py
                                    ├ 3 화소 변화탐지(project_pipeline)  - 신규·소멸 A·B 등급의 교차 확인
                                    ├ 4 조각화·판독기 학습·필지 판정(pipeline.run)
                                    ├ 5 보고서 + 붙임 1 SHP(generate_interpretation_official, deliver.py)
                                    └ (버튼) 필지 도면 PDF(sheets.py), 현장조사 내보내기·가져오기(survey.py)
            ──Pro 안──▶ 레이어 추가, 표본 검수 창, survey.gdb 도메인·첨부(지오프로세싱)
```

- 판독을 Pro 프로세스 안에서 돌리지 않는 이유: 분할 단계가 다중 프로세스를 쓰고, Pro 3.7 기본 환경의 `rasterio.warp`가 DLL 오류로 불러와지지 않는다. Pro 밖의 arcpy는 라이선스가 초기화되지 않아, gdb 도메인·첨부는 Add-in(C#)이 Pro 안에서 처리한다.
- 배포 Python 패키지는 `requirements-runtime.txt`(고정 버전). `build_dist.ps1`이 마지막에 `check_imports.py`로 판독 코드의 모든 import를 배포 Python에서 불러 보고, 빠진 것이 있으면 중단한다(v1.0에서 rasterstats 누락). `tests/test_addin.py`도 같은 것을 정적으로 검사한다.
- 입력 영상: GeoTIFF·ECW·JPEG2000·MrSID·IMG·JPG/PNG(+월드파일). 좌표계가 없으면 EPSG:5186으로 보고, 좌표가 없으면 2022-12-01 기준 영상에 SIFT+위상상관으로 자동 정합한다(원본 2024 JPG로 시험: 기존 수작업 정합과 0.10m 차이).
- 재사용 캐시(`<출력 상위 폴더>/_cache/`): 영상 정리 결과는 원본 파일 SHA-256으로, 분할·판독기·화소 변화탐지 결과는 두 영상 해시·기초자료 해시·관련 설정으로 찾는다. 하드링크라 디스크를 더 쓰지 않는다. 끄려면 `--no-cache`.
- 저장소 결과와 검수 라벨(round1~4)은 건드리지 않는다. 새 실행의 검수 표본은 `review1` 회차.

## 빌드·설치

개발 PC(Visual Studio 불필요):
```powershell
cd arcgis_addin\GwangmyeongInterpretation
dotnet build -c Release          # .NET 10 SDK, Pro 3.7 → bin\Release\GwangmyeongInterpretation.esriAddinX
```

다른 실무자 PC 배포:
```powershell
powershell -ExecutionPolicy Bypass -File arcgis_addin\build_dist.ps1 -Zip      # dist\GwangmyeongInterpretation(.zip)
# 대상 PC에서 압축을 풀고
powershell -ExecutionPolicy Bypass -File install.ps1                            # C:\GwangmyeongInterpretation
```
배포 폴더는 `addin\`(Add-in), `app\`(판독 코드·광명시흥 기초자료·2022 기준 영상), `python\`(분석 Python 3.12 + `requirements-runtime.txt` 고정 버전)으로 되어 있어 저장소·개인 Python이 필요 없다. 대상 PC에는 ArcGIS Pro 3.7만 있으면 된다(ECW·MrSID 변환에 Pro의 GDAL 사용).

### 코드 서명

- Add-in은 서명하지 않아도 동작한다. 단 기관 PC의 Pro 옵션 > Add-In Manager 보안 설정이 "디지털 서명된 Add-in만 로드"면 탭이 나타나지 않는다.
- 그런 PC에 배포하려면 기관 코드 서명 인증서(.pfx)로 서명한다: `build_dist.ps1 -SignCert <인증서.pfx>`(Pro에 들어 있는 `ArcGISSignAddIn.exe` 사용). 자체 서명 인증서는 대상 PC의 신뢰된 게시자에 등록해야 한다.
- 정책을 바꾸는 것보다 서명이 권장된다. 배포 범위가 LH·GH 내부 몇 대면 IT 부서와 인증서 발급·정책을 먼저 확인한다.
