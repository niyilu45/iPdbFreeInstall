"""Load the bundled debugger without installing packages."""
import os
import sys
from pathlib import Path


def OpenIpdbConfig(filename, mode='r'):
    import io
    import locale

    if mode == 'rb':
        return open(filename, mode)
    data = Path(filename).read_bytes()
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        encoding = locale.getpreferredencoding(False)
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError as error:
            raise UnicodeError('Cannot decode ipdb configuration {} as UTF-8 or {}: {}'.format(
                filename, encoding, error)) from error
    stream = io.StringIO(text, newline=None)
    stream.name = str(filename)
    return stream


def Activate():
    if sys.version_info[:2] not in ((3, 6), (3, 7), (3, 9), (3, 10), (3, 12)):
        raise RuntimeError('Supported Python versions: 3.6, 3.7, 3.9, 3.10, 3.12')
    from bundle_loader import Locate
    base = Path(__file__).resolve().parent
    bundled = True
    try:
        vendor = str(Locate(base, sys.platform, sys.version_info[:2]))
    except RuntimeError:
        # Preserve use of an existing installation in source-only setups.
        import importlib.util
        installed = importlib.util.find_spec('ipdb')
        if installed is None or not installed.origin or any((base / 'vendor').glob('*-py*')) or (base / 'dist' / ('ipdb-{}-py{}{}.zip'.format(sys.platform, *sys.version_info[:2]))).exists():
            raise
        vendor = str(Path(installed.origin).parent.parent)
        bundled = False
    # ipdb.main replaces sys.path[0] with the debuggee's directory. Keep
    # both dependencies and lazily imported launcher modules after that slot.
    launcher = str(base)
    retained = [vendor, launcher] if bundled and vendor != launcher else [launcher]
    sys.path[1:] = [entry for entry in sys.path[1:] if entry not in retained]
    sys.path[1:1] = retained
    return vendor


if __name__ == '__main__':
    from launch_mode import CheckpointOption
    checkpoint, arguments = CheckpointOption(sys.argv[1:])
    replay, arguments = CheckpointOption(arguments, '--checkpoint-replay')
    if checkpoint and sys.platform == 'win32':
        sys.exit('Process checkpoints are unavailable in native Windows Python. Use --checkpoint-replay for a deterministic script.')
    if checkpoint and arguments[:1] == ['--pudb']:
        sys.exit('The checkpoint launch option requires ipdb, use --plain, --tui, or Vim mode.')
    if replay:
        if sys.platform != 'win32':
            sys.exit('The replay checkpoint launch option is for native Windows Python.')
        if arguments[:1] in (['--pudb'], ['--tui']):
            sys.exit('Use --checkpoint-replay in plain, system Vim, or gVim mode.')
        if arguments[:1] in (['--source-vim'], ['--source-gvim'], ['--plain']):
            script_arguments = arguments[1:]
        else:
            script_arguments = arguments
        if not script_arguments or script_arguments[0].startswith('-'):
            sys.exit('Usage: ipdbVim.cmd --checkpoint-replay script.py [arguments]')
        if arguments[:1] not in (['--source-vim'], ['--source-gvim']):
            from replay_session import SuperviseReplay
            try:
                sys.exit(SuperviseReplay(Path(__file__).resolve(), script_arguments))
            except OSError as error:
                sys.exit('Could not start native Windows replay: {}'.format(error))
    sys.argv[1:] = arguments
    if not os.environ.get('IPDB_REPLAY_TARGET'):
        print('[ipdb] Starting with Python {}: {}'.format(sys.version.split()[0], sys.executable), file=sys.stderr, flush=True)
        print('[ipdb] Checking offline dependencies...', file=sys.stderr, flush=True)

try:
    vendor_path = Activate()
except (RuntimeError, OSError) as error:
    if __name__ == '__main__':
        sys.exit('{}\nPython: {}'.format(error, sys.executable))
    raise

if __name__ == '__main__' and sys.argv[1:] == ['--check']:
    import ipdb
    print('Python: {}'.format(sys.executable))
    print('Version: {}'.format(sys.version.split()[0]))
    print('Dependencies: {}'.format(vendor_path))
    print('ipdb: {}'.format(ipdb.__file__))
    sys.exit(0)
if __name__ == '__main__' and not os.environ.get('IPDB_REPLAY_TARGET'):
    print('[ipdb] Loading debugger...', file=sys.stderr, flush=True)
import ipdb.__main__ as ipdb_main
# Scope the encoding fallback to ipdb's config reader, including embedded set_trace().
ipdb_main.open = OpenIpdbConfig
from debugger_speed import Install as InstallSpeed
InstallSpeed()
from reload_code import Install as InstallReload
InstallReload()
from gdb_commands import Install
Install()
from plot_events import Install as InstallPlotEvents
if __name__ != '__main__':
    InstallPlotEvents()

if __name__ == '__main__':
    from launch_mode import SelectMode
    mode, arguments = SelectMode(sys.argv[1:])
    if mode != 'pudb':
        InstallPlotEvents()
    sys.argv = [sys.argv[0]] + arguments
    if mode in ('source-vim', 'source-gvim'):
        if mode == 'source-gvim':
            from source_gvim import Run
        else:
            from source_vim import Run
        try:
            sys.exit(Run(['--checkpoint-replay'] + arguments if replay else arguments))
        except (RuntimeError, OSError) as error:
            sys.exit(str(error))
    if mode == 'pudb':
        from pudb.run import main
        main()
        sys.exit(0)
    if mode in ('tui', 'keys'):
        print('[ipdb] Opening terminal debugger...', file=sys.stderr, flush=True)
        from split_ipdb import Run
        try:
            sys.exit(Run(arguments, start_split=(mode == 'tui')))
        except RuntimeError as error:
            sys.exit(str(error))
    if os.environ.get('IPDB_VIM_STATE'):
        from source_vim import InstallHook
        InstallHook(os.environ['IPDB_VIM_STATE'])
    if sys.platform == 'win32' and os.environ.get('IPDB_CONEMU_HELPER'):
        from windows_vim import InstallConEmuKeys
        InstallConEmuKeys()
    if sys.platform == 'win32' and os.environ.get('IPDB_REPLAY_SESSION'):
        from replay_checkpoints import RunReplayWorker
        sys.exit(RunReplayWorker(arguments, ipdb_main))
    from ipdb.__main__ import main
    from checkpoints import Supervise
    saved_input = None
    if sys.platform.startswith('linux') and sys.stdin.isatty() and sys.stdout.isatty():
        from terminal_keys import ConfigureInput, RestoreInput
        saved_input = ConfigureInput()
    try:
        exit_code = Supervise(main)
    finally:
        if saved_input is not None:
            RestoreInput(saved_input)
    sys.exit(exit_code)
