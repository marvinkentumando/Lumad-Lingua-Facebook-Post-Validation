@echo off
cd /d "%~dp0"
echo ========================================
echo   Lumad Lingua - Validation Studio
echo ========================================
echo.
echo Open http://127.0.0.1:8000 in your browser.
echo Read the access password printed below.
echo Press Ctrl+C to stop this server.
echo.
py -3 server.py
if errorlevel 1 (
  echo.
  echo Python launcher unavailable. Trying python...
  python server.py
)
pause
