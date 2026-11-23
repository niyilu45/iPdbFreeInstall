"""System gVim source window with the debugger in the current terminal."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def Run(arguments):
    if not arguments:
        raise RuntimeError('Usage: ipdbGvim script.py [arguments]')
    gvim = shutil.which('gvim')
    if not gvim:
        raise RuntimeError('System gvim was not found on PATH. Install gVim and add it to PATH.')
    if sys.platform.startswith('linux') and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        raise RuntimeError('gVim requires a graphical desktop (DISPLAY or WAYLAND_DISPLAY).')
    if sys.platform == 'win32':
        # Windows gvim --version opens a GUI dialog instead of returning stdout.
        with tempfile.TemporaryDirectory(prefix='ipdb-gvim-version-') as directory:
            version_file = Path(directory) / 'version.txt'
            target = str(version_file).replace('\\', '/').replace("'", "''")
            subprocess.run([gvim, '-N', '-u', 'NONE', '-U', 'NONE', '-i', 'NONE', '-es',
                            '-c', "execute 'redir! > ' . fnameescape('{}')".format(target),
                            '-c', 'silent version', '-c', 'redir END', '-c', 'qa!'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10)
            version = version_file.read_bytes()
    else:
        version = subprocess.check_output([gvim, '--version'], timeout=10)
    if any(feature not in version for feature in (b'+timers', b'+eval', b'+signs')):
        raise RuntimeError('System gVim needs +timers, +eval and +signs.')
    from source_vim import VimCommand
    base = Path(__file__).resolve().parent
    root = Path(tempfile.mkdtemp(prefix='ipdb-gvim-'))
    env = dict(os.environ)
    for key in ('IPDB_TMUX_BIN', 'IPDB_TMUX_SOCKET'):
        env.pop(key, None)
    env.update(IPDB_VIM_STATE=str(root / 'state'), IPDB_VIM_READY=str(root / 'ready'),
               IPDB_VIM_CLOSE=str(root / 'close'), IPDB_VIM_CLOSED=str(root / 'closed'))
    command = VimCommand(gvim, base / 'source_vim.vim')
    command.insert(1, '-f')
    # Keep terminal interrupts in the debugger's process group.
    if sys.platform == 'win32':
        editor_group = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        editor_group = {'start_new_session': True}
    process = None
    print('[ipdb] Opening system gVim; debugger commands remain in this terminal.', flush=True)
    if env.get('IPDB_VIM_CLEAN') == '1':
        print('[ipdb] Diagnostic mode: user vimrc and plugins disabled for this session.', flush=True)
    try:
        with (root / 'startup.log').open('w') as log:
            editor_options = dict(env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **editor_group)
            process = subprocess.Popen(command, **editor_options)
            deadline = time.monotonic() + 20
            while not (root / 'ready').exists():
                if process.poll() is not None:
                    raise RuntimeError('gVim exited before initializing: ' + (root / 'startup.log').read_text(errors='replace')[-1500:])
                if time.monotonic() >= deadline:
                    raise RuntimeError('gVim did not initialize in 20 seconds. Check its window for startup errors.')
                time.sleep(0.05)
            message = (root / 'ready').read_text().strip()
            if message != 'ready':
                raise RuntimeError(message)
            debugger = subprocess.Popen([sys.executable, str(base / 'portable_ipdb.py'), '--plain'] + list(arguments), env=env)
            restarts = []
            while True:
                try:
                    if debugger.poll() is not None:
                        return debugger.returncode
                    if process.poll() is not None:
                        now = time.monotonic()
                        restarts = [stamp for stamp in restarts if now - stamp < 10]
                        # Bound repeated startup crashes while retaining automatic recovery.
                        if len(restarts) < 3:
                            restarts.append(now)
                            for name in ('ready', 'closed'):
                                try:
                                    (root / name).unlink()
                                except FileNotFoundError:
                                    pass
                            print('[ipdb] Source window closed; reopening gVim at the current location.', flush=True)
                            process = subprocess.Popen(command, **editor_options)
                    time.sleep(0.1)
                except KeyboardInterrupt:
                    # The running debugger shares our terminal and handles Ctrl+C.
                    continue
    finally:
        (root / 'close').write_text('close')
        deadline = time.monotonic() + 3
        while process is not None and process.poll() is None and time.monotonic() < deadline:
            if (root / 'closed').exists() and (root / 'closed').read_text().strip() == 'kept':
                print('[ipdb] Unsaved gVim buffers kept open.', flush=True)
                break
            time.sleep(0.05)
        if process is None or process.poll() is not None or (root / 'closed').exists():
            shutil.rmtree(str(root))
        else:
            print('[ipdb] gVim is still active; close request retained in {}'.format(root), flush=True)
