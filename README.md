# iPdbFreeInstall

Portable ipdb launcher for Linux and Windows using an existing Python interpreter.
Python itself is not bundled.
The pinned dependency set requires Python 3.9 or newer; Python 3.9 is the
current validation baseline. Newer Python versions have not been verified.

## Current status

The launcher is ready, but this repository does NOT currently include ipdb
or its dependencies. Dependency downloads failed in the preparation environment.
A fresh clone is therefore not yet an offline-ready portable debugger.

## Prepare the portable folder

On a connected preparation machine, run from this directory:

    python prepare.py

On Linux, use python3 instead of python. Prepare dependencies on the same
OS and Python version as the destination: do not reuse a Windows vendor
folder on Linux. No global or system Python packages are modified.

Then copy this entire folder, including vendor, to the destination computer.
No pip installation is needed on the destination computer.
Third-party packages retain their own licenses and metadata in vendor.

## Run

Linux (from any working directory):

    /path/to/iPdbFreeInstall/ipdb.sh path/to/script.py [arguments]

Alternatively, if executable permissions were lost while copying:

    sh /path/to/iPdbFreeInstall/ipdb.sh path/to/script.py [arguments]

The Linux launcher prefers python3, then python from PATH. To select an
existing interpreter, including a virtual environment:

    IPDB_PYTHON=/path/to/venv/bin/python ./ipdb.sh path/to/script.py

Windows:

    ipdb.cmd path\to\script.py [arguments]

Set IPDB_PYTHON to a python.exe path to select a specific interpreter.
Otherwise the Windows launcher uses python from PATH. IPDB_PYTHON must be
an executable path, not a command containing extra arguments.

For an in-code breakpoint, add this directory to sys.path, then use:

    import portable_ipdb
    import ipdb
    ipdb.set_trace()

Commands: n (next), s (step), c (continue), p expression, q (quit).

## Validation

The launcher syntax has been checked. Debugger execution has not been verified
because the third-party dependencies could not be downloaded.

Sources: https://pypi.org/project/ipdb/ and https://github.com/gotcha/ipdb

Linux launcher validation: shell syntax and interpreter forwarding were
checked under Git Bash on Windows using an existing ipdb installation.
Native Linux execution and the bundled dependency set remain unverified.
If vendor is absent, the launcher can also use ipdb already available in
the selected interpreter.

## Python version selection

Run prepare.py using each destination interpreter on a matching OS:

    python3.6 prepare.py
    python3.7 prepare.py
    python3.9 prepare.py
    python3.10 prepare.py

Dependencies are separated under vendor/linux-py36, vendor/linux-py37,
vendor/linux-py39, and vendor/linux-py310 (win32-pyXY on Windows).
The launcher selects the matching folder automatically. Copy the complete
folder after preparation; destination machines do not need pip or internet.

Python 3.6 uses IPython 7.16.3 and Jedi 0.17.2; Python 3.7 uses IPython
7.34.0; Python 3.9/3.10 use IPython 8.18.1. All use ipdb 0.13.13.
These are compatibility targets, not a claim of completed runtime tests.
Only Python 3.9 is available locally; the other interpreters and native
Linux execution still require validation.
