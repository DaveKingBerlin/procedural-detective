@echo off
setlocal

rem build-docker.bat - build the Procedural Detective image for a target.
rem   dev         -> docker compose -f docker-compose.yml build
rem   netcup/prod -> docker compose -f docker-compose.prod.yml build
rem
rem Production is STANDALONE (docker-compose.prod.yml). Never the base+prod
rem merge. Non-destructive: build only; no volume removal, no image cleanup.

if "%~1"=="" goto :usage
if not "%~2"=="" goto :usage

set "TARGET=%~1"
set "COMPOSE_FILE="
if /i "%TARGET%"=="dev" set "COMPOSE_FILE=docker-compose.yml"
if /i "%TARGET%"=="netcup" set "COMPOSE_FILE=docker-compose.prod.yml"
if /i "%TARGET%"=="prod" set "COMPOSE_FILE=docker-compose.prod.yml"

if not defined COMPOSE_FILE (
    echo error: invalid target "%TARGET%" 1>&2
    goto :usage
)

cd /d "%~dp0"

echo ==^> build-docker: target="%TARGET%" configuration="%COMPOSE_FILE%"
docker compose -f "%COMPOSE_FILE%" build
if errorlevel 1 (
    echo error: docker compose build failed 1>&2
    exit /b 1
)
echo ==^> build-docker: done (target="%TARGET%")
exit /b 0

:usage
echo Usage: build-docker.bat ^<target^> 1>&2
echo.
echo Targets:
echo   dev        build the local development stack ^(docker-compose.yml^)
echo   netcup     build the production stack ^(docker-compose.prod.yml^)
echo   prod       alias for netcup ^(production^)
exit /b 2
