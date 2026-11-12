@echo off
REM Developer launcher. Contract: create .\data and export DATABASE_URL before the ORM starts.
setlocal
cd /d "%~dp0"
if not exist data mkdir data
if not exist logs mkdir logs
set "DATABASE_URL=sqlite:///./data/app.db"
if exist "EnergyBaseline.exe" (
  start "" "EnergyBaseline.exe"
) else if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m energybaseline
) else (
  start "" pythonw -m energybaseline
)
endlocal
