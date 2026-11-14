@echo off
setlocal
if defined IPDB_PYTHON (
    "%IPDB_PYTHON%" "%~dp0portable_ipdb.py" %*
) else (
    python "%~dp0portable_ipdb.py" %*
)
