"""Load the bundled debugger without installing packages."""
import sys
from pathlib import Path


def Activate():
    vendor = str(Path(__file__).resolve().parent / 'vendor')
    if vendor not in sys.path:
        sys.path.insert(0, vendor)


Activate()

if __name__ == '__main__':
    import runpy
    runpy.run_module('ipdb', run_name='__main__')
