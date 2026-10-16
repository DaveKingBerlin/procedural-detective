@echo off
setlocal

rem start-server.bat - start the Procedural Detective stack for a target.
rem   dev         -> docker compose -f docker-compose.yml up -d
rem   netcup/prod -> docker compose -f docker-compose.prod.yml up -d
rem
rem Production is STANDALONE (docker-compose.prod.yml). Never the base+prod
rem merge. Non-destructive: persistent volumes are preserved; no teardown that
rem deletes volumes, no image cleanup. Container state is printed afterward.

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

echo ==^> start-server: target="%TARGET%" configuration="%COMPOSE_FILE%"
docker compose -f "%COMPOSE_FILE%" up -d
if errorlevel 1 (
    echo error: docker compose up failed 1>&2
    exit /b 1
)
echo ==^> start-server: container state
docker compose -f "%COMPOSE_FILE%" ps
echo ==^> start-server: done (target="%TARGET%")
exit /b 0

:usage
echo Usage: start-server.bat ^<target^> 1>&2
echo.
echo Targets:
echo   dev        start the local development stack ^(docker-compose.yml^)
echo   netcup     start the production stack ^(docker-compose.prod.yml^)
echo   prod       alias for netcup ^(production^)
exit /b 2
