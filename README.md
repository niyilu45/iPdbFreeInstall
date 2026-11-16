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
(e.g. dynamically generated code) leaves it blank. Switching layout preserves command/output history and restores the recent
lines that fit the new terminal area, including the current input and cursor.
Up to 10,000 prior terminal rows are retained in memory during the session.
History is not saved to a file; changing width does not reflow old lines.

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

An expression is registered only after its initial evaluation and repr succeed.
Failures are reported without adding an entry or consuming an ID. The initial
expression is evaluated only once. Each successful expression gets a stable
numeric ID and is evaluated immediately and
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

## Shortcut-enabled startup (Linux)

A normal interactive Linux launch now enables keyboard shortcuts automatically:

    IPDB_PYTHON=python3.9 sh ipdb.sh your_script.py

It starts with the source pane hidden and prints "Shortcuts enabled".
Press Ctrl-X, release, then A to show/hide source. At a paused, empty ipdb
prompt, press Ctrl-X, release, then S to enter PuDB. Ctrl-X Ctrl-A and
Ctrl-X Ctrl-S are also accepted, as are uppercase A/S. Do not type a plus
sign between keys.

--tui enables the same shortcuts and shows source immediately. --plain
explicitly disables the wrapper and shortcuts. Noninteractive pipes,
TERM=dumb, and non-Linux platforms use plain mode by default. If the banner
is absent, confirm that you launched the updated ipdb.sh/portable_ipdb.py,
not python -m ipdb or a script that only imports ipdb. Shortcuts cannot be
installed retroactively into an already-running plain debugger session.
If an SSH client or terminal intercepts a key sequence, configure it to
forward those keys, or launch --tui / --pudb directly.

## Source pane layout

The source pane is enclosed by a complete ASCII border. Its title shows the
file and current line; > marks the current statement. The bottom border
separates source from the command/input and program-output area.

While the source pane is visible, automatic ipdb file/line/context output
is suppressed in the lower area. disp values, program prints, command
results, and errors remain visible. Turning the source pane off restores
ordinary ipdb position output. Explicit bt and where commands remain
available. The shared layout state also follows terminal resizing and
checkpoint restoration; a terminal smaller than 10 rows or 20 columns
hides the source pane and restores ordinary output.

## Vim dark source colors

The source pane uses a Vim-based dark palette with 256-color ANSI output:
keywords, function names, strings, numbers, operators, and comments have
separate colors. Comments and strings use brighter dark-theme colors for
readability. The current statement has a full-width gray CursorLine-style
background and a bright yellow line number/marker. Borders and other line
numbers remain muted. Colors reset at the end of each rendered row and do
not change the command/output pane.

Syntax is tokenized with the bundled Pygments Python lexer using full-file
context, so multiline strings keep their coloring when the visible window
moves. Tokenization is cached until the source text changes. A terminal
with 256-color support gives the intended appearance (for example an SSH
terminal using TERM=xterm-256color).

## Source navigation and command editing

While paused with the source pane visible, press Ctrl-X then O to focus source:

- Up/Down scroll source by one line.
- Left/Right pan source horizontally by eight terminal cells; line numbers
  stay fixed and the current-line highlight stays on the execution line.
- PageUp/PageDown scroll one source page.
- Home returns the viewport to the current execution line and resets horizontal
  scrolling. A new debugger stop or frame selection also restores following.

Browsing only changes the viewport, never the execution line. The complete
source file is available for scrolling, rather than a limited nearby excerpt.
Command input has focus by default in split view. Up/Down recall history,
Left/Right move the command cursor, and Home/End edit the command normally.
Ctrl-X then O toggles source/command focus; the border shows the active focus.
Ctrl-P/Ctrl-N and Ctrl-B/Ctrl-F also remain available while source has focus.
While the program is running or PuDB is active, arrows go to that program.

Both ASCII Backspace (Ctrl-H) and DEL-style Backspace are normalized to the
PTY's configured erase character. Readline binds both to backward deletion.
The child uses the real terminal type and explicitly activates IPython's
simple input mode even if its module was imported before the environment
flag was set. This avoids prompt-toolkit repainting the split terminal.

Navigation, clipping, and key decoding have local regression coverage.
The Linux PTY test also exercises both backspace encodings, but still needs
execution on a native Linux test environment.

## Tab completion

The readline input used by the Linux shortcut/split launcher now completes:

- Command names and aliases: chec<Tab> -> checkpoint, disp<Tab> -> display.
- GDB subcommands: i dis<Tab> -> i display, dis disp<Tab> -> dis display.
- Current-frame variables: p sample_c<Tab> -> p sample_count.
- Object attributes: disp sample_values.ap<Tab> -> disp sample_values.append.
- Expressions in condition, display, print, set-variable, and break-if commands.

