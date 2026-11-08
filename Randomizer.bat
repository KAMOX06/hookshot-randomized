@echo off
rem Opens the Hookshot Randomized launcher (tools\gui.py). Needs Python 3.14 (its compression.zstd module).
rem Looks for: uv's Python 3.14, then the py launcher, then pythonw.exe on PATH.
setlocal
set "PYW="
for /d %%D in ("%APPDATA%\uv\python\cpython-3.14*") do if exist "%%D\pythonw.exe" set "PYW=%%D\pythonw.exe"
if not defined PYW (
    for /f "delims=" %%P in ('py -3.14 -c "import sys; print(sys.executable)" 2^>nul') do set "PYW=%%~dpPpythonw.exe"
)
if not defined PYW (
    for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not defined PYW set "PYW=%%P"
)
if not defined PYW goto nopython
if not exist "%PYW%" goto nopython
"%PYW%" -I -c "import compression.zstd" 2>nul || goto nopython
start "" "%PYW%" -I "%~dp0tools\gui.py"
exit /b 0

:nopython
echo Python 3.14 or newer was not found.
echo Install it from python.org, or with uv:  uv python install 3.14
pause
exit /b 1
