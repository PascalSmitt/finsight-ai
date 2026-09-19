@echo off
rem One-click launcher: installs dependencies (first run), starts the server and opens the browser.
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
start "" http://localhost:8000
python -m uvicorn app.main:app --port 8000
pause
