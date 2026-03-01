@echo off
title QuantDSS - Stop Application
color 0C

echo ========================================
echo   Stopping QuantDSS Application...
echo ========================================
echo.

docker-compose down

echo.
echo ========================================
echo   QuantDSS has been stopped.
echo ========================================
pause
