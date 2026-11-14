# iPdbFreeInstall

Portable ipdb launcher for Windows with an existing Python 3.9 interpreter.
Python itself is not bundled.

## Current status

The launcher is ready, but this repository does NOT currently include ipdb
or its dependencies. Dependency downloads failed in the preparation environment.
A fresh clone is therefore not yet an offline-ready portable debugger.

## Prepare the portable folder

On a connected preparation machine, run from this directory:

    python -m pip install --target vendor --no-compile -r requirements.txt

Then copy this entire folder, including vendor, to the destination computer.
No pip installation is needed on the destination computer.
Third-party packages retain their own licenses and metadata in vendor.

## Run

From a terminal:

    ipdb.cmd path\to\script.py [arguments]

Set IPDB_PYTHON to a python.exe path to select a specific interpreter.
Otherwise the launcher uses python from PATH.

For an in-code breakpoint, add this directory to sys.path, then use:

    import portable_ipdb
    import ipdb
    ipdb.set_trace()

Commands: n (next), s (step), c (continue), p expression, q (quit).

## Validation

The launcher syntax has been checked. Debugger execution has not been verified
because the third-party dependencies could not be downloaded.

Sources: https://pypi.org/project/ipdb/ and https://github.com/gotcha/ipdb
