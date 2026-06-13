param(
    [string]$AppDir
)

$ErrorActionPreference = "Stop"

if (-not $AppDir) {
    $AppDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

$AppDir = (Resolve-Path $AppDir).Path
$SupportDir = Join-Path $AppDir "support"
$PlatformToolsDir = Join-Path $SupportDir "platform-tools"
$AdbExe = Join-Path $PlatformToolsDir "adb.exe"

New-Item -ItemType Directory -Force -Path $SupportDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "data") | Out-Null

if (-not (Test-Path $AdbExe)) {
    Write-Host "Downloading Android platform-tools..."
    $TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("stoneage-platform-tools-" + [guid]::NewGuid().ToString("N"))
    $ZipPath = Join-Path $TempDir "platform-tools.zip"
    New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
    try {
        Invoke-WebRequest -Uri "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" -OutFile $ZipPath -UseBasicParsing
        Expand-Archive -Path $ZipPath -DestinationPath $TempDir -Force
        if (Test-Path $PlatformToolsDir) {
            Remove-Item $PlatformToolsDir -Recurse -Force
        }
        Copy-Item (Join-Path $TempDir "platform-tools") $PlatformToolsDir -Recurse -Force
    }
    finally {
        Remove-Item $TempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$Exe = Join-Path $AppDir "StoneAge Script Studio.exe"
$RunBat = Join-Path $AppDir "run.bat"
if (Test-Path $RunBat) {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    $ShortcutPath = Join-Path $Desktop "StoneAge Script Studio.lnk"
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $RunBat
    $Shortcut.WorkingDirectory = $AppDir
    if (Test-Path $Exe) {
        $Shortcut.IconLocation = "$Exe,0"
    }
    $Shortcut.Save()
}

Write-Host "Setup complete."
