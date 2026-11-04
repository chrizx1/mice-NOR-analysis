@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0_packages"
set "NOR_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%NOR_PYTHON%" goto launch
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 analyzer.py
  if errorlevel 1 pause
  exit /b
)
python analyzer.py
if errorlevel 1 pause
exit /b
:launch
"%NOR_PYTHON%" analyzer.py
if errorlevel 1 pause
