@echo off
set PYTHONPATH=.\src
echo Updating dashboard data...
python -m tracker.main export-ui
echo.
echo Opening Dashboard...
start dashboard.html
