@echo off
REM ============================================================
REM  Build DEMMerge.exe on Windows
REM    build_exe.bat           -> folder build  dist\DEMMerge\DEMMerge.exe  (recommended)
REM    build_exe.bat onefile   -> single file   dist\DEMMerge.exe
REM  Requires Python 3.10-3.12 (64-bit) on PATH.
REM ============================================================
setlocal
cd /d "%~dp0"

where py >nul 2>nul && (set PY=py -3) || (set PY=python)

if not exist .venv (
    echo Creating virtual environment...
    %PY% -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error

echo Installing dependencies...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller || goto :error

if /i "%1"=="onefile" (set DEMMERGE_ONEFILE=1) else (set DEMMERGE_ONEFILE=0)

REM Build with a clean PATH so DLLs from Anaconda, GMT, QGIS, etc. cannot leak into the exe
for /f "delims=" %%i in ('python -c "import sys;print(sys.base_prefix)"') do set PYBASE=%%i
set "PATH=%CD%\.venv\Scripts;%PYBASE%;%PYBASE%\DLLs;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0"
set QT_PLUGIN_PATH=
set QT_QPA_PLATFORM_PLUGIN_PATH=
set PYTHONPATH=
set PYTHONHOME=
echo Build PATH: %PATH%

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo Building...
pyinstaller --noconfirm --clean DEMMerge.spec || goto :error

echo Running self-test...
if "%DEMMERGE_ONEFILE%"=="1" (
    dist\DEMMerge.exe --selftest
) else (
    dist\DEMMerge\DEMMerge.exe --selftest
)
if errorlevel 1 (
    echo.
    echo *** Self-test FAILED - see selftest.log next to the exe ***
    goto :error
)

REM The build\ folder only holds intermediate files; its DEMMerge.exe cannot run. Remove it to avoid confusion.
if exist build rmdir /s /q build

echo.
if "%DEMMERGE_ONEFILE%"=="1" (
    echo Done: dist\DEMMerge.exe
    explorer /select,"%cd%\dist\DEMMerge.exe"
) else (
    echo Done: dist\DEMMerge\DEMMerge.exe
    echo Copy the WHOLE dist\DEMMerge folder ^(exe + _internal^) to other PCs; Python is not needed there.
    explorer /select,"%cd%\dist\DEMMerge\DEMMerge.exe"
)
pause
exit /b 0

:error
echo.
echo Build failed.
pause
exit /b 1
