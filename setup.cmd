@echo off
cd /d "%~dp0"
python -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt -c constraints.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" setup_model.py --sample
if errorlevel 1 goto failed
echo Ready. Run start.cmd to open LocalScribe.
pause
exit /b 0
:failed
echo Setup could not finish. See the message above.
pause
exit /b 1
