# Installa il Virtual Display Driver (schermo virtuale per l'iPad).
# Va eseguito come amministratore: "Installa driver.bat" lo avvia già così.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pkg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Directory -Filter 'VirtualDrivers.Virtual-Display-Driver*' |
    Select-Object -First 1
if (-not $pkg) {
    $pkg = Get-ChildItem 'C:\Users\*\AppData\Local\Microsoft\WinGet\Packages' -Directory -Filter 'VirtualDrivers.Virtual-Display-Driver*' |
        Select-Object -First 1
}
if (-not $pkg) { throw 'Pacchetto VirtualDrivers.Virtual-Display-Driver non trovato: installalo con winget.' }
$drv = Join-Path $pkg.FullName 'SignedDrivers\x86\VDD'
$devcon = Join-Path $pkg.FullName 'Dependencies\devcon.exe'

# Il driver legge la configurazione da C:\VirtualDisplayDriver
New-Item -ItemType Directory -Force 'C:\VirtualDisplayDriver' | Out-Null
Copy-Item (Join-Path $here 'vdd_settings.xml') 'C:\VirtualDisplayDriver\vdd_settings.xml' -Force

if (Get-PnpDevice -FriendlyName '*Virtual Display*' -ErrorAction SilentlyContinue) {
    Write-Host 'Driver già presente: lo riavvio per applicare le risoluzioni.'
    & $devcon restart 'Root\MttVDD'
} else {
    & $devcon install (Join-Path $drv 'MttVDD.inf') 'Root\MttVDD'
}
Write-Host "Driver: fatto (codice $LASTEXITCODE)."

# Il firewall deve lasciar passare l'iPad sulla porta dell'app (anche con la rete impostata come "Pubblica")
if (-not (Get-NetFirewallRule -DisplayName 'iPad Schermo (TCP 8770)' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName 'iPad Schermo (TCP 8770)' -Direction Inbound -Protocol TCP -LocalPort 8770 `
        -Action Allow -Profile Any -RemoteAddress LocalSubnet | Out-Null
    Write-Host 'Regola firewall aggiunta.'
}
Start-Sleep 3
