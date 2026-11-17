@echo off
REM Collects information about the Python / Qt / MSVC runtime setup into diagnose.txt
setlocal
cd /d "%~dp0"
set OUT=%~dp0diagnose.txt
echo DEMMerge diagnostics > "%OUT%"
echo ===================== >> "%OUT%"
ver >> "%OUT%"
if exist .venv\Scripts\python.exe (
  echo. >> "%OUT%" & echo [venv python] >> "%OUT%"
  .venv\Scripts\python.exe -c "import sys;print(sys.version);print(sys.base_prefix)" >> "%OUT%" 2>&1
  .venv\Scripts\python.exe -c "import PySide6;print('PySide6',PySide6.__version__)" >> "%OUT%" 2>&1
  .venv\Scripts\python.exe -c "from PySide6 import QtCore;print('QtCore import OK',QtCore.qVersion())" >> "%OUT%" 2>&1
  .venv\Scripts\python.exe -c "import PyInstaller;print('PyInstaller',PyInstaller.__version__)" >> "%OUT%" 2>&1
)
echo. >> "%OUT%" & echo [Qt / runtime DLLs on PATH] >> "%OUT%"
where Qt6Core.dll >> "%OUT%" 2>&1
where Qt5Core.dll >> "%OUT%" 2>&1
where msvcp140.dll >> "%OUT%" 2>&1
echo. >> "%OUT%" & echo [Qt environment variables] >> "%OUT%"
set QT >> "%OUT%" 2>&1
echo. >> "%OUT%" & echo [msvcp140.dll versions] >> "%OUT%"
powershell -NoProfile -Command ^
  "$f=@('C:\Windows\System32\msvcp140.dll','dist\DEMMerge\_internal\msvcp140.dll','dist\DEMMerge\_internal\PySide6\msvcp140.dll','.venv\Lib\site-packages\PySide6\msvcp140.dll');" ^
  "foreach($p in $f){ if(Test-Path $p){ '{0}  {1}' -f (Get-Item $p).VersionInfo.FileVersion,$p } else { 'missing  '+$p } }" >> "%OUT%" 2>&1
echo. >> "%OUT%" & echo [DLLs at _internal top level] >> "%OUT%"
powershell -NoProfile -Command ^
  "Get-ChildItem dist\DEMMerge\_internal\*.dll -ErrorAction SilentlyContinue | ForEach-Object { '{0,-18} {1}' -f $_.VersionInfo.FileVersion,$_.Name }" >> "%OUT%" 2>&1
echo. >> "%OUT%" & echo [python3.dll] >> "%OUT%"
powershell -NoProfile -Command ^
  "foreach($p in @('dist\DEMMerge\_internal\python3.dll',(Join-Path (& .venv\Scripts\python.exe -c 'import sys;print(sys.base_prefix)') 'python3.dll'))){ if(Test-Path $p){ '{0}  {1}' -f (Get-FileHash $p -Algorithm MD5).Hash,$p } }" >> "%OUT%" 2>&1
if exist build_audit.txt (
  echo. >> "%OUT%" & echo [build audit - foreign DLLs] >> "%OUT%"
  findstr /b /c:"REPLACED" /c:"FOREIGN" /c:"No foreign" build_audit.txt >> "%OUT%"
)
if exist dist\DEMMerge\DEMMerge.exe (
  echo. >> "%OUT%" & echo [selftest] >> "%OUT%"
  dist\DEMMerge\DEMMerge.exe --selftest
  type dist\DEMMerge\selftest.log >> "%OUT%" 2>&1
)
echo Written: %OUT%
notepad "%OUT%"
