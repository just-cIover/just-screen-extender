# First-start setup, started by the program itself as administrator (see pc\installa.py).
# Does only what is missing:
# 1. Virtual Display Driver: downloads it with winget if needed, then installs it (driver\installa-driver.ps1)
# 2. firewall rules for port 8770: private networks, iPhone hotspot, Tailscale (tools\firewall.ps1)
# Everything it does is written to the log file given with -Log (data\setup.log).
param([Parameter(Mandatory)][string]$Log)
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
Start-Transcript -Path $Log -Force | Out-Null
$code = 0

if (Get-PnpDevice -FriendlyName '*Virtual Display*' -ErrorAction SilentlyContinue) {
    Write-Host 'Driver: già installato.'
} else {
    try {
        & (Join-Path $root 'driver\installa-driver.ps1') -NoPause
    } catch {
        Write-Host "Driver non installato: $_"
        $code = 1
    }
}

if (Get-NetFirewallRule -DisplayName 'Screen Extender (TCP 8770)' -ErrorAction SilentlyContinue) {
    Write-Host 'Firewall: regole già presenti.'
} else {
    $report = Join-Path (Split-Path -Parent $Log) 'firewall.txt'
    & (Join-Path $here 'firewall.ps1') -Report $report
    Get-Content $report -ErrorAction SilentlyContinue | Write-Host
    if (-not (Get-NetFirewallRule -DisplayName 'Screen Extender (TCP 8770)' -ErrorAction SilentlyContinue)) { $code += 2 }
}

Stop-Transcript | Out-Null
exit $code
