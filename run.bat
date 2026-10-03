@echo off
REM Scheduler-friendly wrapper: runs the scraper with this folder's venv and
REM appends a timestamped line-set to rent_history.csv. Registered by
REM schedule.ps1 as the "RentScrape" task; see the README.
REM
REM Any extra args are passed through, e.g.:  run.bat --no-browser
REM Bare run.bat writes CSVs only. For the full report - charts, explorer.html
REM and the emailed shortlist - use:  run.bat --visualize --email
setlocal
cd /d "%~dp0"

REM Force UTF-8 for Python's stdout. Under Task Scheduler stdout is a redirected
REM file, so Python would otherwise encode with the ANSI codepage (cp1252) and a
REM single scraped character outside it kills the run with UnicodeEncodeError.
set PYTHONUTF8=1

REM Unbuffered, so run.log fills in as the run goes instead of in 8 KB chunks.
REM A two-hour run that shows nothing until it ends is indistinguishable from
REM a hung one.
set PYTHONUNBUFFERED=1

set "LOG=%~dp0run.log"
set "MARK=%~dp0.run-in-progress"

REM Rotate at 5 MB so twice-daily output doesn't grow without bound.
for %%A in ("%LOG%") do if %%~zA GTR 5242880 move /y "%LOG%" "%LOG%.1" >nul

REM A marker still here means the PREVIOUS run never reached its own finish
REM line: terminated by the scheduler's ExecutionTimeLimit, or lost to a reboot.
REM Such a run reports nothing at all - the failure branch at the bottom needs
REM this script to survive, which is exactly what did not happen - so the next
REM run is the one that has to speak up.
if exist "%MARK%" (
    echo ==== previous run died without finishing ==== >> "%LOG%"
    "%~dp0venv\Scripts\python.exe" "%~dp0notify.py" --failure "%LOG%" --died >> "%LOG%" 2>&1
)
echo %date% %time% %*> "%MARK%"

echo ==== run started %date% %time% ==== >> "%LOG%"
"%~dp0venv\Scripts\python.exe" "%~dp0scraper.py" %* >> "%LOG%" 2>&1
REM Capture this immediately - any later command overwrites errorlevel.
set RC=%errorlevel%
echo ==== run finished %date% %time% (exit %RC%) ==== >> "%LOG%"

REM Reached the end under our own power, so the run is accounted for either way.
del "%MARK%" >nul 2>&1

REM A failed run sends no report, so say so out of band.
if not "%RC%"=="0" (
    echo ==== sending failure alert ==== >> "%LOG%"
    "%~dp0venv\Scripts\python.exe" "%~dp0notify.py" --failure "%LOG%" --code %RC% >> "%LOG%" 2>&1
)

REM Hand the real code to Task Scheduler so "Last Run Result" means something.
exit /b %RC%
