"""Prepare dependencies with the destination Python version and OS."""
import subprocess
import sys
from pathlib import Path


def Main():
    if sys.version_info[:2] not in ((3, 6), (3, 7), (3, 9), (3, 10), (3, 12)):
        raise RuntimeError('Use Python 3.6, 3.7, 3.9, 3.10, or 3.12')
    root = Path(__file__).resolve().parent
    target = root / 'vendor' / '{}-py{}{}'.format(sys.platform, *sys.version_info[:2])
    if target.exists():
        raise RuntimeError('Target already exists; use a clean folder: {}'.format(target))
    subprocess.check_call([
        sys.executable, '-m', 'pip', 'install', '--target', str(target),
        '--no-compile', '--find-links', str(root / 'vendor_wheels'),
        '-r', str(root / 'requirements.txt')])


if __name__ == '__main__':
    Main()
