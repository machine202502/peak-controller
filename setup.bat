@echo off
rem Установка окружения пик-контроллера: .venv на Python 3.12 + PySide6, requests, tzdata.
rem Запускать один раз (и повторно, если сломались зависимости или сменился Python).
cd /d "%~dp0"
setlocal
title Peak Controller - setup

set "PY312="

rem 1. штатный лаунчер python.org
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PY312=py -3.12"
)

rem 2. python 3.12 напрямую из PATH
if not defined PY312 (
  python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY312=python"
)

rem 3. pyenv-win: установленная версия 3.12.x (PYENV_ROOT уже с завершающим слешем)
if not defined PY312 (
  for /d %%d in ("%PYENV_ROOT%versions\3.12.*" "%USERPROFILE%\.pyenv\pyenv-win\versions\3.12.*") do (
    if exist "%%d\python.exe" set "PY312=%%d\python.exe"
  )
)

if not defined PY312 (
  echo.
  echo  Python 3.12 не найден.
  echo  Установите его: winget install Python.Python.3.12  (или python.org / pyenv install 3.12.10)
  echo  Затем запустите setup.bat снова.
  echo.
  pause
  exit /b 1
)

echo [1/3] Интерпретатор: %PY312%
%PY312% -c "import sys; print('      Python', sys.version.split()[0])"

echo [2/3] Создаю .venv (если ещё нет)...
if not exist ".venv\Scripts\python.exe" (
  %PY312% -m venv .venv || goto :fail
) else (
  echo       .venv уже существует - обновляю пакеты
)

echo [3/3] Ставлю пакеты: PySide6, requests, tzdata...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install --upgrade PySide6 requests tzdata || goto :fail

echo.
echo  Проверка зависимостей:
".venv\Scripts\python.exe" -c "import PySide6, requests, tzdata; from PySide6.QtWebEngineWidgets import QWebEngineView; from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai'); print('  OK: PySide6', PySide6.__version__)" || goto :fail
echo.
echo  Готово. Запускайте start.bat
echo.
pause
exit /b 0

:fail
echo.
echo  Установка не удалась (код %errorlevel%). Проверьте интернет и свободное место на диске.
echo.
pause
exit /b 1
