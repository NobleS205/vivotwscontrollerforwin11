@echo off
cd /d "%~dp0"
echo Checking PySide6 ...
python -c "import PySide6" 2>nul
if errorlevel 1 python -m pip install -r requirements.txt
echo Starting VivoPods Controller ...
start "" pythonw app.py
