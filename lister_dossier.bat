@echo off
setlocal enabledelayedexpansion
title GOAT // Generateur d'Inventaire

echo ========================================================
echo   GOAT - GENERATEUR D'INVENTAIRE DE DOSSIER
echo ========================================================
echo.

set /p "TARGET_DIR=Entrez le chemin du dossier (laisser vide pour le dossier courant) : "
if "%TARGET_DIR%"=="" set "TARGET_DIR=%CD%"

if not exist "%TARGET_DIR%" (
    echo [ERREUR] Le dossier specifie n'existe pas : %TARGET_DIR%
    pause
    exit /b 1
)

set "OUTPUT_FILE=%CD%\inventaire_fichiers.txt"
echo [*] Analyse en cours de : %TARGET_DIR%...
echo ======================================================== > "%OUTPUT_FILE%"
echo   INVENTAIRE DU DOSSIER : %TARGET_DIR% >> "%OUTPUT_FILE%"
echo   DATE : %DATE% %TIME% >> "%OUTPUT_FILE%"
echo ======================================================== >> "%OUTPUT_FILE%"
echo. >> "%OUTPUT_FILE%"

:: Arborescence avec tailles
dir "%TARGET_DIR%" /s /b /a:-d >> "%OUTPUT_FILE%"

echo. >> "%OUTPUT_FILE%"
echo ======================================================== >> "%OUTPUT_FILE%"
echo [OK] Inventaire termine ! Fichier genere : %OUTPUT_FILE%
notepad "%OUTPUT_FILE%"