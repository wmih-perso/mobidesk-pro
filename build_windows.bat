@echo off
setlocal

echo ============================================
echo   MobiDesk Pro - Generation de l'executable
echo ============================================

if not exist ".venv" (
    echo Creation de l'environnement virtuel...
    py -3.12 -m venv .venv
)

call .venv\Scripts\activate.bat

echo Installation des dependances...
pip install -r requirements.txt --quiet

echo Execution des tests...
pytest -q
if errorlevel 1 (
    echo.
    echo Des tests ont echoue. Build annule.
    exit /b 1
)

echo Nettoyage des builds precedents...
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul

echo Generation de MobiDeskPro.exe...
pyinstaller mobidesk.spec --noconfirm

if exist "dist\MobiDeskPro.exe" (
    echo.
    echo ============================================
    echo   Reussi : dist\MobiDeskPro.exe
    echo ============================================
) else (
    echo.
    echo Echec de la generation.
    exit /b 1
)

endlocal
