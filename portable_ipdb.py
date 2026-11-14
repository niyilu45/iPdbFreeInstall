"""Load the bundled debugger without installing packages."""
import sys
from pathlib import Path


def Activate():
    if sys.version_info[:2] not in ((3, 6), (3, 7), (3, 9), (3, 10)):
        raise RuntimeError('Supported Python versions: 3.6, 3.7, 3.9, 3.10')
    from bundle_loader import Locate
    base = Path(__file__).resolve().parent
    try:
        vendor = str(Locate(base, sys.platform, sys.version_info[:2]))
    except RuntimeError:
        # Preserve use of an existing installation in source-only setups.
        import importlib.util
        installed = importlib.util.find_spec('ipdb')
        if installed is None or not installed.origin or any((base / 'vendor').glob('*-py*')) or (base / 'dist' / ('ipdb-{}-py{}{}.zip'.format(sys.platform, *sys.version_info[:2]))).exists():
            raise
        return str(Path(installed.origin).parent.parent)
    if vendor in sys.path:
        sys.path.remove(vendor)
    # ipdb.main replaces sys.path[0] with the debuggee's directory.
    sys.path.insert(1, vendor)
    return vendor


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
from gdb_commands import Install
Install()

if __name__ == '__main__':
    from launch_mode import SelectMode
    mode, arguments = SelectMode(sys.argv[1:])
    sys.argv = [sys.argv[0]] + arguments
    if mode == 'pudb':
        from pudb.run import main
        main()
        sys.exit(0)
    if mode in ('tui', 'keys'):
        from split_ipdb import Run
        try:
            sys.exit(Run(arguments, start_split=(mode == 'tui')))
        except RuntimeError as error:
            sys.exit(str(error))
    from ipdb.__main__ import main
    from checkpoints import Supervise
    sys.exit(Supervise(main))
