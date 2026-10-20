param([string]$DistPath = "dist")

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

uv sync
if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE" }

npm --prefix ui ci
if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE" }
npm --prefix ui run build
if ($LASTEXITCODE -ne 0) { throw "npm run build failed with exit code $LASTEXITCODE" }

$common = @(
    "--noconfirm", "--clean", "--onedir", "--uac-admin",
    "--distpath", $DistPath,
    "--paths", "src",
    "--add-data", "src\podbridge\web;podbridge\web",
    "--collect-submodules", "podbridge",
    "--collect-all", "bumble",
    "--collect-all", "av",
    "--collect-all", "libusb_package",
    "--collect-all", "pyaudiowpatch",
    "--collect-all", "webview",
    "--collect-submodules", "pycaw",
    "--collect-submodules", "comtypes",
    "src/podbridge/__main__.py"
)

uv run pyinstaller @common --windowed --name podbridge
if ($LASTEXITCODE -ne 0) { throw "pyinstaller (podbridge) failed with exit code $LASTEXITCODE" }
uv run pyinstaller @common --console --name podbridge-cli
if ($LASTEXITCODE -ne 0) { throw "pyinstaller (podbridge-cli) failed with exit code $LASTEXITCODE" }

Copy-Item (Join-Path $DistPath "podbridge-cli/podbridge-cli.exe") (Join-Path $DistPath "podbridge") -Force
Remove-Item (Join-Path $DistPath "podbridge-cli") -Recurse -Force

$exe = Join-Path $DistPath "podbridge/podbridge-cli.exe"
& $exe --version
if ($LASTEXITCODE -ne 0) { throw "$exe --version failed with exit code $LASTEXITCODE" }
