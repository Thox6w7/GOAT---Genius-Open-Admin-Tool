@echo off
setlocal enabledelayedexpansion
title GOAT // Pipeline de Compilation Complete

echo ========================================================
echo   GENIUS OPEN ADMIN TOOL (GOAT) - BUILD & PACKAGING
echo ========================================================
echo.

:: 1. Verifier et fermer les processus residuels
echo [*] Arret force des processus Python et GOAT existants...
taskkill /F /IM GOAT.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
echo [OK] Aucun processus bloquant en cours.
echo.

:: 2. Compilation PyInstaller (build_exe.py)
echo [*] Lancement de build_exe.py avec l'environnement virtuel...
if not exist ".venv\Scripts\python.exe" (
    echo [ERREUR] Impossible de trouver .venv\Scripts\python.exe !
    goto ERROR
)

.venv\Scripts\python.exe build_exe.py
if %ERRORLEVEL% neq 0 (
    echo [ERREUR] Echec lors de la compilation PyInstaller.
    goto ERROR
)
echo.

:: 3. Detection du compilateur Inno Setup (ISCC.exe)
echo [*] Verification de la presence d'Inno Setup...
set "ISCC_PATH="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    set "ISCC_PATH=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    set "ISCC_PATH=C:\Program Files\Inno Setup 6\ISCC.exe"
) else (
    echo [ERREUR] Inno Setup (ISCC.exe) n'a pas ete trouve dans Program Files !
    echo Verifiez l'installation d'Inno Setup 6.
    goto ERROR
)

:: 4. Compilation de l'installateur Windows (installer.iss)
echo [*] Compilation de l'installateur Windows via Inno Setup...
"%ISCC_PATH%" installer.iss
if %ERRORLEVEL% neq 0 (
    echo [ERREUR] Echec lors de la compilation de l'installateur Inno Setup.
    goto ERROR
)
echo.

:: 5. Succes et ouverture du dossier de sortie
echo ========================================================
echo   SUCCES TOTAL : Release Pre-Alpha prete !
echo   Fichier genere : Output\GOAT_Setup_PreAlpha.exe
echo ========================================================
echo.
explorer Output
pause
exit /b 0

:ERROR
echo.
echo ========================================================
echo   ECHEC : La generation de la release a ete interrompue.
echo ========================================================
echo.
pause
exit /b 1