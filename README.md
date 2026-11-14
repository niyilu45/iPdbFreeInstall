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

A complete source checkout automatically extracts the matching archive from
dist/ on first launch, without pip or internet. It needs write permission
for vendor/. Alternatively, on a connected preparation machine run:

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

## Split-screen mode (Linux)

    IPDB_PYTHON=python3.7 sh ipdb.sh --tui /path/to/main.py

The top half follows the selected source frame. The bottom half contains
ipdb commands and live program stdout/stderr. Press Ctrl-X, then A to toggle
the source pane (Ctrl-X Ctrl-A also works). Ctrl-L redraws the display.
Normal n/s/c/p/b/u/d/q commands remain available. Ctrl-C goes to the debuggee.
Resizing the terminal adjusts both panes. Below 10 terminal rows, the source
pane is hidden. --plain selects the original scrolling debugger.

This mode requires an interactive Linux ANSI terminal, such as xterm or a
normal SSH terminal. Redirected input/output and TERM=dumb are not supported.
The debuggee runs in a pseudo-terminal with simple prompts. Full-screen
programs that manipulate terminal cursor positions are not supported.
The source pane shows a window around the current line; unavailable source
(e.g. dynamically generated code) leaves it blank. Switching layout clears
the visible command area; it is not a persistent output log.

To stop at an in-code ipdb.set_trace(), launch the script using --tui and
continue to the desired breakpoint. Importing portable_ipdb alone does not
start the split-screen interface.

Run python -m unittest test_split.py on Linux with the matching dependencies
prepared in vendor. The Linux PTY test checks source display, stepping,
variable inspection, toggling, program output, and debugger exit.

## PuDB mode

The offline archives now include PuDB: version 2020.1 for Python 3.6 and
2022.1 for Python 3.7/3.9/3.10, with Urwid 2.1.2 in pure-Python mode.
No additional installation is required.

    IPDB_PYTHON=python3.7 sh ipdb.sh --pudb /path/to/main.py

Or start --tui, stop at an empty ipdb prompt, then press Ctrl-X followed
by S (Ctrl-X Ctrl-S also works). This hands the current paused session to
PuDB without restarting the script. Use PuDB's n/s/c/b and ? commands.
The PuDB mode remains active for subsequent stops; this shortcut is a
one-way handoff, not a toggle back to ipdb. Ctrl-X A is available only in
the ipdb split view. While the program is running, pause it before using
the PuDB shortcut. Existing ipdb breakpoints are shared with PuDB.

The terminal handoff remains pending native Linux verification. The local
checks cover key decoding and package dependency integrity.

## GDB-style Python commands

These commands work in the ipdb CLI and --tui modes. They are not a full
GDB emulator: expressions are Python expressions, not C/C++ expressions.
PuDB retains its own command/key interface.

| Command | Behavior |
| --- | --- |
| bt / backtrace | List the stack, innermost frame numbered #0 |
| bt 3 | Show the three innermost frames |
| frame 1 / f 1 | Select frame #1; p and info locals use that frame |
| up / u | Move toward the caller; use frame N to select any frame |
| print expression / p expression | Evaluate a Python expression |
| next / n, step / s, continue / c | Execute or resume |
| finish / fin | Run until the current function returns |
| run / r | Restart the script from the beginning |
| break file.py:42 if count > 10 | Conditional breakpoint |
| b 42 if name == "target" | Conditional breakpoint in current file |
| condition 1 count == 20 | Change breakpoint #1's condition |
| condition 1 | Remove breakpoint #1's condition |
| info breakpoints / i b | List breakpoints |
| disable 1 / enable 1 | Disable or enable breakpoint #1 |
| delete 1 | Delete breakpoint #1 |
| info locals / info args | Inspect the selected frame |
| set var count = 5 | Assign a simple variable name (CPython) |

The r alias now means run, as in GDB. Use finish or return for pdb's old r
behavior. Existing Python breakpoint syntax with a comma also works.

## Linux checkpoints (experimental)

    checkpoint
    info checkpoints
    next
    print value
    restart 1
    delete checkpoint 2

A checkpoint is a frozen fork of the paused Python process. Restarting it
restores process memory and the execution stack at the saved stop. It does
not merely copy a variables dictionary. A stable supervisor keeps the
terminal session alive while the active process changes.

Limits:

- Linux CLI launch only, with a single Python/native thread and at most
  eight live snapshots. Native worker threads (for example from numerical
  libraries) also cause checkpoint creation to be refused.
