@echo off
rem Sends the daily complaints report (due 9:00 AM India time) and appends the result to logs\daily-report.log.
rem Windows Task Scheduler runs this file (see INGEST_API.md / the handover notes). Running it more than
rem once a day is safe: the report is sent at most once per day.
cd /d "%~dp0"
if not exist logs mkdir logs
echo ---- %date% %time% >> logs\daily-report.log
.venv\Scripts\python.exe manage.py send_daily_report >> logs\daily-report.log 2>&1
