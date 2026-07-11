@echo off
REM Double-click launcher for the trading bot (live mode against the FxPro demo account).
REM Closing this window stops the bot (equivalent to Ctrl+C in a normal terminal run).

cd /d "%~dp0"
set DRY_RUN=false

REM Local secrets (API keys, etc.) live in local_env.bat, which is
REM gitignored -- see local_env.bat.example for the template.
if exist "%~dp0local_env.bat" call "%~dp0local_env.bat"

echo ============================================================
echo  Trading bot starting in LIVE mode (DRY_RUN=false)
echo  Account: FxPro demo 591843104 / EURUSD
echo  Close this window at any time to stop the bot.
echo ============================================================
echo.

python main.py

echo.
echo ============================================================
echo  Bot has stopped.
echo ============================================================
pause
