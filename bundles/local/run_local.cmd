@echo off
rem simcompare local PC runner (Windows). Double-click to run. No install, no PATH or registry change, no admin rights.
rem Runs simcompare_job_local.py in this folder with a Python that has numpy and Pillow (Anaconda base etc).
rem Edit BASE_DIR and STEPS inside simcompare_job_local.py when needed. See README_local.md.
setlocal EnableExtensions

set "PY="
rem 1) SIMCOMPARE_PYTHON  2) first python on PATH  3) common Anaconda / Miniconda folders
if defined SIMCOMPARE_PYTHON if exist "%SIMCOMPARE_PYTHON%" set "PY=%SIMCOMPARE_PYTHON%"
if not defined PY for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
if not defined PY if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if not defined PY if exist "%USERPROFILE%\miniconda3\python.exe" set "PY=%USERPROFILE%\miniconda3\python.exe"
if not defined PY if exist "%ProgramData%\anaconda3\python.exe" set "PY=%ProgramData%\anaconda3\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\anaconda3\python.exe" set "PY=%LOCALAPPDATA%\anaconda3\python.exe"

if not defined PY (
  echo [simcompare] Python was not found.
  echo Install Anaconda, or set SIMCOMPARE_PYTHON to the full path of python.exe, then run again.
  pause
  exit /b 1
)

"%PY%" -c "import numpy, PIL" >nul 2>nul
if errorlevel 1 (
  echo [simcompare] A Python with numpy and Pillow is required. Use Anaconda base or similar.
  echo Python used: "%PY%"
  echo Set SIMCOMPARE_PYTHON to another python.exe if needed.
  pause
  exit /b 2
)

echo [simcompare] Python: %PY%
"%PY%" -X utf8 "%~dp0simcompare_job_local.py"
set "RC=%ERRORLEVEL%"

echo.
echo [simcompare] exit code: %RC%
echo [simcompare] log: runs\simcompare_job_local.log under BASE_DIR (default: this folder, %~dp0)
pause
exit /b %RC%
