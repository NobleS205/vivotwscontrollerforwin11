@echo off
cd /d "%~dp0"
echo ==== VivoPods Controller (VivoPodsCC) builder ====
echo.
echo [1/3] Installing requirements ...
python -m pip install -r requirements.txt
echo.
echo [2/3] Installing PyInstaller ...
python -m pip install -U "pyinstaller>=6.0"
echo.
echo [3/3] Building single-file EXE ... this may take 1-3 minutes
python -m PyInstaller --noconfirm --clean VivoPodsCC.spec
echo.
echo Output: %cd%\dist\VivoPodsCC.exe
echo.
pause
