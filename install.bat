@echo off
REM Developer / build helper. End users never need this: they double-click EnergyBaseline.exe.
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py -3.12") || (set "PY=python")
if not exist ".venv" %PY% -m venv .venv
call .venv\Scripts\activate.bat
python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 'Python 3.12 is required; recreate .venv with Python 3.12')"
if errorlevel 1 exit /b 1
python -m pip install --upgrade pip
pip install --require-hashes -r requirements.lock
if errorlevel 1 exit /b 1
if not exist data mkdir data
if not exist logs mkdir logs
set "DATABASE_URL=sqlite:///./data/app.db"
python -m alembic upgrade head
python scripts\make_icon.py
python scripts\build_exe.py
echo.
echo Done. Double-click EnergyBaseline.exe (or the EnergyBaseline.lnk shortcut).
endlocal
