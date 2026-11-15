@echo off
setlocal
cd /d "%~dp0"

set "PYEXE="
where python >nul 2>nul
if %errorlevel%==0 set "PYEXE=python"
if not defined PYEXE (
  where py >nul 2>nul
  if %errorlevel%==0 set "PYEXE=py -3"
)
if not defined PYEXE (
  echo [FAIL] Python not found in PATH.
  echo        fix: Install Python 3.9+ and ensure "python" is available. Then run setup.cmd and doctor.cmd.
  exit /b 1
)

echo Running configuration check...
%PYEXE% "%~dp0bridge.py" --check
if errorlevel 1 (
  echo.
  echo Start aborted. Complete setup/configuration, then run start.cmd again.
  exit /b 1
)

echo.
echo Starting bridge...
%PYEXE% "%~dp0bridge.py"
exit /b %errorlevel%
