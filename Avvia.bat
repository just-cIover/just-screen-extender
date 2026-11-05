@echo off
rem Avvia Schermo Esteso: modalità con terminale, lascia aperta questa finestra.
title Schermo Esteso
chcp 65001 >nul
cd /d "%~dp0"
python -u server.py
pause
