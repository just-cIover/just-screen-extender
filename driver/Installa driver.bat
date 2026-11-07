@echo off
rem Installs the virtual screen for the iPad (asks for administrator permission).
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"%~dp0installa-driver.ps1\"'"
