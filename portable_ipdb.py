"""Load the bundled debugger without installing packages."""
import sys
from pathlib import Path


def Activate():
    if sys.version_info[:2] not in ((3, 6), (3, 7), (3, 9), (3, 10)):
        raise RuntimeError('Supported Python versions: 3.6, 3.7, 3.9, 3.10')
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
if __name__ == '__main__':
    print('[ipdb] Loading debugger...', file=sys.stderr, flush=True)
from debugger_speed import Install as InstallSpeed
InstallSpeed()
from reload_code import Install as InstallReload
InstallReload()
from gdb_commands import Install
Install()

if __name__ == '__main__':
    from launch_mode import SelectMode
    mode, arguments = SelectMode(sys.argv[1:])
    sys.argv = [sys.argv[0]] + arguments
    if mode in ('source-vim', 'source-gvim'):
        if mode == 'source-gvim':
            from source_gvim import Run
        else:
            from source_vim import Run
        try:
            sys.exit(Run(arguments))
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
    import os
    if os.environ.get('IPDB_VIM_STATE'):
        from source_vim import InstallHook
        InstallHook(os.environ['IPDB_VIM_STATE'])
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
