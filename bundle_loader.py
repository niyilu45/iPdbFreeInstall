"""Locate or extract matching offline dependencies without pip or network."""
from pathlib import Path
import shutil
import tempfile
import zipfile


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
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.extract-', dir=str(root)) as temporary:
        staging = Path(temporary) / tag
        staging.mkdir()
        with zipfile.ZipFile(str(archive)) as bundle:
            for member in bundle.infolist():
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
            staging.rename(target)
    return target
