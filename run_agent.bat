@echo off
cd /d "%~dp0"
title Order bot agent
echo Order bot laptop agent. Keep this window open; Chrome opens here when a run starts.
echo Every step the bot takes is shown here and saved in the logs folder.
:loop
python agent.py
echo.
echo ======================================================================
echo  Agent stopped with exit code %errorlevel% at %date% %time%.
echo  The reason is shown just above this box (also in the logs folder).
echo  Restarting in 30 seconds. Close this window to quit.
echo ======================================================================
timeout /t 30 /nobreak >nul
goto loop
