# Firewall per Schermo Esteso (da eseguire come amministratore).
# 1. la rete Wi-Fi attuale, se è quella di casa ed è Pubblica, diventa Privata
# 2. porta 8770: reti Private solo dalla rete locale; reti Pubbliche solo dall'hotspot di un iPhone
#    (172.20.10.0/28: l'iPhone è sempre 172.20.10.1, chi si collega al suo hotspot 172.20.10.2-14)
# 3. regole anche per i programmi Python, così Windows non chiede "Consentire l'accesso?"
#    (annullando, creerebbe una regola di blocco che fermerebbe anche i dispositivi)
# 4. via le regole "Python" troppo larghe (tutte le porte) e quelle di blocco create dalle richieste
param([string]$Report = "$env:TEMP\schermo-esteso-firewall.txt", [switch]$HomePrivate)
$ErrorActionPreference = 'Continue'
$out = @()
$Hotspot = '172.20.10.0/28'

if ($HomePrivate) {
    foreach ($p in Get-NetConnectionProfile) {
        if ($p.NetworkCategory -eq 'Public' -and $p.IPv4Connectivity -ne 'Disconnected') {
            Set-NetConnectionProfile -InterfaceIndex $p.InterfaceIndex -NetworkCategory Private
            $out += "Rete '$($p.Name)': da Pubblica a Privata"
        }
    }
}

# regole nostre: le tolgo e le rifaccio
Get-NetFirewallRule | Where-Object { $_.DisplayName -like 'Schermo Esteso*' -or $_.DisplayName -eq 'iPad Schermo (TCP 8770)' } |
    Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'Schermo Esteso (TCP 8770)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8770 `
    -Profile Private -RemoteAddress LocalSubnet -Description 'Dispositivi della rete di casa' | Out-Null
New-NetFirewallRule -DisplayName 'Schermo Esteso (TCP 8770, hotspot iPhone)' -Direction Inbound -Action Allow -Protocol TCP `
    -LocalPort 8770 -Profile Public -RemoteAddress $Hotspot -Description "Solo l'hotspot di un iPhone sulle reti pubbliche" | Out-Null
$out += 'Porta 8770: reti private (rete locale) e reti pubbliche solo dall''hotspot iPhone'

# programmi Python che fanno girare il server (app dello Store: il percorso cambia con gli aggiornamenti)
$pys = Get-ChildItem 'C:\Program Files\WindowsApps' -Directory -Filter 'PythonSoftwareFoundation.Python.3.*' -ErrorAction SilentlyContinue |
    ForEach-Object { Get-ChildItem $_.FullName -File | Where-Object { $_.Name -match '^pythonw?3\.\d+\.exe$' } } |
    Select-Object -ExpandProperty FullName
foreach ($py in $pys) {
    $n = Split-Path $py -Leaf
    New-NetFirewallRule -DisplayName "Schermo Esteso ($n, casa)" -Direction Inbound -Action Allow -Program $py -Protocol TCP `
        -LocalPort 8770 -Profile Private -RemoteAddress LocalSubnet | Out-Null
    New-NetFirewallRule -DisplayName "Schermo Esteso ($n, hotspot iPhone)" -Direction Inbound -Action Allow -Program $py `
        -Protocol TCP -LocalPort 8770 -Profile Public -RemoteAddress $Hotspot | Out-Null
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
