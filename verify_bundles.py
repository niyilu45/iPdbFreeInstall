"""Audit offline archives using target Linux dependency metadata."""
import email
import hashlib
from pathlib import Path
import zipfile
from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.utils import canonicalize_name


def Main():
    root = Path(__file__).resolve().parent
    sums = []
    for version in ('3.6', '3.7', '3.9', '3.10'):
        tag = 'linux-py' + version.replace('.', '')
        path = root / 'dist' / ('ipdb-' + tag + '.zip')
        env = default_environment()
        env.update(python_version=version, python_full_version=version + '.0', sys_platform='linux', os_name='posix', platform_system='Linux', extra='')
        with zipfile.ZipFile(path) as z:
            assert z.testzip() is None
            prefix = 'ipdb-' + tag + '/'
            assert b'\r' not in z.read(prefix + 'ipdb.sh')
            assert z.getinfo(prefix + 'ipdb.sh').external_attr >> 16 & 0o111
            packages = {}
            for name in z.namelist():
                if name.endswith('.dist-info/METADATA') and '/vendor/' in name:
                    meta = email.message_from_bytes(z.read(name))
                    packages[canonicalize_name(meta['Name'])] = meta
            for name, meta in packages.items():
                assert SpecifierSet(meta.get('Requires-Python', '')).contains(version + '.0'), name
                for raw in meta.get_all('Requires-Dist', []):
                    req = Requirement(raw)
                    if req.marker and not req.marker.evaluate(env):
                        continue
                    dependency = packages[canonicalize_name(req.name)]
                    assert req.specifier.contains(dependency['Version']), raw
            assert packages['ipdb']['Version'] == '0.13.13'
            assert packages['pudb']['Version'] == ('2020.1' if version == '3.6' else '2022.1')
            assert packages['pyte']['Version'] == '0.8.0'
            assert 'urwid' in packages
            assert 'pexpect' in packages and 'ptyprocess' in packages
        sums.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name)
        print(tag, len(packages), 'packages: PASS')
    (root / 'dist' / 'SHA256SUMS').write_text('\n'.join(sums) + '\n')


if __name__ == '__main__':
    Main()
