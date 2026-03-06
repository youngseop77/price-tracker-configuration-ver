@echo off
set PYTHONPATH=.\src
echo Naver Price Tracker Daemon Starting...
echo Interval: 1 hour (3600 seconds)
python -m tracker.main daemon
pause
