"""Locate or extract matching offline dependencies without pip or network."""
from pathlib import Path
import shutil
import tempfile
import zipfile
import sys
import time


def Complete(target):
    return all((target / name / '__init__.py').is_file()
               for name in ('ipdb', 'IPython', 'pyte', 'wcwidth'))


def Locate(base, platform, version):
    base = Path(base).resolve()
    tag = '{}-py{}{}'.format(platform, *version)
    root = base / 'vendor'
    target = root / tag
    if Complete(target):
        return target
    if Complete(root):
        return root
    if (root / 'ipdb' / '__init__.py').is_file():
        target = root
    archive = base / 'dist' / ('ipdb-' + tag + '.zip')
    if not archive.is_file():
        available = ', '.join(sorted(p.name for p in root.glob('*-py*') if p.is_dir())) or 'none'
        raise RuntimeError(
            'Missing or incomplete bundled dependencies (ipdb, IPython, pyte, wcwidth) for {}. Available dependency folders: {}. '
            'Download and extract ipdb-{}.zip in full, or run from a complete '
            'repository checkout containing dist/ipdb-{}.zip. '
            'Use IPDB_PYTHON to select the matching interpreter.'.format(tag, available, tag, tag))
    prefix = 'ipdb-{}/vendor/{}/'.format(tag, tag)
    print('[ipdb] Preparing offline dependencies for {} (first run or upgrade)...'.format(tag), file=sys.stderr, flush=True)
    started = time.monotonic()
    progress = started
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.extract-', dir=str(root)) as temporary:
        staging = Path(temporary) / tag
        staging.mkdir()
        with zipfile.ZipFile(str(archive)) as bundle:
            members = [member for member in bundle.infolist() if member.filename.startswith(prefix)]
            for index, member in enumerate(members, 1):
                if time.monotonic() - progress >= 1:
                    print('[ipdb] Extracting dependencies: {}/{} entries'.format(index, len(members)), file=sys.stderr, flush=True)
                    progress = time.monotonic()
                if not member.filename.startswith(prefix):
                    continue
                relative = member.filename[len(prefix):]
                if not relative:
                    continue
                parts = relative.split('/')
                if any(part in ('..', '.') or ':' in part or '\\' in part for part in parts):
                    raise RuntimeError('Unsafe archive member')
                destination = staging.joinpath(*parts)
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, destination.open('wb') as output:
                        shutil.copyfileobj(source, output)
        if not Complete(staging):
            raise RuntimeError('The offline archive is incomplete: {}'.format(archive))
        print('[ipdb] Installing validated dependencies...', file=sys.stderr, flush=True)
        if target.exists():
            # Add missing dependency files after validating the entire archive.
            # Never remove or overwrite existing user files during repair.
            for source in staging.rglob('*'):
                if source.is_file():
                    destination = target / source.relative_to(staging)
                    if not destination.exists():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(source), str(destination))
        else:
            try:
                staging.rename(target)
            except OSError:
                # A concurrent launcher may have published its validated tree.
                if not Complete(target):
                    raise
    print('[ipdb] Dependencies ready ({:.1f}s). Future launches reuse them.'.format(time.monotonic() - started), file=sys.stderr, flush=True)
    return target
