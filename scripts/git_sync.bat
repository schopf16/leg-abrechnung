@echo off
REM Simple helper to save the code to GitHub: stages, commits and pushes
REM only the source code, with a safety check that aborts if any database,
REM output or config file would accidentally be included.

setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo === LEG-Abrechnung: Code sichern ===
echo.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo Dieses Verzeichnis ist kein Git-Repository.
    echo Fuehren Sie zuerst "git init" aus.
    pause
    exit /b 1
)

echo Fuege geaenderte Dateien hinzu ^(data/, output/, backups/ werden durch .gitignore ausgeschlossen^) ...
git add -A

echo.
echo Pruefe auf versehentlich gestagte sensible Dateien ...
set SENSITIVE_FOUND=0
for /f "delims=" %%F in ('git diff --cached --name-only') do (
    echo %%F | findstr /I /R "\.sqlite3$ \.sqlite$ \.db$ ^data/ ^output/ ^backups/ config\.local\." >nul
    if not errorlevel 1 (
        echo   WARNUNG: %%F sieht nach einer sensiblen Datei aus.
        set SENSITIVE_FOUND=1
    )
)

if "!SENSITIVE_FOUND!"=="1" (
    echo.
    echo ABBRUCH: Es wurden moegliche Datenbank-/Konfig-Dateien im Commit gefunden.
    echo Nichts wurde committet oder gepusht. Bitte .gitignore und die oben
    echo aufgelisteten Dateien pruefen.
    git reset >nul
    pause
    exit /b 1
)

git diff --cached --quiet
if not errorlevel 1 (
    echo Keine Aenderungen zum Committen.
    pause
    exit /b 0
)

set /p COMMIT_MSG="Commit-Nachricht (leer = 'Update'): "
if "%COMMIT_MSG%"=="" set COMMIT_MSG=Update

git commit -m "%COMMIT_MSG%"
if errorlevel 1 (
    echo Commit fehlgeschlagen.
    pause
    exit /b 1
)

echo.
echo Sende an GitHub ^(git push^) ...
git push
if errorlevel 1 (
    echo.
    echo Push fehlgeschlagen. Ist ein Remote eingerichtet? ^(git remote add origin ...^)
    echo Der Commit wurde aber lokal gespeichert.
    pause
    exit /b 1
)

echo.
echo Fertig: Code wurde committet und zu GitHub gesendet.
pause
