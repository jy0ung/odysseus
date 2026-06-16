#Requires -Version 5.1
<#
  Start Odysseus inside WSL from Windows.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\Start-Odysseus-WSL.ps1
    powershell -ExecutionPolicy Bypass -File .\Start-Odysseus-WSL.ps1 -Mode docker
    powershell -ExecutionPolicy Bypass -File .\Start-Odysseus-WSL.ps1 -Distro Ubuntu -WslPath /home/me/odysseus

  Mode "auto" uses an existing Docker Compose deployment when one is already
  present, otherwise starts the app from the WSL virtualenv.
#>
param(
    [ValidateSet("auto", "native", "docker")]
    [string]$Mode = "auto",
    [string]$Distro = "",
    [string]$WslPath = "",
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$repoRoot = $PSScriptRoot

function Fail($message) {
    Write-Host ""
    Write-Host ("ERROR: " + $message) -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

function Quote-Bash($value) {
    $single = [string][char]39
    $escapedSingle = $single + "\" + $single + $single
    return $single + ([string]$value).Replace($single, $escapedSingle) + $single
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Fail "wsl.exe was not found. Install WSL first, then run this launcher again."
}

$wslArgs = @()
if ($Distro.Trim()) {
    $wslArgs += @("-d", $Distro.Trim())
}

if (-not $WslPath.Trim()) {
    try {
        $WslPath = (& wsl.exe @wslArgs -e wslpath -a $repoRoot 2>$null).Trim()
    } catch {
        Fail "Could not convert this Windows path to a WSL path. Pass -WslPath /path/to/odysseus."
    }
}

if (-not $WslPath.Trim()) {
    Fail "Could not resolve the WSL project path. Pass -WslPath /path/to/odysseus."
}

$scriptPath = Quote-Bash (($WslPath.TrimEnd("/") + "/scripts/start-wsl.sh") -replace "\\", "/")
$quotedRepo = Quote-Bash $WslPath
$quotedBind = Quote-Bash $BindHost
$quotedMode = Quote-Bash $Mode
$bashCommand = "cd $quotedRepo && bash $scriptPath --mode $quotedMode --bind $quotedBind --port $Port"

Write-Host ""
Write-Host "Starting Odysseus in WSL..." -ForegroundColor Cyan
Write-Host ("WSL path: " + $WslPath)
Write-Host ("URL: http://localhost:{0}" -f $Port)
Write-Host ""

$launchArgs = @()
if ($Distro.Trim()) {
    $launchArgs += @("-d", $Distro.Trim())
}
$launchArgs += @("-e", "bash", "-lc", $bashCommand)

Start-Process -FilePath "wsl.exe" -ArgumentList $launchArgs -WindowStyle Normal

if (-not $NoBrowser) {
    Start-Sleep -Seconds 4
    Start-Process ("http://localhost:{0}" -f $Port)
}
