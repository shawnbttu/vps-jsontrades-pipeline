@echo off
setlocal

set "SCRIPT=C:\trade-export\live_strategy_trade_export.py"
set "DB_PATH=C:\Users\Administrator\Documents\NinjaTrader 8\db\NinjaTrader.sqlite"
set "OUTPUT=C:\trade-export\out\apolloes-hermes-live-trades.json"
set "LOG_PATH=C:\trade-export\out\apolloes-hermes-live-exporter.log"
set "RESTART_DELAY_SECONDS=5"

:run
echo [%date% %time%] Starting live exporter >> "%LOG_PATH%"
py "%SCRIPT%" --db-path "%DB_PATH%" --output "%OUTPUT%" --watch --poll-seconds 2 --log-path "%LOG_PATH%"
echo [%date% %time%] Exporter exited with code %ERRORLEVEL%; restarting in %RESTART_DELAY_SECONDS%s >> "%LOG_PATH%"
timeout /t %RESTART_DELAY_SECONDS% /nobreak >nul
goto run

endlocal
