# Firewall for Schermo Esteso (run as administrator).
# 1. the current Wi-Fi network, if it's the home one and set to Public, becomes Private
# 2. port 8770: Private networks only from the local network; Public networks only from an iPhone hotspot
#    (172.20.10.0/28: the iPhone is always 172.20.10.1, whoever joins its hotspot 172.20.10.2-14)
# 3. rules for the Python programs too, so Windows doesn't ask "Allow access?"
#    (cancelling would create a block rule that would stop the devices too)
# 4. remove the "Python" rules that are too broad (all ports) and the block rules created by those prompts
param([string]$Report = "$env:TEMP\schermo-esteso-firewall.txt", [switch]$HomePrivate)
$ErrorActionPreference = 'Continue'
$out = @()
$Hotspot = '172.20.10.0/28'
$Tailscale = '100.64.0.0/10'  # only your own devices connected to the same Tailscale account

if ($HomePrivate) {
    foreach ($p in Get-NetConnectionProfile) {
        if ($p.NetworkCategory -eq 'Public' -and $p.IPv4Connectivity -ne 'Disconnected') {
            Set-NetConnectionProfile -InterfaceIndex $p.InterfaceIndex -NetworkCategory Private
            $out += "Rete '$($p.Name)': da Pubblica a Privata"
        }
    }
}

# our rules: remove them and create them again
Get-NetFirewallRule | Where-Object { $_.DisplayName -like 'Schermo Esteso*' -or $_.DisplayName -eq 'iPad Schermo (TCP 8770)' } |
    Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'Schermo Esteso (TCP 8770)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8770 `
    -Profile Private -RemoteAddress LocalSubnet -Description 'Dispositivi della rete di casa' | Out-Null
New-NetFirewallRule -DisplayName 'Schermo Esteso (TCP 8770, hotspot iPhone)' -Direction Inbound -Action Allow -Protocol TCP `
    -LocalPort 8770 -Profile Public -RemoteAddress $Hotspot -Description "Solo l'hotspot di un iPhone sulle reti pubbliche" | Out-Null
New-NetFirewallRule -DisplayName 'Schermo Esteso (TCP 8770, Tailscale)' -Direction Inbound -Action Allow -Protocol TCP `
    -LocalPort 8770 -Profile Any -RemoteAddress $Tailscale -Description 'I propri dispositivi via Tailscale, da qualsiasi rete' | Out-Null
$out += 'Porta 8770: reti private (rete locale), hotspot iPhone, dispositivi Tailscale'

# Python programs running the server (Store app: the path changes with updates)
$pys = Get-ChildItem 'C:\Program Files\WindowsApps' -Directory -Filter 'PythonSoftwareFoundation.Python.3.*' -ErrorAction SilentlyContinue |
    ForEach-Object { Get-ChildItem $_.FullName -File | Where-Object { $_.Name -match '^pythonw?3\.\d+\.exe$' } } |
    Select-Object -ExpandProperty FullName
foreach ($py in $pys) {
    $n = Split-Path $py -Leaf
    New-NetFirewallRule -DisplayName "Schermo Esteso ($n, casa)" -Direction Inbound -Action Allow -Program $py -Protocol TCP `
        -LocalPort 8770 -Profile Private -RemoteAddress LocalSubnet | Out-Null
    New-NetFirewallRule -DisplayName "Schermo Esteso ($n, hotspot iPhone)" -Direction Inbound -Action Allow -Program $py `
        -Protocol TCP -LocalPort 8770 -Profile Public -RemoteAddress $Hotspot | Out-Null
    New-NetFirewallRule -DisplayName "Schermo Esteso ($n, Tailscale)" -Direction Inbound -Action Allow -Program $py `
        -Protocol TCP -LocalPort 8770 -Profile Any -RemoteAddress $Tailscale | Out-Null
    $out += "Regole per $py"
}

foreach ($r in Get-NetFirewallRule -Direction Inbound | Where-Object { $_.DisplayName -eq 'Python' }) {
    $prog = ($r | Get-NetFirewallApplicationFilter).Program
    $port = ($r | Get-NetFirewallPortFilter).LocalPort
    if ($r.Action -eq 'Block' -or ($port -eq 'Any' -and $r.Profile -match 'Public')) {
        $r | Remove-NetFirewallRule
        $out += "Tolta regola 'Python' ($($r.Action), $($r.Profile), porte $port): $prog"
    }
}
$out | Out-File -FilePath $Report -Encoding utf8
