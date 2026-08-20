@echo off
REM ===========================================================
REM  INSTALAR.bat - doble clic aqui para instalar vozbot.
REM  Abre el instalador con ventana grafica.
REM ===========================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalador.ps1"
if errorlevel 1 (
    echo.
    echo El instalador no pudo abrirse.
    echo Prueba con clic derecho sobre instalador.ps1 - Ejecutar con PowerShell
    pause
)