Press Tab to extend a unique match or display ambiguous candidates. Candidate
names use the selected frame, so frame changes affect variable completion.
Existing pdb filename completion is retained for break/source where available.
Attribute traversal does not evaluate properties; custom __dir__ methods may
still be called to enumerate attribute names. Internal adapter commands and
the disabled down command are excluded from suggestions.

Tab is forwarded to readline even while arrow keys are browsing source.
PuDB retains its own completion behavior. Interactive Linux --plain and
split modes now use the same readline debugger and completion adapter.

Local completion tests pass. A Tab-completed variable command was also added
to the Linux PTY test, which still requires native Linux execution.


## Consistent input across layouts

Interactive Linux --plain, shortcut-only, and split modes share one readline
input backend: the same prompt, backspace handling, history, completion and
editing bindings. Split mode defaults to COMMAND focus, so normal arrow keys
remain command-editing keys. Ctrl-X O explicitly toggles SOURCE focus for
source scrolling, then switches back to COMMAND focus. Ctrl-X A resets focus
to COMMAND when changing the layout. Tab remains completion in either focus.

Ctrl-L redraws the command line rather than allowing readline to clear the
entire terminal over the source pane. The wrapper separately refreshes its
layout. Plain-mode terminal settings are restored when the debugger exits.

A Linux PTY parity test now runs the same backspace, Tab, cursor-editing and
history commands in --plain and --tui. Native Linux execution remains pending
in the Windows development environment.


### Vim source navigation and mouse

In split view, press Ctrl-W, release it, then press k to focus the upper
source pane or j to focus the lower command pane. Ctrl-W h/l select a pane
to the left/right; with the current top/bottom layout they keep focus unchanged.
Ctrl-X O remains available as a focus toggle. Outside split view Ctrl-W keeps
its normal command-line editing behavior.

With SOURCE focus, h/j/k/l scroll left/down/up/right, gg/G jump to the start/end
of the file, Ctrl-B/F scroll a page, Ctrl-U/D scroll half a page, 0/$ scroll to
the left/right edge, and zz recenters the execution line. This is a read-only
source browser, not a full Vim editor. Return with Ctrl-W j before entering
commands. Existing arrow keys and PageUp/PageDown remain available.

On terminals supporting SGR mouse reporting, the mouse wheel over the source
box scrolls three lines per notch, regardless of keyboard focus. Mouse reports
are not passed into debugger input. Mouse capture is disabled when leaving
split view, entering PuDB, or exiting. Use the terminal's selection modifier
(usually Shift) to select text while mouse capture is active.


### Continue performance

Continue mode skips IPython's stepping-only frame inspection while retaining
normal breakpoint evaluation, conditions, ignore counts and temporary breaks.
Step/next/return behavior is unchanged. Continuing without breakpoints already
disables Python tracing; this optimization targets runs with breakpoints.
Python 3.6-3.10 still use tracing when breakpoints are present, so they cannot
be expected to match normal execution speed, especially in tight Python loops.

Run `python benchmark_speed.py` from a source checkout for a repeatable comparison
of native execution, upstream tracing and optimized tracing. This measures a
CPU loop with an end breakpoint, not terminal output throughput or startup time.
The local Windows Python 3.9 measurement was approximately 0.49 s upstream versus
0.19 s optimized (2.6x); native execution was about 0.003 s. Actual gains depend
on the Python/IPython version and workload. Native Linux measurement is pending.


If an older checkout reports `No module named pyte`, update the complete
repository with `git pull` and launch again. The loader now checks for pyte
and wcwidth as well as ipdb/IPython, and adds missing files from the matching
archive in dist without network access. Existing files are preserved. For an
extracted release without dist, extract the latest matching ZIP into a new
directory and run its ipdb.sh; do not copy only the launcher scripts.


### Startup progress and unexpected exits

The launcher prints the selected interpreter, dependency check, debugger loading
and terminal initialization stages. First-run or upgrade extraction reports
progress and completion time; subsequent launches reuse the dependencies.
When the terminal debugger exits, the last 100 captured output lines are printed
on the normal terminal screen so error messages remain visible. Nonzero exit
status is also reported. If startup still fails, run `./ipdb.sh --plain my.py`
and provide the complete traceback. The progress messages describe actual work;
they do not imply a successful startup before the debugger prompt appears.


### Source cursor, search and marks

