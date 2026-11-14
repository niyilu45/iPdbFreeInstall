#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -n "${IPDB_PYTHON:-}" ]; then
    python_bin=$IPDB_PYTHON
elif command -v python3 >/dev/null 2>&1; then
    python_bin=python3
elif command -v python >/dev/null 2>&1; then
    python_bin=python
else
    printf '%s\n' 'Python was not found. Set IPDB_PYTHON to an existing interpreter.' >&2
    exit 127
fi
exec "$python_bin" "$script_dir/portable_ipdb.py" "$@"
