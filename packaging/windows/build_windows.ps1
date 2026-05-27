param(
    [string]$Python = "python",
    [string]$AppName = "SSAI-WX 通知小工具"
)

$ErrorActionPreference = "Stop"

if (-not $IsWindows) {
    throw "Windows installer must be built on Windows. Current platform is not Windows."
}

Write-Host "Creating virtual environment..."
& $Python -m venv .venv

Write-Host "Installing dependencies..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller

Write-Host "Building Windows executable with PyInstaller..."
& .\.venv\Scripts\pyinstaller.exe `
    --noconfirm `
    --windowed `
    --name $AppName `
    --icon assets\app_icon.ico `
    --add-data "assets;assets" `
    --hidden-import docx `
    app.py

Write-Host "Build complete. Output is in dist\$AppName"
