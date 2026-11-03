<#
.SYNOPSIS
  다른 실무자 PC에 배포할 "광명시흥 판독" 패키지 폴더를 만든다(저장소 경로·개인 Python 의존 없음).

.DESCRIPTION
  결과 폴더 구성(-Dest, 기본 dist\GwangmyeongInterpretation):
    addin\GwangmyeongInterpretation.esriAddinX   Pro Add-in(-SignCert 주면 서명)
    app\                                         판독 코드(src)·광명시흥 설정·기초자료·2022 기준 영상
    python\                                      분석 Python 3.12 + requirements-runtime.txt 패키지(고정 버전)
    install.ps1, 설치안내.txt
  대상 PC 조건: Windows 10/11 64비트, ArcGIS Pro 3.7(ECW·MrSID 형식 변환에 Pro의 GDAL을 쓴다).
  python\은 이 PC의 Python 3.12 설치본을 복사한 뒤 패키지를 그 안에 설치한다(인터넷 필요, 약 1.5GB).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File arcgis_addin\build_dist.ps1
  powershell -ExecutionPolicy Bypass -File arcgis_addin\build_dist.ps1 -Zip -SignCert C:\cert\org.pfx
#>
param(
    [string]$Dest = (Join-Path $PSScriptRoot "..\dist\GwangmyeongInterpretation"),
    [string]$PythonHome = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312"),
    [string]$SignCert = "",
    [switch]$SkipPython,
    [switch]$Zip
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Proj = "data\projects\gwangmyeong_siheung_aerial"
New-Item -ItemType Directory -Force $Dest | Out-Null
$Dest = (Resolve-Path $Dest).Path
Write-Host "배포 폴더: $Dest"

# 1) Add-in 빌드(+서명)
$addinProj = Join-Path $PSScriptRoot "GwangmyeongInterpretation"
dotnet build $addinProj -c Release | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Add-in 빌드 실패" }
New-Item -ItemType Directory -Force "$Dest\addin" | Out-Null
Copy-Item "$addinProj\bin\Release\GwangmyeongInterpretation.esriAddinX" "$Dest\addin\" -Force
if ($SignCert) {
    # ArcGIS Pro에 들어 있는 서명 도구. 기관 코드 서명 인증서(.pfx)가 필요하다(암호는 실행 중 입력).
    & "${env:ProgramFiles}\ArcGIS\Pro\bin\ArcGISSignAddIn.exe" "$Dest\addin\GwangmyeongInterpretation.esriAddinX" /c:$SignCert
}

# 2) 판독 코드·설정·기초자료(Add-in 실행에 필요한 것만)
$app = "$Dest\app"
robocopy "$Repo\src" "$app\src" /E /XD __pycache__ /NFL /NDL /NJH /NJS | Out-Null
$files = @(
    "config\config.yaml", "config\projects\gwangmyeong_siheung_aerial.yaml",
    "$Proj\aoi\gwangmyeong_siheung_official.gpkg", "$Proj\processed\buildings.gpkg", "$Proj\processed\cadastre.gpkg",
    "$Proj\raw\building_register\title_info.json", "$Proj\raw\building_permits\basic_outline.json",
    "$Proj\raw\imagery\aerial_20221201_0.25m.tif",          # 자동 정합 기준 영상(필수)
    "$Proj\raw\imagery\georef_2024_report.json",
    "arcgis_addin\requirements-runtime.txt"
)
foreach ($f in $files) {
    New-Item -ItemType Directory -Force (Split-Path "$app\$f") | Out-Null
    Copy-Item "$Repo\$f" "$app\$f" -Force
}
# 2024 정합 영상은 1.6GB라 기본 제외 - 없으면 같은 원본 JPG를 넣었을 때 자동 정합으로 다시 만든다(약 5분)
Write-Host "판독 코드·기초자료 복사 완료"

# 3) 분석 Python(이 PC 설치본 복사 → 패키지 설치). 사용자 site-packages는 쓰지 않는다(-s).
if (-not $SkipPython) {
    if (-not (Test-Path "$PythonHome\python.exe")) { throw "Python 3.12 설치본이 없음: $PythonHome" }
    robocopy $PythonHome "$Dest\python" /E /XD __pycache__ site-packages Scripts /NFL /NDL /NJH /NJS | Out-Null
    New-Item -ItemType Directory -Force "$Dest\python\Lib\site-packages" | Out-Null
    $env:PYTHONNOUSERSITE = "1"
    # 다운로드·압축 해제 임시 파일은 배포 폴더 드라이브에 둔다(시스템 드라이브 공간이 부족한 PC 대비)
    $tmp = Join-Path (Split-Path $Dest) "_build_tmp"
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $env:TEMP = $env:TMP = $tmp
    & "$Dest\python\python.exe" -s -m ensurepip --upgrade | Out-Null
    & "$Dest\python\python.exe" -s -m pip install --no-cache-dir --no-warn-script-location -r "$app\arcgis_addin\requirements-runtime.txt"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) { throw "패키지 설치 실패" }
    & "$Dest\python\python.exe" -s -c "import rasterio.warp, sklearn, cv2, skimage, geopandas, yaml, pyarrow, matplotlib, openpyxl; print('python ok')"
}

# 4) 설치 스크립트·안내
Copy-Item "$PSScriptRoot\install.ps1" "$Dest\install.ps1" -Force
@"
광명시흥 필지 구조물 판독 - ArcGIS Pro Add-in 설치 안내

1. ArcGIS Pro 3.7을 닫는다.
2. 이 폴더에서 PowerShell을 열고 실행:
     powershell -ExecutionPolicy Bypass -File install.ps1
   (설치 위치를 바꾸려면 -InstallDir D:\광명시흥판독)
3. ArcGIS Pro를 열면 리본에 [광명시흥 판독] 탭이 생긴다.
   - 탭이 안 보이면: Pro 옵션 > Add-In Manager > 옵션에서 서명 정책을 확인(서명 안 된 Add-in 차단 시 기관 인증서로 서명된 판으로 교체)
4. [판독 실행]에서 기준·비교 시기 항공사진과 촬영일을 넣고 실행(30분 안팎).
   첫 실행 전 [옵션 및 환경] > [환경 점검]으로 분석 Python이 잡히는지 확인.

구성: addin\(Add-in) · app\(판독 코드·광명시흥 기초자료) · python\(분석 Python, 인터넷 불필요)
"@ | Out-File -Encoding utf8 "$Dest\설치안내.txt"

if ($Zip) {
    $z = "$Dest.zip"
    if (Test-Path $z) { Remove-Item $z }
    Compress-Archive -Path "$Dest\*" -DestinationPath $z
    Write-Host "zip: $z"
}
$mb = [math]::Round((Get-ChildItem $Dest -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "완료: $Dest ($mb MB)"
