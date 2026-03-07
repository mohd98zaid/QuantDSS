@echo off
title QuantDSS - Run Application
color 0A

echo ========================================
echo   Starting QuantDSS Application...
echo ========================================
echo.

:: Check if Docker is running
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Docker is not running. Please start Docker Desktop first.
    pause
    exit /b 1
)

echo Building and starting Docker stack...
docker compose build frontend
docker compose up -d

echo.
echo ========================================
echo   QuantDSS is running!
echo ========================================
echo.
echo   Dashboard:  http://localhost:3000
echo   API Docs:   http://localhost:8001/docs
echo   API Health: http://localhost:8001/api/v1/health
echo.
echo   To view logs, type: docker-compose logs -f
echo   To stop the app, run: stop.bat
echo ========================================
pause
