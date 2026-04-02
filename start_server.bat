@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found at .venv
  exit /b 1
)

".venv\Scripts\python.exe" serve_waitress.py

