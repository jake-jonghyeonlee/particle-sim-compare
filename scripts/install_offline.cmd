@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 goto fail
py -3.12 -c "import platform,struct,sys; assert sys.implementation.name == 'cpython' and sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 and platform.machine().upper() == 'AMD64', 'CPython 3.12 x64 required'"
if errorlevel 1 goto fail
if exist ".venv-cmd\Scripts\python.exe" goto verify_python
if exist ".venv-cmd" goto incomplete_venv
py -3.12 -m venv .venv-cmd
if errorlevel 1 goto fail
:verify_python
.venv-cmd\Scripts\python.exe -c "import platform,struct,sys; assert sys.implementation.name == 'cpython' and sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 and platform.machine().upper() == 'AMD64', 'CPython 3.12 x64 required'"
if errorlevel 1 goto fail
.venv-cmd\Scripts\python.exe verify_windows_bundle.py .
if errorlevel 1 goto fail
.venv-cmd\Scripts\python.exe -m pip --disable-pip-version-check install --no-index --only-binary=:all: --require-hashes --find-links=wheelhouse -r requirements-windows.txt
if errorlevel 1 goto fail
.venv-cmd\Scripts\python.exe -m pip check
if errorlevel 1 goto fail
.venv-cmd\Scripts\python.exe -m simcompare --help
if errorlevel 1 goto fail
echo Offline installation and CLI check passed. Numerical and real-data checks are separate.
exit /b 0
:incomplete_venv
echo Incomplete .venv-cmd exists. Extract the bundle to a new folder; no files were deleted. 1>&2
exit /b 2
:fail
set "failure_code=%errorlevel%"
if "%failure_code%"=="0" set "failure_code=1"
echo Offline installation stopped. See the error above. 1>&2
exit /b %failure_code%
