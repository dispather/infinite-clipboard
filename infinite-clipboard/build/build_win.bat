@echo off
REM Self-redirect: when launched from explorer (double-click), capture
REM ALL output to a log file so the failure point is preserved even if
REM the cmd window closes. The wrapper re-invokes itself with _inner arg.

if "%~1"=="_inner" goto :main

REM -- Wrapper: redirect stdout+stderr to log, then tail it after --
if not defined IC_BUILD_DIR set "IC_BUILD_DIR=%LOCALAPPDATA%\InfiniteClipboard-Build"
if not exist "%IC_BUILD_DIR%" mkdir "%IC_BUILD_DIR%" 2>nul
set "BUILD_LOG=%IC_BUILD_DIR%\build_win.log"

call "%~f0" _inner > "%BUILD_LOG%" 2>&1
set "RC=%ERRORLEVEL%"

REM Show the log to the user (works even if main body crashed)
echo ========================================
echo   Build log (also saved at: %BUILD_LOG%)
echo ========================================
type "%BUILD_LOG%"
echo.
echo ========================================
if %RC% neq 0 (
    echo   BUILD FAILED  ^(exit code %RC%^)
) else (
    echo   BUILD OK
)
echo ========================================
pause
exit /b %RC%

:main
REM -- Actual build steps (output goes to log via wrapper redirect) --

echo ========================================
echo   Infinite Clipboard v2 - Windows Build
echo ========================================

cd /d "%~dp0\.."

REM Build outputs MUST live outside the NextCloud sync folder.
REM   1. Avoid sync churn / accidental upload of large dist payloads
REM   2. Reduce risk of antivirus quarantine on synced unsigned .exe
REM   3. Keep the spec-based build self-contained (trap #16)
REM Override with: set IC_BUILD_DIR=D:\my\path

echo Output dir: %IC_BUILD_DIR%
echo Working dir: %CD%
echo.

REM IMPORTANT: pyenv-win installs `pip` and `pyinstaller` as .bat shims.
REM Calling another .bat from a .bat WITHOUT `call` transfers control and
REM never returns - the parent .bat silently exits with code 0 after the
REM child finishes. This is a classic cmd quirk. Always prefix with `call`.

echo [1/4] Installing dependencies (pywin32 etc.) ...
call pip install -r requirements_win.txt
if errorlevel 1 goto :fail_deps

echo.
echo [2/4] Installing PyInstaller ...
call pip install pyinstaller
if errorlevel 1 goto :fail_pyinstaller

echo.
echo [3/4] Building (spec-based) ...
call pyinstaller --clean --noconfirm --distpath "%IC_BUILD_DIR%\dist" --workpath "%IC_BUILD_DIR%\build" build\infinite-clipboard.spec
if errorlevel 1 goto :fail_build

REM Extract version from version.py for the Inno Setup AppVersion symbol.
REM Fallback: leave undefined and let installer.iss use its #ifndef default.
set "APP_VERSION="
for /f "usebackq delims=" %%v in (`python -c "from version import __version__; print(__version__)"`) do set "APP_VERSION=%%v"

echo.
echo [4/4] Building Inno Setup installer ...
set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"      set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe"      set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo [SKIP] ISCC.exe not found. To enable installer build, install Inno Setup:
    echo        winget install JRSoftware.InnoSetup
    echo        ^(or: choco install innosetup -y^)
    echo PyInstaller dist is ready - installer step skipped.
    goto :ok_no_installer
)

set "INSTALLER_OUT=%IC_BUILD_DIR%\installer"
if not exist "%INSTALLER_OUT%" mkdir "%INSTALLER_OUT%"
if defined APP_VERSION (
    "%ISCC%" /DMyAppVersion=%APP_VERSION% /Q build\installer.iss
) else (
    "%ISCC%" /Q build\installer.iss
)
if errorlevel 1 goto :fail_installer

echo.
echo ========================================
echo   Build complete (with installer).
echo ========================================
echo Dist:      "%IC_BUILD_DIR%\dist\Infinite Clipboard\"
echo Run:       "%IC_BUILD_DIR%\dist\Infinite Clipboard\Infinite Clipboard.exe"
echo Installer: "%INSTALLER_OUT%\infinite-clipboard-setup-%APP_VERSION%.exe"
exit /b 0

:ok_no_installer
echo.
echo ========================================
echo   Build complete (dist only, installer skipped).
echo ========================================
echo Dist: "%IC_BUILD_DIR%\dist\Infinite Clipboard\"
echo Run:  "%IC_BUILD_DIR%\dist\Infinite Clipboard\Infinite Clipboard.exe"
exit /b 0

:fail_deps
echo [ERROR] pip install dependencies failed.
exit /b 1

:fail_pyinstaller
echo [ERROR] pip install pyinstaller failed.
exit /b 1

:fail_build
echo [ERROR] PyInstaller build failed.
exit /b 1

:fail_installer
echo [ERROR] Inno Setup compile failed.
exit /b 1
