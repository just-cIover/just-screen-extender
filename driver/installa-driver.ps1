# Installs the Virtual Display Driver (virtual screen for the iPad).
# Must run as administrator: the program starts it that way on its first start (tools\setup.ps1),
# and so does "Installa driver.bat" when run by hand.
param([switch]$NoPause)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-Package {
    foreach ($base in "$env:LOCALAPPDATA\Microsoft\WinGet\Packages", 'C:\Users\*\AppData\Local\Microsoft\WinGet\Packages') {
        $p = Get-ChildItem $base -Directory -Filter 'VirtualDrivers.Virtual-Display-Driver*' -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($p) { return $p }
    }
}

$pkg = Find-Package
if (-not $pkg) {
    # not downloaded yet: winget gets it (official package of the VirtualDrivers project)
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'winget non trovato: installa il pacchetto VirtualDrivers.Virtual-Display-Driver a mano.'
    }
    Write-Host 'Scarico il Virtual Display Driver con winget...'
    winget install --id VirtualDrivers.Virtual-Display-Driver -e --silent --accept-source-agreements --accept-package-agreements
    $pkg = Find-Package
}
if (-not $pkg) { throw 'Pacchetto VirtualDrivers.Virtual-Display-Driver non trovato: installalo con winget.' }
$drv = Join-Path $pkg.FullName 'SignedDrivers\x86\VDD'
$devcon = Join-Path $pkg.FullName 'Dependencies\devcon.exe'

# The driver reads its configuration from C:\VirtualDisplayDriver
New-Item -ItemType Directory -Force 'C:\VirtualDisplayDriver' | Out-Null
Copy-Item (Join-Path $here 'vdd_settings.xml') 'C:\VirtualDisplayDriver\vdd_settings.xml' -Force

if (Get-PnpDevice -FriendlyName '*Virtual Display*' -ErrorAction SilentlyContinue) {
    Write-Host 'Driver già presente: lo riavvio per applicare le risoluzioni.'
    & $devcon restart 'Root\MttVDD'
} else {
    & $devcon install (Join-Path $drv 'MttVDD.inf') 'Root\MttVDD'
}
Write-Host "Driver: fatto (codice $LASTEXITCODE)."
# the firewall is set up by tools\firewall.ps1 (private networks, iPhone hotspot and Tailscale only)
if (-not $NoPause) { Start-Sleep 3 }
