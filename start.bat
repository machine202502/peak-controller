@echo off
rem Пик-контроллер: если собран exe — запускаем его, иначе из исходников (py -3.12)
cd /d "%~dp0"
if exist "dist\PeakController\PeakController.exe" (
  start "" "dist\PeakController\PeakController.exe"
) else (
  start "" pyw -3.12 "app\main.py"
)
