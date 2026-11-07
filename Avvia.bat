@echo off
rem Start Schermo Esteso with a terminal: keep this window open.
title Schermo Esteso
chcp 65001 >nul
cd /d "%~dp0pc"
python -u server.py
pause
