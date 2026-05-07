@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
:: ============================================================
::  GenericAgent — Chrome 扩展一键安装 (Windows)
::  双击运行，自动检测 Chrome 状态并引导安装
:: ============================================================

title GenericAgent - Chrome 扩展安装
cd /d "%~dp0"

set "EXT_DIR=%~dp0assets\tmwd_cdp_bridge"

echo.
echo ================================================================
echo    GenericAgent - Chrome 扩展安装
echo ================================================================
echo.

:: ---- Find Chrome ----
set "CHROME="
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

if "%CHROME%"=="" (
    echo [ERROR] 未找到 Chrome 浏览器
    echo   请先安装: https://www.google.com/chrome/
    echo.
    pause
    exit /b 1
)
echo [OK] Chrome: %CHROME%

:: ---- Check Extension ----
if not exist "%EXT_DIR%\manifest.json" (
    echo [ERROR] 扩展文件缺失: %EXT_DIR%
    pause
    exit /b 1
)
echo [OK] 扩展目录: %EXT_DIR%

:: ---- Check if Chrome is running ----
set "CHROME_RUNNING=0"
tasklist /FI "IMAGENAME eq chrome.exe" 2>nul | find /I "chrome.exe" >nul
if not errorlevel 1 set "CHROME_RUNNING=1"

echo.
if !CHROME_RUNNING! equ 1 (
    echo [INFO] Chrome 正在运行，使用手动安装模式
    echo.
    echo   请按以下步骤操作：
    echo.
    echo   1. Chrome 地址栏输入 chrome://extensions 并回车
    echo   2. 打开右上角「开发者模式」开关
    echo   3. 点击左上角「加载已解压的扩展程序」
    echo   4. 选择目录:
    echo      %EXT_DIR%
    echo.

    :: Open extensions page and Explorer
    start "" "chrome://extensions"
    start "" explorer.exe /select,"%EXT_DIR%\manifest.json"

    echo [OK] 已自动打开扩展页面和文件夹
    echo   将文件夹拖入扩展页面或按上方步骤操作即可
) else (
    echo [INFO] Chrome 未运行，使用自动加载模式...
    echo.

    start "" "%CHROME%" --load-extension="%EXT_DIR%" "chrome://extensions/"

    timeout /t 3 /nobreak >nul

    echo [OK] Chrome 已启动，扩展已临时加载
    echo.
    echo   [WARN] 临时加载仅本次 Chrome 会话有效
    echo   如需永久安装，请在 chrome://extensions 页面：
    echo   1. 打开右上角「开发者模式」
    echo   2. 确认 TMWD CDP Bridge 扩展已启用
    echo   3. 若未出现，点「加载已解压的扩展程序」选:
    echo      %EXT_DIR%
)

echo.
echo   验证方法：
echo   1. 回到 GenericAgent Web UI (http://localhost:18600^)
echo   2. 查看顶部工具栏浏览器图标应为 🌐（绿色已连接^)
echo.
pause
