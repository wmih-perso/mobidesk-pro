@echo off
setlocal

echo ============================================
echo   MobiDesk Pro - Generation de l'installeur
echo ============================================

if not exist "dist\MobiDeskPro.exe" (
    echo.
    echo dist\MobiDeskPro.exe introuvable - lancer build_windows.bat d'abord.
    exit /b 1
)

set "ISCC=ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"

for /f "delims=" %%v in ('.venv\Scripts\python.exe -c "from app.version import APP_VERSION; print(APP_VERSION)"') do set "RAWVER=%%v"

echo.
echo Compilation de l'installeur pour la version %RAWVER%...
"%ISCC%" /DAppVersion=%RAWVER% installer.iss

if exist "dist\MobiDeskProSetup.exe" (
    echo.
    echo ============================================
    echo   Reussi : dist\MobiDeskProSetup.exe
    echo ============================================
) else (
    echo.
    echo Echec de la generation de l'installeur.
    exit /b 1
)

endlocal
