@echo off
rem Пик-контроллер: запуск в трее без окна консоли (Python 3.12 + PySide6)
cd /d "%~dp0"
start "" pyw -3.12 "app\main.py"
