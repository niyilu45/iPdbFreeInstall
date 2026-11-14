"""Load the bundled debugger without installing packages."""
import sys
from pathlib import Path


def Activate():
    if sys.version_info[:2] not in ((3, 6), (3, 7), (3, 9), (3, 10)):
        raise RuntimeError('Supported Python versions: 3.6, 3.7, 3.9, 3.10')
    root = Path(__file__).resolve().parent / 'vendor'
    specific = root / '{}-py{}{}'.format(sys.platform, *sys.version_info[:2])
    vendor = str(specific if specific.is_dir() else root)
    if vendor not in sys.path:
        sys.path.insert(0, vendor)


Activate()
from gdb_commands import Install
Install()

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--pudb':
        del sys.argv[1]
        from pudb.run import main
        main()
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == '--tui':
        from split_ipdb import Run
        try:
            sys.exit(Run(sys.argv[2:]))
        except RuntimeError as error:
            sys.exit(str(error))
    if len(sys.argv) > 1 and sys.argv[1] == '--plain':
        del sys.argv[1]
    from ipdb.__main__ import main
    from checkpoints import Supervise
    sys.exit(Supervise(main))
