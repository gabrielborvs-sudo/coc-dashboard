@echo off
cd /d "%~dp0"
rem Local preview goes through the RoyaleAPI proxy, same as the live site,
rem so the key file must contain the proxy key (allowed IP 45.79.218.79).
set COC_API_BASE=https://cocproxy.royaleapi.dev/v1
python dashboard.py --open
if errorlevel 1 pause
