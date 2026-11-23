"""Refresh release launchers without downloading or changing dependencies."""
from pathlib import Path
import zipfile


def Main():
    root = Path(__file__).resolve().parent
    archives = [archive for archive in (root / 'dist').glob('ipdb-*-py*.zip')
                if archive.name.startswith(('ipdb-linux-', 'ipdb-win32-'))]
    for archive in archives:
        prefix = archive.stem + '/'
        replacement = {prefix + name: (root / name).read_bytes() for name in ('portable_ipdb.py', 'bundle_loader.py', 'launch_mode.py', 'source_theme.py', 'source_browser.py', 'source_panes.py', 'source_vim.py', 'source_gvim.py', 'tmux_resize.py', 'windows_vim.py', 'source_vim.vim', 'terminal_keys.py', 'debugger_speed.py', 'reload_code.py', 'function_timing.py', 'plot_events.py', 'output_history.py', 'split_ipdb.py', 'gdb_commands.py', 'completion.py', 'checkpoints.py', 'ipdb.sh', 'ipdb.cmd', 'ipdbVim.sh', 'ipdbVim.cmd', 'ipdbGvim.sh', 'ipdbGvim.cmd', 'README.md')}
        temporary = archive.with_suffix('.tmp')
        replacement = {name: data.replace(b'\r\n', b'\n') if name.endswith(('.sh', '.vim')) else data
                       for name, data in replacement.items()}
        with zipfile.ZipFile(archive) as old, zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as new:
            for info in old.infolist():
                if info.filename not in replacement:
                    new.writestr(info, old.read(info.filename))
            for name, data in replacement.items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
                new.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        temporary.replace(archive)
    print('Updated {} offline archives'.format(len(archives)))


if __name__ == '__main__':
    Main()
