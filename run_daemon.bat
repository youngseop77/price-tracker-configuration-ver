@echo off
set PYTHONPATH=.\src
echo Starting Price Tracker Daemon...
echo Log will be saved to job_log.txt
python -m tracker.main daemon --interval 3600 --verbose > job_log.txt 2>&1
pause
