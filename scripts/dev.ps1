<#
.SYNOPSIS
Run the newest podbridge source on this PC, elevated, with the assets of the installed podbridge.

.DESCRIPTION
Fetches the branch from the repo's push remote (read from `git remote -v`), fast-forwards to it,
runs `uv sync`, and starts `python -m podbridge <args>` as admin. PODBRIDGE_INSTALL_DIR points the
source run at the installed app's firmware, tools\wdi-simple.exe, driver state and built UI, so
nothing is rebuilt. A source run never self-updates. Arguments after the options go to podbridge;
the default is `run`.

.EXAMPLE
.\scripts\dev.ps1
.\scripts\dev.ps1 doctor
.\scripts\dev.ps1 -Branch fix/radio run --console
#>
param(
    [string]$Branch = "",
    [string]$Remote = "",
    [string]$InstallDir = "",
    [switch]$NoPull,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$PodbridgeArgs
)

$ErrorActionPreference = "Stop"
$AppId = "{1D046C64-8CBA-456B-B0D9-752F8A4D01FF}"  # AppId in installer/podbridge.iss
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $PodbridgeArgs) { $PodbridgeArgs = @("run") }

function Git {
    $out = & git -C $Repo @args
    if ($LASTEXITCODE -ne 0) { throw "git $($args -join ' ') failed with exit code $LASTEXITCODE" }
    $out
}

function Find-PushRemote {
    $push = @(Git remote -v | Where-Object { $_ -match '\(push\)$' } | ForEach-Object { ($_ -split '\s+')[0] } | Select-Object -Unique)
    if ($push.Count -eq 1) { return $push[0] }
    if ($push -contains "origin") { return "origin" }
    throw "cannot pick a push remote from 'git remote -v' ($($push -join ', ')); pass -Remote <name>"
}

function Find-InstallDir {
    $key = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\${AppId}_is1"
    $location = (Get-ItemProperty -Path $key -Name InstallLocation -ErrorAction SilentlyContinue).InstallLocation
    if (-not $location) { throw "podbridge is not installed (no $key); install podbridge-setup.exe once, or pass -InstallDir" }
    $location.TrimEnd('\')
}

function Quote([string]$s) { "'" + $s.Replace("'", "''") + "'" }

if (-not $NoPull) {
    $remote = if ($Remote) { $Remote } else { Find-PushRemote }
    if (Git status --porcelain) { throw "$Repo has local changes; commit or discard them first, or pass -NoPull" }
    $current = (Git branch --show-current)
    $target = if ($Branch) { $Branch } else { $current }
    if (-not $target) { throw "HEAD is detached; pass -Branch <name>" }
    Write-Host "dev: fetching $remote"
    Git fetch $remote | Out-Null
    if ($target -ne $current) {
        $local = & git -C $Repo rev-parse --verify --quiet "refs/heads/$target"
        if ($local) { Git checkout $target | Out-Null } else { Git checkout -b $target --track "$remote/$target" | Out-Null }
    }
    Git merge --ff-only "$remote/$target" | Out-Null
    Write-Host "dev: at $target $(Git rev-parse --short HEAD)"
}

Push-Location $Repo
try {
    uv sync
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }

$install = if ($InstallDir) { $InstallDir.TrimEnd('\') } else { Find-InstallDir }
foreach ($part in @("tools\wdi-simple.exe", "firmware")) {
    if (-not (Test-Path (Join-Path $install $part))) { throw "$install has no $part; reinstall podbridge or pass -InstallDir" }
}
$running = @(Get-Process -Name podbridge, podbridge-cli -ErrorAction SilentlyContinue)
if ($running) { throw "the installed podbridge is running (pid $($running.Id -join ', ')); close it first, both cannot own the radio" }

$python = Join-Path $Repo ".venv\Scripts\python.exe"
$quotedArgs = ($PodbridgeArgs | ForEach-Object { Quote $_ }) -join " "
$command = "`$env:PODBRIDGE_INSTALL_DIR = $(Quote $install); Set-Location $(Quote $Repo); & $(Quote $python) -m podbridge $quotedArgs"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host "dev: podbridge $($PodbridgeArgs -join ' ') with assets from $install"
if ($admin) {
    Invoke-Expression $command
    exit $LASTEXITCODE
}
# An elevated child does not inherit this session's environment, so the command sets it itself.
$elevated = "$command; Read-Host ('podbridge exited with code ' + `$LASTEXITCODE + '. Press Enter to close')"
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($elevated))
Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded
