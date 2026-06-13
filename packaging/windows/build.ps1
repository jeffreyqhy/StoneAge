param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

$Python = "python"
if (-not $SkipInstall) {
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r requirements.txt -r requirements-build.txt
}

$IconDir = Join-Path $Root "build\windows"
$IconPath = Join-Path $IconDir "stoneage_app.ico"
New-Item -ItemType Directory -Force -Path $IconDir | Out-Null
& $Python -c "from pathlib import Path; from PIL import Image; src=Path('stoneage_studio/assets/app_icon.png'); dst=Path(r'$IconPath'); img=Image.open(src).convert('RGBA'); img.save(dst, sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "StoneAge Script Studio" `
    --icon "$IconPath" `
    --collect-all rapidocr_onnxruntime `
    --collect-all onnxruntime `
    --collect-all cv2 `
    --add-data "stoneage_studio/assets;stoneage_studio/assets" `
    --add-data "stoneage_studio/web;stoneage_studio/web" `
    "packaging/windows/launcher.py"

$AppDir = Join-Path $Root "dist\StoneAge Script Studio"
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "support") | Out-Null
Copy-Item "packaging/windows/install_and_run.bat" (Join-Path $AppDir "install_and_run.bat") -Force
Copy-Item "packaging/windows/run.bat" (Join-Path $AppDir "run.bat") -Force
Copy-Item "packaging/windows/WINDOWS_README.md" (Join-Path $AppDir "WINDOWS_README.md") -Force
Copy-Item "packaging/windows/support/bootstrap_support.ps1" (Join-Path $AppDir "support/bootstrap_support.ps1") -Force

Write-Host "Windows package ready: $AppDir"
