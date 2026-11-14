# iPdbFreeInstall

Offline ipdb bundles for Linux Python 3.6, 3.7, 3.9, and 3.10.
Python itself is not included. Use the interpreter already on your machine.

## Download and run

Download the matching ZIP from the repository dist directory and extract it.
For Python 3.7, for example:

    unzip ipdb-linux-py37.zip
    cd ipdb-linux-py37
    IPDB_PYTHON=python3.7 sh ipdb.sh /path/to/your_script.py

No pip installation or network connection is needed on the destination.
Without IPDB_PYTHON, the launcher selects python3, then python from PATH.
Use a matching interpreter; each archive contains one version's dependencies.
The Linux bundles contain pure-Python wheels and include pexpect/ptyprocess.

| Python | Archive | IPython |
| --- | --- | --- |
| 3.6 | dist/ipdb-linux-py36.zip | 7.16.3 |
| 3.7 | dist/ipdb-linux-py37.zip | 7.34.0 |
| 3.9 | dist/ipdb-linux-py39.zip | 8.18.1 |
| 3.10 | dist/ipdb-linux-py310.zip | 8.18.1 |

All bundles include ipdb 0.13.13, dependency metadata, upstream licenses,
requirements.lock, and wheel-sha256.json. SHA256SUMS covers the archives.

## In-code breakpoints

Add the extracted bundle directory to sys.path, then:

    import portable_ipdb
    import ipdb
    ipdb.set_trace()

Commands: n (next), s (step), c (continue), p expression, q (quit).

## Prepare from source

A source checkout alone does not activate bundled dependencies. Extract the
matching archive, or on a connected machine run the matching interpreter:

    python3.7 prepare.py

This writes vendor/linux-py37 without changing global Python packages.
On Windows, prepare with your Windows interpreter and run ipdb.cmd.
Linux bundles are not Windows dependency bundles.

## Rebuild Linux archives

On the build machine, Python 3.9 and pip are required:

    python build_linux.py

The builder evaluates dependency markers for each target Linux/Python
combination, checks Requires-Python and dependency version constraints,
and includes only pure-Python wheels. It downloads from the HTTPS Aliyun
mirror. Build artifacts are stored in build/ and archives in dist/.

## Validation limits

Archive integrity and target dependency metadata are checked during build.
Native Linux execution and Python 3.6/3.7/3.10 runtime tests have not been
performed in the Windows build environment. Report the full traceback,
Python version, and archive name if a target runtime issue occurs.

Upstream: https://pypi.org/project/ipdb/ and https://github.com/gotcha/ipdb
