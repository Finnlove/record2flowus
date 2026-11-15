@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python "%~dp0scripts\doctor.py" %*
  exit /b %errorlevel%
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0scripts\doctor.py" %*
  exit /b %errorlevel%
)
echo [FAIL] python: Python launcher not found in PATH.
echo        fix: Install Python 3.9+ from https://www.python.org/ and ensure "python" is available, then re-run doctor.cmd.
exit /b 1
