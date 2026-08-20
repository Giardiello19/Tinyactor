@echo off
setlocal enabledelayedexpansion
REM ===========================================================
REM  INICIAR-LDPLAYER.bat
REM  Conecta con el Chrome que corre DENTRO de LDPlayer.
REM
REM  REQUISITO EN LDPLAYER (una sola vez):
REM    Ajustes -> Otros -> Depuracion ADB -> "Abrir conexion local"
REM    y reinicia el emulador.
REM ===========================================================

set ADB=C:\LDPlayer\LDPlayer14\adb.exe
REM 9333 y no 9222: el Chrome de escritorio suele ocupar el 9222
set PUERTO_CDP=9333
set TMPLIST=%TEMP%\vb_devices.txt

if not exist "%ADB%" (
    echo No encuentro adb.exe en: %ADB%
    pause
    exit /b 1
)

echo Reiniciando el servidor ADB...
"%ADB%" kill-server >nul 2>&1
"%ADB%" start-server >nul 2>&1

echo Buscando la instancia de LDPlayer...
for %%P in (5555 5554 5556 5557 5558 5559 5560 5561 62001 62025 21503) do (
    "%ADB%" connect 127.0.0.1:%%P >nul 2>&1
)

echo.
echo Dispositivos encontrados:
"%ADB%" devices
"%ADB%" devices > "%TMPLIST%" 2>&1

set SERIAL=
for /f "usebackq tokens=1,2" %%A in ("%TMPLIST%") do (
    if "%%B"=="device" if not defined SERIAL set SERIAL=%%A
)
del "%TMPLIST%" >nul 2>&1

if not defined SERIAL (
    echo.
    echo ===========================================================
    echo  Ninguna instancia respondio. Revisa, en este orden:
    echo.
    echo   1. LDPlayer -^> Ajustes -^> Otros -^> Depuracion ADB
    echo      ponlo en "Abrir conexion local" y REINICIA el emulador.
    echo.
    echo   2. Dentro de Android: Ajustes -^> Opciones de desarrollador
    echo      -^> Depuracion USB activada.
    echo.
    echo   3. Vuelve a ejecutar este archivo.
    echo ===========================================================
    pause
    exit /b 1
)

echo.
echo Instancia detectada: !SERIAL!
echo Abriendo el canal de automatizacion...
"%ADB%" forward --remove-all >nul 2>&1
"%ADB%" -s !SERIAL! forward tcp:%PUERTO_CDP% localabstract:chrome_devtools_remote

echo.
echo Comprobando que Chrome del emulador responde...
curl -s http://localhost:%PUERTO_CDP%/json/version
echo.
echo  ^(El User-Agent debe decir "Android" o "Linux".
echo   Si dice "Windows", estas hablando con el Chrome de tu PC.^)
echo.
echo.
echo ===========================================================
echo  Si arriba ves un JSON con "Chrome/...", ya esta listo:
echo     python app.py     (tu generador de voz)
echo     python run.py     (el bot)
echo.
echo  Si salio vacio: abre Chrome DENTRO del emulador con el
echo  juego cargado y vuelve a ejecutar este archivo.
echo ===========================================================
pause
