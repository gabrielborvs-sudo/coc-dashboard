@echo off
cd /d "%~dp0"
python dashboard.py --open
if errorlevel 1 pause
