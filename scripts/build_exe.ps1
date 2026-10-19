param([string]$DistPath = "dist")

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

uv sync
if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE" }

uv run pyinstaller --noconfirm --clean --onedir --console --uac-admin `
    --name podbridge `
    --distpath $DistPath `
    --paths src `
    --collect-submodules podbridge `
    --collect-all bumble `
    --collect-all av `
    --collect-all libusb_package `
    --collect-all pyaudiowpatch `
    --collect-submodules pycaw `
    --collect-submodules comtypes `
    src/podbridge/__main__.py
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed with exit code $LASTEXITCODE" }

$exe = Join-Path $DistPath "podbridge/podbridge.exe"
& $exe --version
if ($LASTEXITCODE -ne 0) { throw "$exe --version failed with exit code $LASTEXITCODE" }
