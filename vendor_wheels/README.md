# Locally built pure-Python wheels

PuDB 2020.1, PuDB 2022.1, and urwid-readline 0.13 were built from upstream
source distributions with pip wheel --use-pep517 --no-deps.
Urwid 2.1.2 was built from its upstream source distribution with the
setup.py ext_modules list replaced by [] to disable the optional C speedup.
No package runtime source was modified. Upstream licenses and metadata
are retained inside each wheel and the extracted release bundles.

Sources: https://pypi.org/project/pudb/2020.1/
https://pypi.org/project/pudb/2022.1/
https://pypi.org/project/urwid/2.1.2/
https://pypi.org/project/urwid-readline/0.13/
