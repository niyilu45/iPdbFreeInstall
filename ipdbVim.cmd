@echo off
setlocal
if defined IPDB_PYTHON (
    "%IPDB_PYTHON%" "%~dp0portable_ipdb.py" --source-vim %*
) else (
    python "%~dp0portable_ipdb.py" --source-vim %*
)
exit /b %errorlevel%
