@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo         HYDRA STANDALONE APP AND SETUP.EXE BUILDER
echo ============================================================
echo.

cd /d "%~dp0"

REM 1. Verify Python & PyInstaller
echo [1/3] Checking PyInstaller...
python -m PyInstaller --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] PyInstaller is not available. Installing...
    python -m pip install pyinstaller
)

REM 2. Run PyInstaller Freeze
echo [2/3] Freezing HYDRA into standalone executable bundle...
python -m PyInstaller hydra.spec --noconfirm --clean
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] PyInstaller freeze failed!
    exit /b 1
)

REM 3. Locate ISCC.exe (Inno Setup Compiler)
echo [3/3] Locating Inno Setup Compiler...
set "ISCC_PATH="
if exist "C:\Users\%USERNAME%\AppData\Local\Programs\Inno Setup 6\ISCC.exe" (
    set "ISCC_PATH=C:\Users\%USERNAME%\AppData\Local\Programs\Inno Setup 6\ISCC.exe"
) else if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    set "ISCC_PATH=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    set "ISCC_PATH=C:\Program Files\Inno Setup 6\ISCC.exe"
) else (
    for /f "tokens=*" %%i in ('where ISCC.exe 2^>nul') do set "ISCC_PATH=%%i"
)

if "%ISCC_PATH%"=="" (
    echo [ERROR] Could not find ISCC.exe. Please ensure Inno Setup is installed.
    exit /b 1
)

echo Compiling setup.exe with: "%ISCC_PATH%"
"%ISCC_PATH%" HydraSetup.iss

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Inno Setup compilation failed!
    exit /b 1
)

REM Copy setup.exe to project root for easy access
if exist "dist\installer\setup.exe" (
    copy /y "dist\installer\setup.exe" "setup.exe" >nul
    copy /y "dist\installer\setup.exe" "Hydra-Setup.exe" >nul
    echo.
    echo ============================================================
    echo SUCCESS! Standalone setup installer generated:
    echo   - Output: %~dp0setup.exe
    echo   - Copy:   %~dp0Hydra-Setup.exe
    echo   - Target: %~dp0dist\installer\setup.exe
    echo ============================================================
)

endlocal
