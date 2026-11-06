<#
.SYNOPSIS
  "광명시흥 판독" 배포 패키지 설치 - 판독 코드·Python을 설치 폴더로 복사하고 Add-in을 등록, 설정을 기록한다.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1
  powershell -ExecutionPolicy Bypass -File install.ps1 -InstallDir D:\광명시흥판독
#>
param([string]$InstallDir = "C:\GwangmyeongInterpretation")
$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
if (Get-Process ArcGISPro -ErrorAction SilentlyContinue) { throw "ArcGIS Pro를 닫고 다시 실행하세요." }

Write-Host "설치 위치: $InstallDir"
foreach ($d in "app", "python") {
    robocopy "$src\$d" "$InstallDir\$d" /MIR /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "$d 복사 실패" }
}

# Add-in: Pro가 시작할 때 읽는 기본 폴더
$addins = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "ArcGIS\AddIns\ArcGISPro"
New-Item -ItemType Directory -Force $addins | Out-Null
Copy-Item "$src\addin\GwangmyeongInterpretation.esriAddinX" $addins -Force

# Add-in 설정(%APPDATA%\GwangmyeongInterpretation\settings.json): 코드 폴더·분석 Python 경로. 기존 설정의 나머지 값은 유지
$cfgDir = Join-Path $env:APPDATA "GwangmyeongInterpretation"
New-Item -ItemType Directory -Force $cfgDir | Out-Null
$cfgFile = Join-Path $cfgDir "settings.json"
$cfg = if (Test-Path $cfgFile) { Get-Content $cfgFile -Raw -Encoding utf8 | ConvertFrom-Json } else { New-Object psobject }
$cfg | Add-Member -Force NoteProperty RepoPath "$InstallDir\app"
$cfg | Add-Member -Force NoteProperty AnalysisPython "$InstallDir\python\python.exe"
if (-not $cfg.OutRoot) { $cfg | Add-Member -Force NoteProperty OutRoot (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "광명시흥_판독") }
[IO.File]::WriteAllText($cfgFile, ($cfg | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding $false))

# 점검
$env:PYTHONNOUSERSITE = "1"
& "$InstallDir\python\python.exe" -s -c "import rasterio.warp, rasterstats, sklearn, cv2, skimage, geopandas, yaml, pyarrow, matplotlib, openpyxl; print('분석 Python 정상')"
if (-not (Test-Path "${env:ProgramFiles}\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe")) {
    Write-Warning "ArcGIS Pro 파이썬을 찾지 못함 - ECW·MrSID 입력은 변환할 수 없습니다(GeoTIFF·JPG 등은 가능)."
}
Write-Host "설치 완료. ArcGIS Pro를 열면 [광명시흥 판독] 탭이 보입니다."
