@echo off
cd /d "%~dp0"
echo ==== VivoPods Controller (VivoPodsCC) builder ====
echo.
echo [1/5] Installing requirements ...
python -m pip install -r requirements.txt
echo.
echo [2/5] Installing PyInstaller ...
python -m pip install -U "pyinstaller>=6.0"
echo.
echo [3/5] Building single-file EXE ... this may take 1-3 minutes
python -m PyInstaller --noconfirm --clean VivoPodsCC.spec
if errorlevel 1 goto fail
echo.
echo [4/5] Copying EXE to project root ...
copy /y "dist\VivoPodsCC.exe" "VivoPodsCC.exe" >nul
if errorlevel 1 goto copyfail
echo.
echo [5/5] Creating desktop shortcut ...
python -m ui.shortcut "VivoPodsCC.exe"
if errorlevel 1 goto shortcutfail
echo.
echo Done. Output: %cd%\VivoPodsCC.exe
echo (dist\VivoPodsCC.exe also kept)
echo Double-click the desktop shortcut "VivoPods Controller" to run.
echo.
pause
exit /b 0

:shortcutfail
echo.
echo [WARN] EXE is ready, but the desktop shortcut could not be created.
echo        You can still run %cd%\VivoPodsCC.exe directly.
echo        Retry manually if needed:
echo        python -m ui.shortcut "VivoPodsCC.exe"
echo.
pause
exit /b 1

:copyfail
echo.
echo [WARN] EXE was built, but copying it to the project root failed.
echo        If VivoPodsCC.exe is running, close it and copy again:
echo        copy /y "dist\VivoPodsCC.exe" "VivoPodsCC.exe"
echo.
pause
exit /b 1

:fail
echo.
echo [ERROR] PyInstaller failed. See the messages above.
echo.
pause
exit /b 1
