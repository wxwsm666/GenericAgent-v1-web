@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
:: ============================================================
::  GenericAgent - Windows Launcher
::  Double-click start.bat to run.
:: ============================================================

title GenericAgent
cd /d "%~dp0"

echo.
echo ================================================================
echo    GenericAgent Web UI
echo ================================================================
echo.

:: ---- Step 1: Find Python 3.10+ ----
set PYTHON_CMD=

:: Method 1: py launcher (most reliable on Windows)
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set PYTHON_CMD=py
        goto :python_ok
    )
)

:: Method 2: python3
where python3 >nul 2>&1
if not errorlevel 1 (
    python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set PYTHON_CMD=python3
        goto :python_ok
    )
)

:: Method 3: python
where python >nul 2>&1
if not errorlevel 1 (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set PYTHON_CMD=python
        goto :python_ok
    )
)

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
:: Read version dynamically from version.json
for /f "delims=" %%v in ('%PYTHON_CMD% -c "import json; print(json.load(open('version.json')).get('version','unknown'))"') do set GA_VERSION=%%v
echo    GenericAgent Web UI  v%GA_VERSION%
echo.
title GenericAgent v%GA_VERSION%

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
if exist "mykey.py" goto :check_update_source

if not exist "mykey_template.py" goto :no_template
copy "mykey_template.py" "mykey.py" >nul
echo [INFO] mykey.py created from template.
echo   You can configure API Key in the web UI (Settings page).
echo.

:check_update_source
:: Auto-add update_source for existing users upgrading from old versions
findstr /C:"update_source =" mykey.py >nul 2>&1
if errorlevel 1 (
    echo [INFO] Old mykey.py detected, adding update_source...
    echo. >> mykey.py
    echo # ── 在线更新配置 ── >> mykey.py
    echo update_source = 'https://raw.githubusercontent.com/wxwsm666/GenericAgent-v1-web/main/version.json' >> mykey.py
    echo update_channel = 'stable' >> mykey.py
    echo [OK] update_source added.
)

:no_template
:: Continue anyway - user can configure via web UI

:: ---- Step 5: Start server ----
:launch
set PORT=18600
set "PROJECT_DIR=%~dp0"
if not exist "%PROJECT_DIR%temp" mkdir "%PROJECT_DIR%temp"
set "ERRLOG=%PROJECT_DIR%temp\startup_error.log"

:: Kill any existing process on our port
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING" 2^>nul') do (
    echo [INFO] Port %PORT% in use, killing process %%a...
    taskkill /F /PID %%a >nul 2>&1
)

echo ================================================================
echo   Starting Web UI...
echo.
echo   Browser will open:  http://localhost:%PORT%
echo   Tip: Close browser? Click tray icon to reopen.
echo   Press Ctrl+C or close this window to stop.
echo ================================================================
echo.

:: Try web_server directly first (more reliable on Windows)
"%PROJECT_DIR%.venv\Scripts\python.exe" -c "import flask; print('Flask OK')" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Flask not installed. Reinstalling dependencies...
    "%PROJECT_DIR%.venv\Scripts\pip.exe" install flask requests pystray Pillow --quiet 2>>"%ERRLOG%"
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies. See: %ERRLOG%
        echo [ERROR] 依赖安装失败。请检查网络连接后重试。
        pause
        exit /b 1
    )
)

cd /d "%PROJECT_DIR%frontends"

echo [OK] Starting server on port %PORT%...
echo   Error log: %ERRLOG%
echo.

:: Try tray_app first (gives system tray icon for easy re-access)
:: If it fails, fall back to web_server.py directly
set TRAY_OK=0
"%PROJECT_DIR%.venv\Scripts\python.exe" -c "import pystray; print('OK')" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Launching with system tray icon...
    "%PROJECT_DIR%.venv\Scripts\python.exe" "%PROJECT_DIR%tray_app.py" --port %PORT% 2>>"%ERRLOG%"
    set EXIT_CODE=%ERRORLEVEL%
    if !EXIT_CODE! neq 0 (
        echo [WARN] Tray app failed (code !EXIT_CODE!), falling back to direct mode...
        set TRAY_OK=0
    ) else (
        set TRAY_OK=1
    )
)

if %TRAY_OK% equ 0 (
    echo [INFO] Running in direct mode (no tray icon)...
    "%PROJECT_DIR%.venv\Scripts\python.exe" "%PROJECT_DIR%frontends\web_server.py" --port %PORT% 2>>"%ERRLOG%"
    set EXIT_CODE=%ERRORLEVEL%
)

echo.
if %EXIT_CODE% neq 0 (
    echo ═══════════════════════════════════════════════════════
    echo   Server exited with code %EXIT_CODE%
    echo   If this was unexpected, check the log below:
    echo ═══════════════════════════════════════════════════════
    echo.
    type "%ERRLOG%" 2>nul
    echo.
    echo ═══════════════════════════════════════════════════════
    echo   常见问题：
    echo    1. 端口 %PORT% 被占用 - 关闭其他程序后重试
    echo    2. mykey.py 配置错误 - 检查 API Key 配置
    echo    3. Python 版本过低 - 需要 Python 3.10+
    echo    4. 杀毒软件拦截 - 添加信任列表
    echo ═══════════════════════════════════════════════════════
) else (
    echo Server stopped normally.
)
echo.
pause
