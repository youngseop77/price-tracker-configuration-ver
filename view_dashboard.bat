@echo off
set PYTHONPATH=.\src
echo Updating dashboard data...
python -m tracker.main export-ui
echo.
echo Starting local server at http://localhost:8000
echo Press Ctrl+C to stop the server.
start "" http://localhost:8000/dashboard.html
python -m http.server 8000
