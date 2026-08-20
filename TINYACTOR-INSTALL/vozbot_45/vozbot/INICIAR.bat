@echo off
REM INICIAR.bat - abre el panel de vozbot. Doble clic y listo.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No hay entorno virtual en esta carpeta.
    echo Crealo una vez con:
    echo     py -m venv .venv
    echo     .venv\Scripts\activate
    echo     python -m pip install -r requirements.txt
    echo     python -m playwright install chromium
    pause
    exit /b 1
)

".venv\Scripts\python.exe" run.py
if errorlevel 1 pause
