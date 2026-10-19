# Firewall per Schermo Esteso (da eseguire come amministratore).
# 1. la rete Wi-Fi attuale diventa Privata (rete di casa)
# 2. la porta 8770 si apre solo sulle reti Private e solo ai dispositivi della stessa rete locale
# 3. via le regole "Python" che aprivano qualsiasi porta a qualsiasi script sulle reti pubbliche
param([string]$Report = "$env:TEMP\schermo-esteso-firewall.txt")
$ErrorActionPreference = 'Continue'
$out = @()

foreach ($p in Get-NetConnectionProfile) {
    if ($p.NetworkCategory -eq 'Public') {
        Set-NetConnectionProfile -InterfaceIndex $p.InterfaceIndex -NetworkCategory Private
        $out += "Rete '$($p.Name)': da Pubblica a Privata"
    }
}

$rules = Get-NetFirewallRule | Where-Object { $_.DisplayName -in @('iPad Schermo (TCP 8770)', 'Schermo Esteso (TCP 8770)') }
if ($rules) { $rules | Remove-NetFirewallRule; $out += "Vecchia regola della porta 8770 tolta" }
New-NetFirewallRule -DisplayName 'Schermo Esteso (TCP 8770)' -Description 'iPad/iPhone come schermi del PC: solo reti private, solo rete locale' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8770 -Profile Private -RemoteAddress LocalSubnet | Out-Null
$out += "Nuova regola: porta 8770, solo reti Private, solo rete locale"

$py = Get-NetFirewallRule -Direction Inbound | Where-Object {
    $_.DisplayName -eq 'Python' -and $_.Profile -match 'Public' -and $_.Action -eq 'Allow'
}
foreach ($r in $py) {
    $prog = ($r | Get-NetFirewallApplicationFilter).Program
    $r | Remove-NetFirewallRule
    $out += "Tolta regola 'Python' (reti pubbliche, tutte le porte): $prog"
}

$out | Out-File -FilePath $Report -Encoding utf8
