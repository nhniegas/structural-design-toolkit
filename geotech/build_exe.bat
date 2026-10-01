@echo off
REM ===================================================================
REM  Builds LogSpiralPassive.exe  (double-click this file on Windows)
REM  Needs Python 3.8+ installed (python.org, tick "Add to PATH").
REM  Keep this file in the same folder as logspiral_passive.py
REM ===================================================================
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

echo Installing PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo Could not install PyInstaller. Check your internet connection.
    pause
    exit /b 1
)

echo Building LogSpiralPassive.exe ...
python -m PyInstaller --onefile --console --clean --name LogSpiralPassive logspiral_passive.py
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

copy /y "dist\LogSpiralPassive.exe" "LogSpiralPassive.exe" >nul
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q LogSpiralPassive.spec 2>nul

echo.
echo DONE.  LogSpiralPassive.exe is now in this folder.
echo Copy that single file to anyone - no Python needed on their PC.
pause
