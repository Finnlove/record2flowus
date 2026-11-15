@echo off
cd /d "%~dp0"
echo Starting Hex-FlowUs Voice Note Bridge...
python "%~dp0bridge.py"
pause
