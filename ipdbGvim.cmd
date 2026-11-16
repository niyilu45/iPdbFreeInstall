@echo off
setlocal
if defined IPDB_PYTHON (
    "%IPDB_PYTHON%" "%~dp0portable_ipdb.py" --source-gvim %*
) else (
    python "%~dp0portable_ipdb.py" --source-gvim %*
)
exit /b %errorlevel%
