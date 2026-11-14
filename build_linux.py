"""Build Linux portable bundles from pure-Python wheels with target markers."""
import email
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent


def Build(version):
    tag = 'linux-py' + version.replace('.', '')
    wheels = ROOT / 'build' / tag
    wheels.mkdir(parents=True, exist_ok=True)
    env = default_environment()
    env.update(python_version=version, python_full_version=version + '.0', sys_platform='linux', os_name='posix', platform_system='Linux', extra='')
    ipython = {'3.6': '7.16.3', '3.7': '7.34.0', '3.9': '8.18.1', '3.10': '8.18.1'}[version]
    pending = [Requirement('pyte==0.8.0'), Requirement('ipdb==0.13.13'), Requirement('ipython==' + ipython)]
    pending.extend([Requirement('pudb==' + ('2020.1' if version == '3.6' else '2022.1')),
                    Requirement('urwid==2.1.2'), Requirement('urwid-readline==0.13')])
    if version == '3.6':
        pending.append(Requirement('jedi==0.17.2'))
    selected = {}
    constraints = {}
    while pending:
        req = pending.pop(0)
        if req.marker and not req.marker.evaluate(env):
            continue
        name = canonicalize_name(req.name)
        constraints.setdefault(name, set()).add(str(req.specifier))
        spec = ','.join(sorted(x for x in constraints[name] if x))
        if name in selected:
            if not SpecifierSet(spec).contains(selected[name][0]):
                raise RuntimeError('Dependency conflict: ' + name + spec)
            continue
        cached = []
        for candidate in list(wheels.glob('*.whl')) + list((ROOT / 'vendor_wheels').glob('*.whl')):
            with zipfile.ZipFile(candidate) as z:
                metadata = email.message_from_bytes(z.read(next(n for n in z.namelist() if n.endswith('.dist-info/METADATA'))))
            if canonicalize_name(metadata['Name']) == name and SpecifierSet(spec).contains(metadata['Version']):
                cached.append(candidate)
        if cached:
            if cached[0].parent != wheels:
                shutil.copyfile(cached[0], wheels / cached[0].name)
        else:
            subprocess.check_call([sys.executable, '-m', 'pip', 'download', '--no-deps', '--only-binary=:all:', '--python-version', version, '--platform', 'any', '--dest', str(wheels), '--index-url', 'https://mirrors.aliyun.com/pypi/simple/', '--disable-pip-version-check', name + spec])
        matches = []
        for path in wheels.glob('*.whl'):
            with zipfile.ZipFile(path) as z:
                meta = email.message_from_bytes(z.read(next(n for n in z.namelist() if n.endswith('.dist-info/METADATA'))))
            if canonicalize_name(meta['Name']) == name and SpecifierSet(spec).contains(meta['Version']):
                matches.append((path, meta))
        path, meta = matches[-1]
        if not SpecifierSet(meta.get('Requires-Python', '')).contains(version + '.0'):
            raise RuntimeError('Unsupported Python: ' + name)
        selected[name] = (meta['Version'], path)
        pending.extend(Requirement(r) for r in meta.get_all('Requires-Dist', []))
    package = ROOT / 'build' / ('ipdb-' + tag)
    package.mkdir(exist_ok=True)
    vendor = package / 'vendor' / tag
    vendor.mkdir(parents=True, exist_ok=True)
    for _, path in selected.values():
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                parts = info.filename.split('/')
                if any(p == '..' for p in parts) or info.filename.startswith('/'):
                    raise RuntimeError('Unsafe wheel path')
                if '.data/' in info.filename:
                    if len(parts) > 2 and parts[1] in ('purelib', 'platlib'):
                        target = vendor.joinpath(*parts[2:])
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if not info.is_dir():
                            target.write_bytes(z.read(info))
                    continue
                z.extract(info, vendor)
    for filename in ('portable_ipdb.py', 'bundle_loader.py', 'launch_mode.py', 'source_theme.py', 'source_browser.py', 'source_panes.py', 'source_vim.py', 'windows_vim.py', 'source_vim.vim', 'terminal_keys.py', 'debugger_speed.py', 'reload_code.py', 'output_history.py', 'split_ipdb.py', 'gdb_commands.py', 'completion.py', 'checkpoints.py', 'ipdb.sh', 'ipdbVim.sh', 'ipdbVim.cmd', 'README.md'):
        shutil.copyfile(ROOT / filename, package / filename)
    (package / 'requirements.lock').write_text('\n'.join('{}=={}'.format(k, v[0]) for k, v in sorted(selected.items())) + '\n')
    (package / 'wheel-sha256.json').write_text(json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for _, p in selected.values()}, indent=2))
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    archive = dist / ('ipdb-' + tag + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in package.rglob('*'):
            if path.is_file():
                info = zipfile.ZipInfo.from_file(path, str(Path(package.name) / path.relative_to(package)))
                info.create_system = 3
                info.external_attr = (0o100755 if path.name in ('ipdb.sh', 'ipdbVim.sh') else 0o100644) << 16
                z.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    print('BUILT', archive, flush=True)


if __name__ == '__main__':
    os.environ['NO_PROXY'] = '*'
    for version in sys.argv[1:] or ['3.6', '3.7', '3.9', '3.10']:
        Build(version)