Use Ctrl-W k to focus source and Ctrl-W j to return to commands. The source
browser has a light block cursor independent of the yellow execution marker.
h/j/k/l and arrows move the cursor; pages, gg/G and 0/$ move through the file.
The viewport follows the cursor. The mouse wheel scrolls the viewport only.

Type /keyword and Enter for forward literal, case-sensitive search, or ?keyword
for backward search. n repeats in the same direction, N reverses it; searches
wrap at file boundaries. Backspace edits the query and Escape cancels it.
The query/status appears in the source footer. No-match searches retain position.

ma saves mark a at the browsing cursor (a-z supported). 'a jumps to its line;
`a restores its exact column. Marks are local to each file and kept for this
session across debugger stops; they are not saved on disk and do not change
program execution. A new debugger stop places the browsing cursor at the
execution line. zz returns the browser to that line.


### Multiple source windows

With source focus, type :split (or :sp) and Enter for a top/bottom split,
or :vsplit (or :vs) for a left/right split. The new window receives a copy
of the current source view and has an independent cursor, search and viewport.
Ctrl-W h/j/k/l selects the adjacent source window or the command area.
:q (or :quit) closes the focused extra source window and returns focus to MAIN.
The top-left MAIN window cannot be closed. Only MAIN follows debugger stops
and frame changes; other windows keep their displayed file and browsing state.
Mouse-wheel scrolling targets the window under the pointer without changing
keyboard focus. Splitting is refused when the current window is too small.


### System Vim mode

Run `./ipdbVim.sh my.py [script arguments]` to always use the system Vim view.
The equivalent explicit command is `./ipdb.sh --source-vim my.py`.
The normal ipdb.sh default is unchanged. IPDB_PYTHON also works with ipdbVim.sh.

This Linux mode requires vim and tmux on PATH. Vim must support timers, JSON,
window IDs and signs (Vim 8+ with +timers, +eval and +signs). It launches real
Vim with your normal vimrc and plugins; plugin compatibility still depends on
your Vim build and each plugin's dependencies. No user configuration is rewritten.
Vim startup is checked before starting the debuggee and reports a timeout/error
instead of silently opening a broken session.

The upper tmux pane is Vim; the lower pane is ipdb. Press Ctrl-B, release it,
then k/j to move between these panes. Inside Vim, use native Ctrl-W navigation,
:split, :vsplit, :q, searches, marks and plugins. Only the top-left window of
the first Vim tab follows debugger stops. Other windows retain their views.
The bridge places an execution sign without writing source files, and preserves
modified buffers when switching source. Source-sync errors appear in Vim messages
and g:ipdb_source_error. Allowing Vim/plugin actions to edit files is normal Vim
behavior; edits do not reload code already running in Python.

The mode uses its own temporary tmux server with default bindings and mouse
support, independent of existing tmux sessions. It does not load ~/.tmux.conf.
On debugger exit, output remains visible until Enter is pressed; save any Vim
edits before confirming. Detaching ends this temporary session and its processes.
The built-in Ctrl-X A/S layout and PuDB shortcuts are not active in this mode.
Local validation includes real Windows Vim configuration/plugin loading and
source synchronization; a complete Linux tmux interactive run is still pending.


### Windows system Vim launcher

From cmd: `ipdbVim.cmd my.py`. From PowerShell: `.\ipdbVim.cmd my.py`.
This always selects system Vim mode, and respects IPDB_PYTHON like ipdb.cmd.
Native vim.exe must be on PATH; no WSL or tmux is needed on Windows. The selected
Python must have the Windows debugger dependencies (an existing ipdb installation
or a matching vendor/win32-pyXY prepared using `python prepare.py`). Linux vendor
packages are not Windows dependency bundles.

If wt.exe is on PATH, a new Windows Terminal window contains upper Vim and lower
ipdb panes. Use your Windows Terminal pane-switch shortcuts between them, and
Vim's own Ctrl-W keys within its source windows. Without Windows Terminal, Vim
opens in a separate console and ipdb stays in the invoking console; use Alt-Tab.
Vim loads the normal Windows user configuration (_vimrc/vimrc) and plugins.
After debugging, Enter closes an unmodified Vim session; unsaved buffers are
kept open. If Vim fails to initialize, inspect its visible startup errors. A
blocking plugin or vimrc command must finish before the debugger can start.

Validation includes the actual Windows .cmd entry, native console Vim/ipdb
execution with an isolated Vim configuration, and separate real Vim plugin and
source-sync tests. The Windows Terminal pane path is not locally end-to-end
tested because wt.exe is unavailable on the test machine. Opt in to visible
console integration tests by setting IPDB_TEST_WINDOWS_CONSOLE=1.


### Unified tmux window keys and saved-code restart

In system Vim/tmux mode, Ctrl-W followed by h/j/k/l now navigates between
source and debugger panes. Inside Vim, the same keys first move between Vim
windows; at an edge they move to the neighboring tmux pane. Ctrl-B k/j still
works. In the debugger pane Ctrl-W Ctrl-W sends the original Ctrl-W character
(backward word deletion). These tmux mappings are session-local and do not
change user configuration. Windows Terminal uses its own pane keys.

After saving with :w, enter r (or run) at the ipdb prompt to restart using the
saved script. Newly imported Python modules under the entry script's directory
are evicted from the module cache and loaded from source, bypassing stale pyc
files even for same-size saves with the same timestamp. Modules loaded before
the debug session, modules outside that directory, and native extensions are
not reloaded. This is a program restart, not live replacement of active frames.
Breakpoints remain line-based; adjust them if edits move code. Existing fork
checkpoints are discarded on restart so they cannot restore pre-edit state.


### Window height keys in system Vim/tmux mode

Use Ctrl-W, then an optional count, then + or - to grow/shrink the current
window vertically. For example Ctrl-W 5 + adds five rows; Ctrl-W - removes one.
Ctrl-W = equalizes heights. Inside the source Vim pane these remain Vim's native
window commands and affect Vim's internal splits. In the debugger pane the
same keys adjust its tmux pane (the source pane receives the remaining space).
Counts are capped at 9999 and tmux enforces available terminal space. Use
Ctrl-W = in the debugger pane to restore equal source/debugger heights.


Mouse clicks now select the corresponding source window or command pane and
clear unfinished window-key/search prefixes. In the built-in source view, clicks
also place the browsing cursor. System Vim mode enables mouse=a, allowing Vim
to select its internal windows. In tmux, a click leaves copy mode and returns
to normal input before forwarding the event. Drag selection inside Vim can enter
Vim Visual mode; press Escape to return to Normal mode. Shift+mouse is normally
handled by the terminal for text copying and may bypass application focus; use
a regular click before typing commands after terminal-side selection.


System Vim source views now request a full redraw after viewport changes such
as G/gg, horizontal scrolling, window switches and resizing. Redraw is queued
after window events and coalesced; unchanged view signatures do not force
another repaint. Debugger source synchronization also
refreshes the screen after restoring the selected window. Ctrl-L remains Vim's
manual redraw key. Linux tmux visual regression verification is still pending.


In system Vim mode, automatic file/line and source listings are shown only in
Vim. The debugger pane retains display expressions, command results and errors.
Explicit bt, where/w and list commands remain available when you want source
or stack information in the command pane. Plain ipdb mode is unchanged.


The readline debugger command loop explicitly enables Tab completion even when
IPython initializes completekey=None. Examples: p vari<Tab>, disp vari<Tab>,
and p object.attr<Tab>. Candidates use the selected frame's locals and globals;
ambiguous names extend only to their common prefix, without a candidate list. The previous readline completer
is restored when leaving the command loop.


When stepping into another function, the Vim source bridge opens folds at the
execution line and schedules a second full repaint after buffer/window events
have settled. Only the main source window follows the step; other source windows
remain at their saved positions. This supplements the immediate repaint.


For system Vim under tmux, the bridge disables terminal line-scroll reuse
(ttyscroll=0), delayed redraw, and background-color erase optimization (t_ut).
This favors explicit repainting on terminals that leave old text behind.
The view signature now includes buffer identity, edits, wrapped-line offsets,
fold state and cursor/view changes. A low-frequency poll also covers old Vim
versions where scrolling/folding does not emit a suitable window event.
Regression scenarios include counted G, gg, searches, marks, page movement,
horizontal scrolling, folds, edits and split-window navigation. This does not
constitute validation of every terminal/Vim/plugin combination. If artifacts
persist, :IpdbViewInfo reports the current terminal and viewport settings for
diagnosis. These options apply only to the launched tmux Vim session.


Variable Tab completion only inserts the longest common prefix. For example,
with signal_count, signal_config and signal_status, sig<Tab> becomes signal_.
Another Tab does nothing; after typing c, Tab extends to signal_co. After typing
u, Tab completes signal_count. Candidate lists are never displayed for these
completions, including repeated Tab presses. Completion remains triggered by Tab.


Background source polling no longer repaints an unchanged view. Automatic
source updates and redraw are paused outside Normal mode, including Ex command
output and hit-enter prompts. Command-line exit no longer forces a repaint,
so :ls and :IpdbViewInfo remain visible. IpdbViewInfo also stores its output in
:messages. Use the exact command capitalization :IpdbViewInfo.
