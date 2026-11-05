@echo off
rem Installa lo schermo virtuale per l'iPad (chiede il permesso di amministratore).
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"%~dp0installa-driver.ps1\"'"
