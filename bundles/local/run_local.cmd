@echo off
rem simcompare local PC runner (Windows). Double-click to run. No install, no PATH or registry change, no admin rights.
rem Runs simcompare_job_local.py in this folder with the first python.exe that can import numpy and Pillow.
rem Edit BASE_DIR and STEPS inside simcompare_job_local.py when needed. See README_local.md.
setlocal EnableExtensions

set "PY="
set "ANY="
rem Candidates, in order: 1) SIMCOMPARE_PYTHON  2) every python.exe on PATH (Store alias under \WindowsApps\ is skipped)  3) common Anaconda / Miniconda folders
if defined SIMCOMPARE_PYTHON if not exist "%SIMCOMPARE_PYTHON%" echo [simcompare] Warning: SIMCOMPARE_PYTHON does not exist, ignored: "%SIMCOMPARE_PYTHON%"
if defined SIMCOMPARE_PYTHON if exist "%SIMCOMPARE_PYTHON%" call :try "%SIMCOMPARE_PYTHON%"
if not defined PY for /f "delims=" %%P in ('where python.exe 2^>nul') do call :try "%%P"
if not defined PY call :try "%USERPROFILE%\anaconda3\python.exe"
if not defined PY call :try "%USERPROFILE%\miniconda3\python.exe"
if not defined PY call :try "%ProgramData%\anaconda3\python.exe"
if not defined PY call :try "%LOCALAPPDATA%\anaconda3\python.exe"
if not defined PY call :try "%ProgramData%\miniconda3\python.exe"

if defined PY goto :run
if not defined ANY goto :nopython
echo [simcompare] A Python with numpy and Pillow was not found (candidates listed above).
echo Use Anaconda base or similar. Set SIMCOMPARE_PYTHON to the full path of that python.exe, then run again.
pause
exit /b 2

:nopython
echo [simcompare] Python was not found.
echo Install Anaconda, or set SIMCOMPARE_PYTHON to the full path of python.exe, then run again.
pause
exit /b 1

:run
echo [simcompare] Python: "%PY%"
"%PY%" -X utf8 "%~dp0simcompare_job_local.py"
set "RC=%ERRORLEVEL%"

echo.
echo [simcompare] exit code: %RC%
echo [simcompare] log: runs\simcompare_job_local.log under BASE_DIR (default: this folder "%~dp0")
pause
exit /b %RC%

:try
rem Test one candidate: keep it in PY only when it is a python.exe that can import numpy and Pillow.
if defined PY exit /b 0
if /i not "%~x1"==".exe" exit /b 0
set "CAND=%~1"
if not "%CAND:\WindowsApps\=%"=="%CAND%" exit /b 0
if not exist "%~1" exit /b 0
set "ANY=1"
"%~1" -c "import numpy, PIL" >nul 2>nul
if errorlevel 1 echo [simcompare] no numpy/Pillow, skipped: "%~1" & exit /b 0
set "PY=%~1"
exit /b 0
