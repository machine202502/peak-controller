@echo off
rem Пик-контроллер: запуск в трее.
rem Порядок выбора: собранный exe -> .venv проекта (Python 3.12 + зависимости)
rem                  -> штатный лаунчер python.org (pyw -3.12) -> pythonw из PATH.
cd /d "%~dp0"
setlocal
title Peak Controller

rem --- 0. собранный exe (если когда-нибудь будет собран PyInstaller'ом)
if exist "dist\PeakController\PeakController.exe" (
  start "" "dist\PeakController\PeakController.exe"
  exit /b 0
)

rem --- 1. виртуальное окружение проекта: свой Python 3.12 и все пакеты внутри .venv
if exist ".venv\Scripts\pythonw.exe" (
  set "PYW=%CD%\.venv\Scripts\pythonw.exe"
  set "PYEXE=%CD%\.venv\Scripts\python.exe"
  set "ARG12="
  goto :check
)

rem --- 2. штатный лаунчер Python (python.org): pyw -3.12
where pyw >nul 2>nul
if errorlevel 1 goto :try_path
set "PYW=pyw"
set "PYEXE=py"
set "ARG12=-3.12"
goto :check

:try_path
rem --- 3. pythonw 3.12 из PATH (например, pyenv-win с активной 3.12)
where pythonw >nul 2>nul
if errorlevel 1 goto :nodeps
set "PYW=pythonw"
set "PYEXE=python"
set "ARG12="

:check
rem Быстрая проверка зависимостей: иначе pythonw падает молча и <ничего не происходит>
"%PYEXE%" %ARG12% -c "import PySide6, requests, tzdata" >nul 2>nul
if errorlevel 1 goto :nodeps

start "" "%PYW%" %ARG12% "%CD%\app\main.py"
exit /b 0

:nodeps
echo.
echo  Не хватает Python 3.12 и/или зависимостей приложения.
echo.
echo  Нужно установить:
echo    Python 3.12 (python.org, с галочкой "py launcher", или pyenv-win 3.12.x)
echo    пакеты: PySide6, requests, tzdata  (tzdata обязателен: в Windows нет базы часовых поясов)
echo.
echo  Проще всего - запустить setup.bat из этой папки (создаст .venv и поставит пакеты).
echo  Вручную:
echo    py -3.12 -m venv .venv
echo    .venv\Scripts\python.exe -m pip install PySide6 requests tzdata
echo.
pause
exit /b 1
