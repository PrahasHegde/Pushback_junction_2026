@echo off
REM One-click start for Windows: installs dependencies, checks setup, starts Pushback and opens the browser.
cd /d "%~dp0"
if not exist .env (
  copy .env.example .env >nul
  echo Created .env from .env.example. Add your GEMINI_API_KEY and EXA_API_KEY to .env, then run this again.
  notepad .env
  exit /b 1
)
python -m pip install -q -r requirements.txt
python check_setup.py
if errorlevel 1 exit /b 1
if "%PORT%"=="" set PORT=8899
start "" http://localhost:%PORT%
python main.py
