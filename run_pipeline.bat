@echo off
REM ============================================================
REM  WeatherLLM scheduled pipeline run
REM  Invoked by Windows Task Scheduler, running as SYSTEM.
REM  Set "Start in" to this folder in the task's Action tab.
REM ============================================================

cd /d "%~dp0"

REM ------------------------------------------------------------
REM  Keep the model cache inside the project, not in a user profile.
REM
REM  The task runs as SYSTEM, whose %USERPROFILE% is
REM  C:\Windows\System32\config\systemprofile - not yours. Without
REM  this, sentence-transformers would re-download the ~90 MB MiniLM
REM  model into that profile on the first scheduled run and keep a
REM  second copy on disk. Pinning the cache here means every account
REM  shares one copy and runs stay reproducible.
REM
REM  .cache/ is gitignored.
REM ------------------------------------------------------------

set "HF_HOME=%~dp0.cache\huggingface"
set "SENTENCE_TRANSFORMERS_HOME=%~dp0.cache\sentence-transformers"

if not exist "%~dp0.cache" mkdir "%~dp0.cache"

REM ------------------------------------------------------------
REM  Use the project virtualenv directly rather than activating it.
REM  Calling python.exe by absolute path is what makes this work
REM  under SYSTEM, which does not inherit your PATH.
REM ------------------------------------------------------------

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

"%PY%" src\pipeline.py

set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
    echo.
    echo Pipeline failed with exit code %RC% - see logs\ for details.
)

exit /b %RC%
