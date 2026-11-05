@echo off
cd /d "%~dp0"
echo Order bot laptop agent. Keep this window open; Chrome opens here when a run starts.
:loop
python agent.py
echo Agent stopped. Restarting in 10 seconds (close this window to quit)...
timeout /t 10 /nobreak >nul
goto loop
