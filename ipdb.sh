#!/bin/sh
set -eu

# Public launches must not inherit another debugger session bridge.
unset IPDB_VIM_STATE IPDB_VIM_READY IPDB_VIM_CLOSE IPDB_VIM_CLOSED IPDB_TMUX_BIN IPDB_TMUX_SOCKET

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -n "${IPDB_PYTHON:-}" ]; then
    exec "$IPDB_PYTHON" "$script_dir/portable_ipdb.py" "$@"
fi

FindWindowsCommand() (
    # The WindowsApps execution aliases look executable to MSYS but may fail
    # with Permission denied. Only filter PATH for interpreter discovery.
    IFS=:
    set -f
    search_path=
    for directory in ${PATH-}; do
        case "${directory%/}" in
            ''|*/[Mm]icrosoft/[Ww]indows[Aa]pps) continue ;;
        esac
        search_path=${search_path:+$search_path:}$directory
    done
    [ -n "$search_path" ] || exit 1
    PATH=$search_path
    for name do
        resolved=$(command -v "$name") || continue
        if [ -f "$resolved" ] && [ -x "$resolved" ]; then
            printf '%s\n' "$resolved"
            exit 0
        fi
    done
    exit 1
)

case "$(uname -s 2>/dev/null || :)" in
    MINGW*|MSYS*|CYGWIN*)
        if python_bin=$(FindWindowsCommand python python3); then
            exec "$python_bin" "$script_dir/portable_ipdb.py" "$@"
        fi
        if python_launcher=$(FindWindowsCommand py); then
            exec "$python_launcher" -3 "$script_dir/portable_ipdb.py" "$@"
        fi
        printf '%s\n' 'Windows Python was not found. Add an installed python.exe to PATH or set IPDB_PYTHON to its full path.' >&2
        exit 127
        ;;
esac

if command -v python3 >/dev/null 2>&1; then
    python_bin=python3
elif command -v python >/dev/null 2>&1; then
    python_bin=python
else
    printf '%s\n' 'Python was not found. Set IPDB_PYTHON to an existing interpreter.' >&2
    exit 127
fi
exec "$python_bin" "$script_dir/portable_ipdb.py" "$@"
