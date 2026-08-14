@echo off
setlocal

echo ============================================
echo  SEO Event Tracker -- Windows Build Script
echo ============================================
echo.

:: Move to project root (one level up from windows-app\)
cd /d "%~dp0.."

echo [1/3] Installing build dependencies...
pip install pyinstaller --quiet
if %errorlevel% neq 0 (
    echo ERROR: pip install failed. Make sure Python is on your PATH.
    pause
    exit /b 1
)

echo [2/3] Installing app dependencies...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo ERROR: requirements.txt install failed.
    pause
    exit /b 1
)

echo [3/3] Building .exe with PyInstaller...
pyinstaller windows-app\SEO-Event-Tracker.spec --clean --noconfirm
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller build failed. See output above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  BUILD SUCCESSFUL
echo  Output: dist\SEO-Event-Tracker.exe
echo ============================================
echo.
echo NOTE: Users must run "playwright install chromium" once
echo       before screenshot capture will work.
echo.
pause
