@echo off
:: ============================================================
::  GenericAgent - Windows 11 One-Click Launcher
::  Double-click start.bat to run.
:: ============================================================

title GenericAgent v0.8.1
cd /d "%~dp0"

echo.
echo ================================================================
echo    GenericAgent Web UI  v0.8.1
echo ================================================================
echo.

:: ---- Step 1: Find Python 3.10+ ----
set PYTHON_CMD=

:: Method 1: py launcher (most reliable on Windows 11)
where py >nul 2>&1
if not errorlevel 1 goto :try_py
goto :try_python3

:try_py
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto :try_python3
set PYTHON_CMD=py
goto :python_ok

:try_python3
where python3 >nul 2>&1
if errorlevel 1 goto :try_python
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto :try_python
set PYTHON_CMD=python3
goto :python_ok

:try_python
where python >nul 2>&1
if errorlevel 1 goto :python_missing
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto :python_missing
set PYTHON_CMD=python
goto :python_ok

:python_missing
echo [WARN] Python 3.10+ not found. Trying auto-install...
echo.

:: Try winget (Windows 11 has it by default)
where winget >nul 2>&1
if errorlevel 1 goto :download_python
echo Installing Python 3.12 via winget...
winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
if errorlevel 1 goto :download_python
echo.
echo Done! Python installed successfully.
echo Please close this window and double-click start.bat again.
pause
exit /b 0

:download_python
echo Downloading Python 3.12 from python.org...
set "INSTALLER=%TEMP%\python-3.12-installer.exe"
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile '%INSTALLER%'"
if not exist "%INSTALLER%" goto :python_manual
echo Installing Python silently (this may take 1-2 minutes)...
start /wait "" "%INSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
del "%INSTALLER%" 2>nul
echo.
echo Done! Python installed.
echo Please close this window and double-click start.bat again.
pause
exit /b 0

:python_manual
echo.
echo [ERROR] Cannot install Python automatically.
echo.
echo Please install it manually:
echo   1. Open https://www.python.org/downloads/
echo   2. Download Python 3.12 for Windows
echo   3. Run the installer
echo   4. CHECK "Add Python to PATH" at the bottom of the installer!
echo   5. After install, double-click start.bat again
echo.
pause
exit /b 1

:: ---- Python found ----
:python_ok
echo [OK] Python: %PYTHON_CMD%
%PYTHON_CMD% --version
echo.

:: ---- Step 2: Create virtual environment ----
if exist ".venv\Scripts\python.exe" goto :venv_ok
echo [SETUP] Creating virtual environment...
%PYTHON_CMD% -m venv .venv
if not errorlevel 1 goto :venv_ok
echo.
echo [ERROR] Failed to create .venv
echo Try deleting the .venv folder and re-run.
pause
exit /b 1

:venv_ok
set "VENV_PY=.venv\Scripts\python.exe"
set "VENV_PIP=.venv\Scripts\pip.exe"

:: ---- Step 3: Install dependencies ----
if exist ".venv\.deps_installed" goto :deps_ok
echo [SETUP] Installing dependencies (needs internet, ~1 min)...
"%VENV_PIP%" install --upgrade pip --quiet
"%VENV_PIP%" install flask requests beautifulsoup4 bottle simple-websocket-server pystray Pillow lark-oapi dingtalk-stream rapidocr-onnxruntime --quiet
if not errorlevel 1 goto :deps_done
echo.
echo [ERROR] Failed to install dependencies.
echo Please check your internet connection and re-run start.bat
pause
exit /b 1

:deps_done
echo . > ".venv\.deps_installed"
echo [OK] Dependencies installed.
echo.

:deps_ok

:: ---- Step 4: Check mykey.py ----
if exist "mykey.py" goto :launch

if not exist "mykey_template.py" goto :no_template
copy "mykey_template.py" "mykey.py" >nul
echo [INFO] mykey.py created from template.
echo   You can configure API Key in the web UI (Settings page).
echo.

:no_template
:: Continue anyway - user can configure via web UI

:: ---- Step 5: Start server ----
:launch
echo ================================================================
echo   Starting Web UI...
echo.
echo   Browser will open:  http://localhost:18600
echo   Press Ctrl+C or close this window to stop.
echo ================================================================
echo.

:: Open browser
start "" http://localhost:18600 2>nul

cd frontends
"..\%VENV_PY%" web_server.py --port 18600

echo.
echo Server stopped. Press any key to exit.
pause >nul
