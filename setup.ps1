<#
.SYNOPSIS
    Google Antigravity ACP Setup & Manager for Windows PowerShell
.DESCRIPTION
    Dual-mode manager for Google Antigravity ACP Server, OAuth authentication,
    transparent tool bridge (Read slice & Edit diff), and Paseo integration on Windows.
.EXAMPLE
    .\setup.ps1 paseo
    .\setup.ps1 status
    .\setup.ps1 auth
    .\setup.ps1 install
    .\setup.ps1 setup
#>

param(
    [Parameter(Position=0)]
    [string]$Command = "setup",

    [Parameter(Position=1)]
    [string]$Target = "windows",

    [switch]$Force,
    [switch]$SkipAgyCheck
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Locate Python 3.10+
$PythonCmd = $null
$CandidatePythons = @(
    "python",
    "py",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "C:\Program Files\Python311\python.exe",
    "C:\Program Files\Python312\python.exe",
    "C:\Program Files\Python310\python.exe"
)

foreach ($c in $CandidatePythons) {
    if (Get-Command $c -ErrorAction SilentlyContinue) {
        $PythonCmd = $c
        break
    }
    if (Test-Path $c) {
        $PythonCmd = $c
        break
    }
}

if (-not $PythonCmd) {
    Write-Error "Python 3 is required but not found. Please install Python 3.10+ from python.org or Microsoft Store."
    exit 1
}

$ScriptPath = Join-Path $ScriptDir "agy_acp.py"
$ArgsList = @($ScriptPath, $Command)

if ($Target) {
    $ArgsList += $Target
}
if ($Force) {
    $ArgsList += "--force"
}
if ($SkipAgyCheck) {
    $ArgsList += "--skip-agy-check"
}

& $PythonCmd $ArgsList
exit $LASTEXITCODE
