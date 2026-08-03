@echo off
REM Double-click launcher for the LEG-Abrechnung app.
REM Creates the virtual environment and installs dependencies on first run.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Erstelle virtuelle Umgebung ^(einmalig^) ...
    py -3 -m venv .venv
    if errorlevel 1 (
        echo Konnte keine virtuelle Umgebung erstellen. Ist Python installiert?
        pause
        exit /b 1
    )
    echo Installiere Abhaengigkeiten ^(einmalig, kann einige Minuten dauern^) ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

".venv\Scripts\python.exe" run.py

if errorlevel 1 (
    echo.
    echo Die Anwendung wurde mit einem Fehler beendet.
    pause
)
