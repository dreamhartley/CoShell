$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\pyinstaller.exe `
    --noconfirm `
    --clean `
    --windowed `
    --name "LightSSHTerminal" `
    --icon "assets\app-icon.ico" `
    --add-data "static;static" `
    --add-data "assets\app-icon.ico;assets" `
    --collect-all webview `
    --hidden-import app.main `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols.http.auto `
    --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import uvicorn.lifespan.on `
    run.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Build completed: $PSScriptRoot\dist\LightSSHTerminal\LightSSHTerminal.exe"
