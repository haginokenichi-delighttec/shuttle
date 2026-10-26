@echo off
rem Fetch travel times from Google Routes API into data\sample_routing\travel_matrix.csv
rem   cmd       : set GOOGLE_MAPS_API_KEY=YOUR_KEY
rem   PowerShell: $env:GOOGLE_MAPS_API_KEY = "YOUR_KEY"
rem   fetch_matrix.bat --dry-run   (count only, no API call)
rem   fetch_matrix.bat             (fetch; later runs fetch only missing pairs)
rem   fetch_matrix.bat --refresh   (fetch everything again)
chcp 65001 > nul
cd /d %~dp0
.venv\Scripts\python tools\fetch_google_matrix.py --data data\sample_routing %*
pause
