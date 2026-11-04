@echo off
chcp 65001 >nul 2>&1
title BatteryScope - 充放电功率实时监控

cd /d "%~dp0"

rem --- 定位可用的 Python（优先带 fastapi/uvicorn 的系统 Python）---
set PY=
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
) do (
    if exist %%P (
        if not defined PY (
            %%P -c "import fastapi,uvicorn" >nul 2>&1 && set PY=%%~P
        )
    )
)

if not defined PY (
    for %%P in (
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    ) do (
        if exist %%P if not defined PY set PY=%%~P
    )
)

if not defined PY (
    echo.
    echo   [错误] 未找到 Python，请先安装 Python 3.10+
    echo.
    pause
    exit /b 1
)

echo.
echo   使用 Python: %PY%
echo   正在启动 BatteryScope，浏览器会自动打开...
echo   关闭本窗口即可停止监控
echo.

start "" "http://127.0.0.1:8765/"
"%PY%" battery_server.py --port 8765 --interval 0.5

echo.
echo   服务已停止。
pause