- IPython's background history writer is stopped before the first fork;
  persistent IPython history is then disabled for this session.
- A restored checkpoint is consumed. Newer checkpoints are discarded;
  earlier checkpoints remain. Create another checkpoint to save again.
- Python debugger state, including breakpoints, is restored with the process.
  This differs from GDB, which keeps debugger state separate.
- Files, file offsets, sockets, subprocesses, shared memory, external writes,
  and network effects are NOT restored. Use this for single-process,
  in-memory calculations; it is not transactional rollback.
- Process IDs change. Native extension fork-safety is not guaranteed.
- Use quit to end the session and release snapshots. Delete all live
  checkpoints before switching from ipdb to PuDB.
- Checkpoints cannot be created by merely importing portable_ipdb into an
  independently launched Python process; launch via ipdb.sh instead.

Local tests cover bt/frame numbering, conditional breakpoint execution,
variable assignment, and command translation. Linux fork restoration and
terminal integration tests are included in CI, but have not been verified
in the Windows development environment. Treat checkpoints as experimental
until those tests pass on the target Linux/Python combination.

## Automatic expression display

    disp count
    display values[0]
    next
    info display
    disable display 1
    enable display 1
    undisplay 1
    delete display 2

Each expression gets a stable numeric ID and is evaluated immediately and
at every ipdb stop, even if its value has not changed. Values are also
refreshed when selecting a frame. Evaluation uses the currently displayed
frame's Python globals and locals. An unavailable variable or another
exception is shown inline; the expression stays registered and retries
on later stops. Mutable objects are evaluated afresh rather than compared
with a saved object reference.

Bare display re-evaluates enabled items. info display lists all IDs and
enabled states. enable/disable display and undisplay accept multiple IDs
separated by spaces; omitting IDs applies to all displays. delete display
is an alias for undisplay. Invalid IDs leave the whole requested operation
unchanged. Display IDs are independent of breakpoint IDs and are not reused.

Expressions are Python expressions and can have side effects; prefer
read-only expressions. GDB slash formats (display/x) are not implemented;
use disp hex(value) instead. These commands apply to ipdb and --tui;
PuDB continues to use its own variable display. Checkpoint restoration also
restores the display configuration saved with that checkpoint.


## Command abbreviations

The GDB-compatible command set and its info/delete/enable/disable
subcommands now accept unambiguous prefixes. Explicit aliases take priority.
Native ipdb commands outside this set remain available by their exact names.

| Input | Expanded command |
| --- | --- |
| d 1 / del 1 | delete breakpoint 1 |
| d disp 1 | delete display 1 |
| del check 2 | delete checkpoint 2 |
| dis disp 1 | disable display 1 |
| en disp 1 | enable display 1 |
| und 1 | undisplay 1 |
| i disp | info display |
| i b | info breakpoints |
| i check | info checkpoints |
| cond 1 value > 3 | condition 1 value > 3 |
| check | checkpoint |
| res 1 | restart 1 |
| set v count = 3 | set variable count = 3 |

The down command is disabled. d now means delete, not down. Use frame 0,
frame 1, etc. for direct frame selection. Existing bt/f/i/b/n/s/c/p/q/r,
fin, disp, and u aliases remain supported.

Ambiguous prefixes are rejected with a candidate list: re matches restart
and return, for example. They never silently choose a command or repeat the
previous one. dis is an explicit alias for disable; disp selects display.
Prefix expansion only affects command words, not Python expression text.
Use ! to explicitly execute Python when a variable name overlaps a command.


## Diagnose missing ipdb

Python 3.9.12 uses the py39 archive; the patch version does not affect selection.
Always launch through ipdb.sh or portable_ipdb.py, not python -m ipdb:

    IPDB_PYTHON=/path/to/python3.9 sh ipdb.sh --check
    IPDB_PYTHON=/path/to/python3.9 sh ipdb.sh --tui /path/to/main.py

--check prints the interpreter, Python version, dependency directory, and
actual imported ipdb location. A complete git checkout now extracts its
matching dist archive automatically on first launch. Extracted ZIP bundles
already contain vendor and do not need this step. Preserve the entire bundle.
Wrong interpreter versions or incomplete bundles produce an explicit message
listing the expected package and available dependency folders.

The vendor search path is kept separate from sys.path[0], which ipdb replaces
with the debugged script directory. This prevents lazy imports from losing
access to bundled dependencies after execution begins.
