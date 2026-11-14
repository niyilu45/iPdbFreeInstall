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

if __name__ == '__main__':
    from ipdb.__main__ import main
    main()
