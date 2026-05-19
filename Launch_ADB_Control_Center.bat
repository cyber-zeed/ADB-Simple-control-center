@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 ADB_Control_Center.py
    goto :eof
)
where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python ADB_Control_Center.py
    goto :eof
)
echo Python 3 was not found. Install Python 3 from the Installers tab or from python.org.
pause
