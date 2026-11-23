"""Native Windows system Vim launchers; no WSL or tmux required."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


def Environment(config):
    return dict(os.environ, IPDB_VIM_STATE=config['state'], IPDB_VIM_READY=config['ready'],
                IPDB_VIM_CLOSE=config['close'], IPDB_VIM_CLOSED=config['closed'])


def WaitReady(config):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if Path(config['ready']).exists():
            message = Path(config['ready']).read_text().strip()
            if message != 'ready':
                raise RuntimeError(message)
            return
        if Path(config['vim_result']).exists():
            raise RuntimeError('System Vim exited before initializing; check your vimrc and plugins')
        time.sleep(0.1)
    raise RuntimeError('Vim did not initialize in 20 seconds; check its window for vimrc/plugin errors')


def Debug(config):
    from source_vim import WaitForDebugger
    code = 1
    try:
        WaitReady(config)
        debugger = subprocess.Popen(config['debugger'], env=Environment(config), cwd=config['cwd'])
        code = WaitForDebugger(debugger)
    except (OSError, RuntimeError) as error:
        print('[ipdb] ' + str(error), flush=True)
    finally:
        Path(config['result']).write_text(str(code))
        print('[ipdb] Debugger exited with status {}. Press Enter to finish. Unsaved Vim buffers stay open.'.format(code), flush=True)
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        Path(config['close']).write_text('close')
        Path(config['done']).write_text('done')
    return code


def Worker(role, filename):
    config = json.loads(Path(filename).read_text())
    if role == '--debug':
        Path(config['debug_pid']).write_text(str(os.getpid()))
    if role == '--vim':
        if sys.platform == 'win32' and not sys.stdin.isatty():
            with open('CONIN$', 'r') as console_in, open('CONOUT$', 'w') as console_out:
                code = subprocess.call(config['vim'], env=Environment(config), cwd=config['cwd'],
                                       stdin=console_in, stdout=console_out, stderr=console_out)
        else:
            code = subprocess.call(config['vim'], env=Environment(config), cwd=config['cwd'])
        try:
            Path(config['vim_result']).write_text(str(code))
        except FileNotFoundError:
            pass
        return code
    return Debug(config)


def TerminalCommand(terminal, name, config_path):
    worker = str(Path(__file__).resolve())
    common = [sys.executable, worker]
    return [terminal, '-w', name, 'new-tab', '--title', 'ipdb source',
            *common, '--vim', str(config_path), ';', 'split-pane', '-H', '--size', '0.5',
            '--title', 'ipdb commands', *common, '--debug', str(config_path)]


def OpenConEmuVim(config_path):
    directory = os.environ.get('ConEmuBaseDir')
    helper = shutil.which('ConEmuC64.exe', path=directory) or shutil.which('ConEmuC.exe', path=directory)
    if not helper:
        raise RuntimeError('ConEmuC was not found')
    worker = str(Path(__file__).resolve())
    command = subprocess.list2cmdline([sys.executable, worker, '--vim', str(config_path)])
    macro = 'Shell("new_console:s50Vbn","",{})'.format(json.dumps(command, ensure_ascii=False))
    # ConEmu reads the raw command-line remainder after -GuiMacro.
    call = subprocess.list2cmdline([helper, '-GuiMacro']) + ' ' + macro
    result = subprocess.run(call, executable=helper, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5)
    if result.returncode or result.stdout.strip() != b'OK':
        raise RuntimeError('ConEmu did not create the Vim pane')


def IsRunning(pid):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


def Run(arguments):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError('ipdbVim.cmd requires an interactive Windows console')
    vim = shutil.which('vim')
    if not vim:
        raise RuntimeError('System vim.exe was not found on PATH. Add your Vim installation directory to PATH.')
    if not arguments:
        raise RuntimeError('Usage: ipdbVim.cmd script.py [arguments]')
    version = subprocess.check_output([vim, '--version'])
    if any(feature not in version for feature in (b'+timers', b'+eval', b'+signs')):
        raise RuntimeError('System Vim needs +timers, +eval and +signs (Vim 8 or newer)')
    base = Path(__file__).resolve().parent
    conemu = bool(os.environ.get('ConEmuPID'))
    terminal = None if conemu else shutil.which('wt.exe')
    with tempfile.TemporaryDirectory(prefix='ipdb-vim-') as directory:
        root = Path(directory)
        config = {key: str(root / key) for key in ('state', 'ready', 'close', 'closed', 'result', 'done', 'vim_result', 'debug_pid')}
        from source_vim import VimCommand, WaitForDebugger
        config.update(vim=VimCommand(vim, base / 'source_vim.vim'),
                      debugger=[sys.executable, str(base / 'portable_ipdb.py'), '--plain'] + list(arguments), cwd=os.getcwd())
        config_path = root / 'config.json'
        config_path.write_text(json.dumps(config))
        if conemu:
            try:
                OpenConEmuVim(config_path)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                print('[ipdb] {}. Starting ipdb in the current ConEmu pane.'.format(error), flush=True)
                return WaitForDebugger(subprocess.Popen(config['debugger'], cwd=config['cwd']))
        try:
            if conemu:
                print('[ipdb] System Vim is in the lower ConEmu pane; ipdb commands stay here.', flush=True)
                return Debug(config)
            if terminal:
                print('[ipdb] Opening Windows Terminal: system Vim above, ipdb below.', flush=True)
                subprocess.check_call(TerminalCommand(terminal, 'ipdb-' + uuid.uuid4().hex, config_path))
                deadline = time.monotonic() + 20
                while not Path(config['done']).exists():
                    if Path(config['debug_pid']).exists():
                        if not IsRunning(int(Path(config['debug_pid']).read_text())):
                            raise RuntimeError('The Windows Terminal debugger pane was closed')
                    elif time.monotonic() > deadline:
                        raise RuntimeError('Windows Terminal did not start the debugger pane')
                    try:
                        time.sleep(0.1)
                    except KeyboardInterrupt:
                        print('[ipdb] Use q in the debugger pane, then Enter, to finish this session.', flush=True)
                return int(Path(config['result']).read_text())
            print('[ipdb] Windows Terminal is unavailable: opening system Vim in a separate window; ipdb stays here.', flush=True)
            subprocess.Popen([sys.executable, str(base / 'windows_vim.py'), '--vim', str(config_path)],
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
            return Debug(config)
        finally:
            Path(config['close']).write_text('close')
            deadline = time.monotonic() + 3
            while not Path(config['vim_result']).exists() and time.monotonic() < deadline:
                if Path(config['closed']).exists() and Path(config['closed']).read_text().strip() == 'kept':
                    break
                time.sleep(0.1)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] in ('--vim', '--debug'):
        sys.exit(Worker(sys.argv[1], sys.argv[2]))
