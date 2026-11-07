# iPhone connected with the cable and Personal Hotspot on: the PC sees it as an extra network adapter.
# Here I make it the last choice for Internet (which stays on the home Wi-Fi): the cable is only for
# Schermo Esteso, without using the iPhone's data. Run as administrator (once:
# Windows remembers the setting for that adapter).
param([string]$Report = "$env:TEMP\schermo-esteso-usb.txt")
$out = @()
$adapters = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object {
    $_.InterfaceDescription -match 'Apple Mobile Device Ethernet|iPhone' -or
    (Get-NetIPAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress -like '172.20.10.*'
}
if (-not $adapters) { $out += "Nessuna scheda dell'iPhone trovata: cavo collegato e Hotspot personale attivo?" }
foreach ($a in $adapters) {
    Set-NetIPInterface -InterfaceIndex $a.ifIndex -InterfaceMetric 9000 -ErrorAction SilentlyContinue
    $ip = (Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress
    $out += "Scheda '$($a.Name)' ($($a.InterfaceDescription), $ip): ultima scelta per Internet"
}
$out += "Internet passa da: " + ((Find-NetRoute -RemoteIPAddress 1.1.1.1 -ErrorAction SilentlyContinue |
    Select-Object -First 1).InterfaceAlias)
$out | Out-File -FilePath $Report -Encoding utf8
