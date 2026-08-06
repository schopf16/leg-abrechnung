@echo off
REM Double-click updater for end users: fetches the latest source code
REM from GitHub. No GitHub account, no manual Git installation and no
REM command-line knowledge required -- if Git is missing, a small
REM portable copy is downloaded automatically (no installer, no admin
REM rights, nothing added to the system).
REM
REM Never touches data\, output\, backups\ or .venv\ -- those are not
REM part of the source code and are left completely alone.

setlocal enabledelayedexpansion
cd /d "%~dp0"

REM %~dp0 always ends in a backslash, which breaks quoted paths passed to
REM external tools like robocopy (a trailing backslash right before the
REM closing quote escapes the quote instead of ending the argument). All
REM other paths are built from this trailing-slash-free PROJECT_DIR instead.
set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

set "REPO_URL=https://github.com/schopf16/leg-abrechnung.git"
set "DEFAULT_BRANCH=master"
set "MINGIT_DIR=%PROJECT_DIR%\.mingit"
set "MINGIT_URL=https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.3/MinGit-2.55.0.3-64-bit.zip"

echo === LEG-Abrechnung: Update ===
echo.

REM --- Step 1: find a usable git.exe -- system-wide, our own portable copy, or download one ---
set "GIT_EXE="
for /f "delims=" %%G in ('where git 2^>nul') do (
    if not defined GIT_EXE set "GIT_EXE=%%G"
)

if not defined GIT_EXE if exist "%MINGIT_DIR%\cmd\git.exe" (
    set "GIT_EXE=%MINGIT_DIR%\cmd\git.exe"
)

if not defined GIT_EXE (
    echo Git wurde auf diesem Computer nicht gefunden.
    echo Lade eine kleine, eigene Kopie herunter ^(einmalig, ca. 40 MB^) ...
    echo.

    if not exist "%MINGIT_DIR%" mkdir "%MINGIT_DIR%"
    set "MINGIT_ZIP=%TEMP%\leg_mingit_download.zip"

    REM NOTE: this PowerShell command must stay on a single physical line.
    REM Batch's "^" line-continuation does not work inside a quoted string
    REM that spans multiple lines -- the caret gets passed through
    REM literally instead of joining the lines, breaking the command.
    REM Also: MINGIT_ZIP is set and used within this same parenthesized
    REM block, so it must be read with delayed expansion (!VAR!) -- %VAR%
    REM would resolve to whatever it was when the block started (empty).
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { $null = Invoke-WebRequest -Uri '%MINGIT_URL%' -OutFile '!MINGIT_ZIP!' -UseBasicParsing } catch { Write-Host $_.Exception.Message; exit 1 }"
    if errorlevel 1 (
        echo.
        echo FEHLER: Der Download von Git ist fehlgeschlagen.
        echo Bitte pruefen Sie Ihre Internetverbindung und versuchen Sie es erneut.
        pause
        exit /b 1
    )

    echo Entpacke Git ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '!MINGIT_ZIP!' -DestinationPath '%MINGIT_DIR%' -Force"
    del "!MINGIT_ZIP!" >nul 2>&1

    if not exist "%MINGIT_DIR%\cmd\git.exe" (
        echo.
        echo FEHLER: Git konnte nicht eingerichtet werden.
        pause
        exit /b 1
    )
    set "GIT_EXE=%MINGIT_DIR%\cmd\git.exe"
    echo Git wurde erfolgreich eingerichtet.
    echo.
)

REM --- Step 2: no local copy yet -> download the whole project for the first time ---
if not exist "%PROJECT_DIR%\.git" (
    echo Es ist noch keine lokale Version vorhanden. Lade das Programm herunter ...
    echo.

    set "CLONE_TMP=%TEMP%\leg_abrechnung_clone_%RANDOM%"
    "%GIT_EXE%" clone --quiet "%REPO_URL%" "!CLONE_TMP!"
    if errorlevel 1 (
        echo.
        echo FEHLER: Herunterladen fehlgeschlagen.
        echo Bitte pruefen Sie Ihre Internetverbindung und versuchen Sie es erneut.
        pause
        exit /b 1
    )

    robocopy "!CLONE_TMP!" "%PROJECT_DIR%" /E >nul
    REM Robocopy's exit codes 0-7 all mean success (various combinations of
    REM "files copied" / "extra files found"); 8 or higher means failure.
    if errorlevel 8 (
        rmdir /s /q "!CLONE_TMP!" >nul 2>&1
        echo.
        echo FEHLER: Die heruntergeladenen Dateien konnten nicht kopiert werden.
        pause
        exit /b 1
    )
    rmdir /s /q "!CLONE_TMP!" >nul 2>&1

    echo.
    echo Fertig: Das Programm wurde heruntergeladen.
    goto :update_dependencies
)

REM --- Step 3: local copy exists -> update it to match GitHub exactly ---
echo Suche nach einer neueren Version ...
"%GIT_EXE%" fetch --quiet origin
if errorlevel 1 (
    echo.
    echo FEHLER: Konnte keine Verbindung zu GitHub herstellen.
    echo Bitte pruefen Sie Ihre Internetverbindung und versuchen Sie es erneut.
    pause
    exit /b 1
)

REM Deliberately no "git clean": it would also delete untracked helper
REM files that must survive an update -- this very script (if it is not
REM yet part of the repository), the downloaded .mingit\ folder, and
REM anything the user might have placed in the project folder. Syncing
REM tracked files via "reset --hard" is all an update needs; it already
REM removes files that were tracked before and got deleted upstream.
for /f "delims=" %%R in ('"%GIT_EXE%" rev-parse HEAD') do set "BEFORE=%%R"
"%GIT_EXE%" reset --quiet --hard "origin/%DEFAULT_BRANCH%"
for /f "delims=" %%R in ('"%GIT_EXE%" rev-parse HEAD') do set "AFTER=%%R"

echo.
if "%BEFORE%"=="%AFTER%" (
    echo Sie haben bereits die neuste Version -- keine Aenderungen noetig.
) else (
    echo Update erfolgreich -- Ihre Daten ^(Ordner data, output, backups^) sind
    echo davon nicht betroffen und bleiben unveraendert erhalten.
)

:update_dependencies
REM If the app was already set up before, refresh its dependencies too,
REM in case an update added or changed one. Skipped on a brand-new
REM install: start.bat sets everything up from scratch on first launch.
if exist ".venv\Scripts\python.exe" (
    echo.
    echo Aktualisiere Programmbibliotheken ...
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
)

echo.
echo Sie koennen die App jetzt wie gewohnt ueber start.bat oeffnen.
pause
