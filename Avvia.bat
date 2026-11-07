@echo off
rem Start Screen Extender with a terminal: keep this window open.
title Screen Extender
chcp 65001 >nul
rem the control window opens by itself as soon as the server is up
start "" pythonw "%~dp0avvio.pyw" --window
cd /d "%~dp0pc"
python -u server.py
pause
