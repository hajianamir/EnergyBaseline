@echo off
REM Stops a running EnergyBaseline instance.
taskkill /IM EnergyBaseline.exe /F >nul 2>nul
wmic process where "name='pythonw.exe' and commandline like '%%energybaseline%%'" call terminate >nul 2>nul
echo EnergyBaseline stopped.
