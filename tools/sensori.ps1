# Temperature per Schermo Esteso: LibreHardwareMonitor parte all'accesso, con i permessi che servono
# per leggere i sensori, ridotto vicino all'orologio. Da eseguire come amministratore.
param([string]$Exe, [string]$Report = "$env:TEMP\schermo-esteso-sensori.txt")
$out = @()
$dir = Split-Path $Exe
# impostazioni: parte ridotto, va nell'area di notifica, chiudere la finestra lo riduce
$cfg = Join-Path $dir 'LibreHardwareMonitor.config'
@'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <appSettings>
    <add key="startMinMenuItem" value="true" />
    <add key="minTrayMenuItem" value="true" />
    <add key="minCloseMenuItem" value="true" />
  </appSettings>
</configuration>
'@ | Out-File -FilePath $cfg -Encoding utf8
$out += "Impostazioni scritte: $cfg"

$action = New-ScheduledTaskAction -Execute $Exe -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName 'Schermo Esteso - sensori' -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'LibreHardwareMonitor per le temperature nella finestra di Schermo Esteso' -Force | Out-Null
$out += "Attività all'accesso creata"
Start-ScheduledTask -TaskName 'Schermo Esteso - sensori'
$out += "Avviato"
$out | Out-File -FilePath $Report -Encoding utf8
