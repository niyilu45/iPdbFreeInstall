@echo off
setlocal
set "IPDB_VIM_STATE="
set "IPDB_VIM_READY="
set "IPDB_VIM_CLOSE="
set "IPDB_VIM_CLOSED="
set "IPDB_TMUX_BIN="
set "IPDB_TMUX_SOCKET="
if defined IPDB_PYTHON (
    "%IPDB_PYTHON%" "%~dp0portable_ipdb.py" --source-vim %*
) else (
    python "%~dp0portable_ipdb.py" --source-vim %*
)
exit /b %errorlevel%
