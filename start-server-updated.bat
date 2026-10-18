@echo off
setlocal EnableExtensions

set "TARGET=%~1"

if "%TARGET%"=="" goto :usage

if /I "%TARGET%"=="dev" goto :devbox
if /I "%TARGET%"=="devbox" goto :devbox
if /I "%TARGET%"=="netcup" goto :prod
if /I "%TARGET%"=="prod" goto :prod

echo ERROR: Unknown target "%TARGET%".
goto :usage

:devbox
set "ENVIRONMENT=development"
set "CADDY_DOMAIN=enshrouded-server"
set "PD_GENERATION_DEBUG_LOGS=true"
set "COMPOSE_ARGS=-f docker-compose.prod.yml -f docker-compose.lan.yml -f docker-compose.debug.yml"

call :check_file docker-compose.prod.yml || exit /b 1
call :check_file docker-compose.lan.yml || exit /b 1
call :check_file docker-compose.debug.yml || exit /b 1
call :check_docker || exit /b 1

echo ==^> Target: %TARGET%
echo ==^> Environment: %ENVIRONMENT%
echo ==^> Caddy domain: %CADDY_DOMAIN%
echo ==^> Debug generation logs: %PD_GENERATION_DEBUG_LOGS%
echo ==^> Compose: prod + lan + debug
echo ==^> Starting Dev-Box services with --force-recreate...

docker compose %COMPOSE_ARGS% up -d --force-recreate
if errorlevel 1 exit /b %errorlevel%

echo.
echo ==^> Container status:
docker compose %COMPOSE_ARGS% ps
if errorlevel 1 exit /b %errorlevel%

echo.
echo ==^> Dev-Box server started successfully.
exit /b 0

:prod
set "COMPOSE_ARGS=-f docker-compose.prod.yml"

call :check_file docker-compose.prod.yml || exit /b 1
call :check_docker || exit /b 1

echo ==^> Target: %TARGET%
echo ==^> Compose: docker-compose.prod.yml
echo ==^> Starting production services...

docker compose %COMPOSE_ARGS% up -d
if errorlevel 1 exit /b %errorlevel%

echo.
echo ==^> Container status:
docker compose %COMPOSE_ARGS% ps
if errorlevel 1 exit /b %errorlevel%

echo.
echo ==^> Production server started successfully.
exit /b 0

:check_file
if not exist "%~1" (
    echo ERROR: %~1 not found. Run this script from the repository root.
    exit /b 1
)
exit /b 0

:check_docker
docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker is not available. Start Docker Desktop and try again.
    exit /b 1
)
exit /b 0

:usage
echo Usage: %~nx0 ^<dev^|devbox^|netcup^|prod^>
echo.
echo   dev      Start Dev-Box using prod + lan + debug compose files
echo   devbox   Alias for dev
echo   netcup   Start production using docker-compose.prod.yml only
echo   prod     Alias for netcup
exit /b 2
