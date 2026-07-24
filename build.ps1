$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONDONTWRITEBYTECODE = "1"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\pyinstaller.exe `
    --noconfirm `
    --clean `
    --windowed `
    --name "CoShell" `
    --icon "assets\app-icon.ico" `
    --add-data "static;static" `
    --add-data "assets\app-icon.ico;assets" `
    --add-data "assets\app-icon.png;assets" `
    --add-data "third_party\searxng;third_party\searxng" `
    --paths "third_party\searxng" `
    --collect-all webview `
    --collect-all searx `
    --hidden-import app.main `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols.http.auto `
    --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import uvicorn.lifespan.on `
    run.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$Version = & .\.venv\Scripts\python.exe -c "from app import __version__; print(__version__)"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$Archive = Join-Path $PSScriptRoot "dist\CoShell-v$Version-windows-x64-portable.zip"
Compress-Archive -Path (Join-Path $PSScriptRoot "dist\CoShell") -DestinationPath $Archive -CompressionLevel Optimal -Force

Write-Host "Build completed: $PSScriptRoot\dist\CoShell\CoShell.exe"
Write-Host "Release archive: $Archive"
