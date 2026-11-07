# Temperatures for Screen Extender: LibreHardwareMonitor starts at sign-in, with the rights needed
# to read the sensors, minimized next to the clock. Run as administrator.
param([string]$Exe, [string]$Report = "$env:TEMP\screen-extender-sensori.txt")
$out = @()
$dir = Split-Path $Exe
# settings: starts minimized, goes to the notification area, closing the window minimizes it
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
# the task of earlier versions had the Italian name
Unregister-ScheduledTask -TaskName 'Schermo Esteso - sensori' -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName 'Screen Extender - sensori' -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'LibreHardwareMonitor per le temperature nella finestra di Screen Extender' -Force | Out-Null
$out += "Attività all'accesso creata"
Start-ScheduledTask -TaskName 'Screen Extender - sensori'
$out += "Avviato"
$out | Out-File -FilePath $Report -Encoding utf8
