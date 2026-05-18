@echo off
setlocal
cd /d "%~dp0"

set SCRIPT=ADB_Control_Center.py

if not exist "%SCRIPT%" (
    echo [ERROR] %SCRIPT% was not found in this folder.
    echo Please keep this launcher in the same folder as %SCRIPT%.
    pause
    exit /b 1
)

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -3 "%SCRIPT%"
    goto :end
)

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python "%SCRIPT%"
    goto :end
)

echo [ERROR] Python 3 was not found.
echo Install Python 3 from python.org, then run this launcher again.
echo If Python is installed, make sure it is available in PATH.
pause
exit /b 1

:end
endlocal
