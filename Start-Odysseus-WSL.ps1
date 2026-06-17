#Requires -Version 5.1
<#
  Start Odysseus inside WSL from Windows.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\Start-Odysseus-WSL.ps1
    powershell -ExecutionPolicy Bypass -File .\Start-Odysseus-WSL.ps1 -Mode docker
    powershell -ExecutionPolicy Bypass -File .\Start-Odysseus-WSL.ps1 -Distro Ubuntu -WslPath /home/me/odysseus
    powershell -ExecutionPolicy Bypass -File .\Start-Odysseus-WSL.ps1 -Foreground

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
    [switch]$NoBrowser,
    [switch]$Foreground,
    [int]$StartupTimeoutSeconds = 120
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
$runMode = if ($Foreground) { "--foreground" } else { "--detach" }
$bashCommand = "cd $quotedRepo && bash $scriptPath --mode $quotedMode --bind $quotedBind --port $Port $runMode"

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

if ($Foreground) {
    Start-Process -FilePath "wsl.exe" -ArgumentList $launchArgs -WindowStyle Normal
} else {
    & wsl.exe @launchArgs
    if ($LASTEXITCODE -ne 0) {
        Fail "WSL startup failed. Check logs/odysseus-wsl.log from WSL for details."
    }
}

if (-not $NoBrowser) {
    if (-not $Foreground) {
        $url = "http://localhost:{0}" -f $Port
        $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
        $ready = $false
        Write-Host ""
        Write-Host "Waiting for Odysseus to accept connections..." -ForegroundColor Cyan
        while ((Get-Date) -lt $deadline) {
            try {
                $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 3
                if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                    $ready = $true
                    break
                }
            } catch {
                Start-Sleep -Seconds 2
            }
        }
        if (-not $ready) {
            Write-Host ("Odysseus is still starting. Open {0} in a minute, or check WSL logs/odysseus-wsl.log." -f $url) -ForegroundColor Yellow
        }
    } else {
        Start-Sleep -Seconds 4
    }
    Start-Process ("http://localhost:{0}" -f $Port)
}
