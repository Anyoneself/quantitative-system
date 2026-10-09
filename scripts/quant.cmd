@echo off
rem Windows counterpart of scripts/quant (POSIX sh): prepends src to PYTHONPATH
rem and dispatches to either the CLI or the web server.
rem
rem   scripts\quant.cmd web
rem   scripts\quant.cmd advise 600519
rem
rem ASCII only on purpose: cmd.exe reads batch files using the OEM code page,
rem not UTF-8, so non-ASCII comments would be mis-parsed on some systems.
rem "python" is used rather than "python3" because on Windows "python3" may
rem resolve to the zero-byte Microsoft Store app-execution-alias stub.
setlocal
for %%I in ("%~dp0..") do set "ROOT_DIR=%%~fI"
if defined PYTHONPATH (
  set "PYTHONPATH=%ROOT_DIR%\src;%PYTHONPATH%"
) else (
  set "PYTHONPATH=%ROOT_DIR%\src"
)

if /i "%~1"=="web" goto web

python -m cli %*
exit /b %errorlevel%

:web
python -m web %2 %3 %4 %5 %6 %7 %8 %9
exit /b %errorlevel%
