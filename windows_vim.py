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


VIM_FEATURES = (b'+timers', b'+eval', b'+signs')


def Environment(config):
    return dict(os.environ, IPDB_VIM_STATE=Path(config['state']).as_posix(),
                IPDB_VIM_READY=Path(config['ready']).as_posix(),
                IPDB_VIM_CLOSE=Path(config['close']).as_posix(),
                IPDB_VIM_CLOSED=Path(config['closed']).as_posix())


def WaitReady(config):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if Path(config['vim_result']).exists():
            raise RuntimeError('System Vim exited before debugging started; check your vimrc and plugins')
        if Path(config['ready']).exists():
            message = Path(config['ready']).read_text().strip()
            if message != 'ready':
                raise RuntimeError(message)
            return
        time.sleep(0.1)
    raise RuntimeError('Vim did not initialize in 20 seconds; check its window for vimrc/plugin errors')


def Debug(config):
    from source_vim import WaitForDebugger
    code = 1
    try:
        WaitReady(config)
        if 'source_draw' in config:
            RestoreConEmuSplit(config['source_draw'])
            print('[ipdb] System Vim is in the lower ConEmu pane; ipdb commands stay here.', flush=True)
        debugger = subprocess.Popen(config['debugger'], env=Environment(config), cwd=config['cwd'])
        code = WaitForDebugger(debugger)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('[ipdb] ' + str(error), flush=True)
    finally:
        Path(config['result']).write_text(str(code))
        print('[ipdb] Debugger exited with status {}. Press Enter to finish. Unsaved Vim buffers stay open.'.format(code), flush=True)
        try:
            while input():
                pass
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
        draw = os.environ.get('ConEmuDrawHWND')
        if draw and 'source_draw' in config:
            Path(config['source_draw']).write_text(draw, encoding='ascii')
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


def ConEmuMacro(macro):
    directory = os.environ.get('ConEmuBaseDir')
    helper = shutil.which('ConEmuC64.exe', path=directory) or shutil.which('ConEmuC.exe', path=directory)
    if not helper:
        raise RuntimeError('ConEmuC was not found')
    server = os.environ.get('ConEmuServerPID', '')
    server = int(server) if server.isdecimal() else 0
    option = '-GuiMacro:{}'.format(server) if server else '-GuiMacro'
    # ConEmu reads the raw command-line remainder after -GuiMacro.
    call = subprocess.list2cmdline([helper, option]) + ' ' + macro
    result = subprocess.run(call, executable=helper, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5)
    if result.returncode:
        raise RuntimeError('ConEmu GUI macro failed with status {}'.format(result.returncode))
    return result.stdout.strip()


def OpenConEmuVim(config_path):
    worker = str(Path(__file__).resolve())
    command = subprocess.list2cmdline([sys.executable, worker, '--vim', str(config_path)])
    # Shell splits the active pane, so activate the caller in the same macro.
    macro = 'Tab(7,-2);Shell("new_console:s50Vbn","",{})'.format(json.dumps(command, ensure_ascii=False))
    if ConEmuMacro(macro) != b'OK;OK':
        raise RuntimeError('ConEmu did not create the Vim pane')


def ConEmuSplitVisible(command, source):
    import ctypes
    from ctypes import wintypes

    gui = ctypes.WinDLL('user32', use_last_error=True)
    gui.IsWindow.argtypes = gui.IsWindowVisible.argtypes = [wintypes.HWND]
    gui.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    if not (gui.IsWindow(command) and gui.IsWindow(source) and gui.IsWindowVisible(command)):
        return None  # The user may have switched to a different tab.
    if not gui.IsWindowVisible(source):
        return False
    upper, lower = wintypes.RECT(), wintypes.RECT()
    if not (gui.GetWindowRect(command, ctypes.byref(upper)) and gui.GetWindowRect(source, ctypes.byref(lower))):
        return None
    return upper.bottom <= lower.top < lower.bottom and lower.left < lower.right


def RestoreConEmuSplit(source_draw):
    try:
        command = int(os.environ.get('ConEmuDrawHWND', ''), 16)
        source = int(Path(source_draw).read_text(encoding='ascii'), 16)
    except (OSError, ValueError):
        return
    if ConEmuSplitVisible(command, source) is not False:
        return
    # A new pane can inherit a maximized group's full-size rectangle. ConEmu
    # can return InvalidArg after toggling, so check the layout itself.
    ConEmuMacro('Split(3)')
    deadline = time.monotonic() + 2
    while ConEmuSplitVisible(command, source) is False:
        if time.monotonic() >= deadline:
            raise RuntimeError('ConEmu did not make the Vim source pane visible')
        time.sleep(0.05)


def SelectVim(conemu):
    vim = shutil.which('vim')
    if not vim:
        raise RuntimeError('System vim.exe was not found on PATH. Add your Vim installation directory to PATH.')
    version = subprocess.check_output([vim, '--version'])
    if conemu and b'MS-Windows' not in version:
        # Git Bash puts its MSYS Vim before native Vim on PATH. The native
        # build can use the Windows Vim configuration and console together.
        checked = {os.path.normcase(os.path.abspath(vim))}
        for directory in os.get_exec_path():
            candidate = Path(directory.strip('"')) / 'vim.exe'
            name = os.path.normcase(os.path.abspath(str(candidate)))
            if name in checked or not candidate.is_file():
                continue
            checked.add(name)
            try:
                features = subprocess.check_output([str(candidate), '--version'], timeout=5)
            except (OSError, subprocess.SubprocessError):
                continue
            if b'MS-Windows' in features and all(feature in features for feature in VIM_FEATURES):
                return str(candidate)
    if any(feature not in version for feature in VIM_FEATURES):
        raise RuntimeError('System Vim needs +timers, +eval and +signs (Vim 8 or newer)')
    return vim


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
    conemu = bool(os.environ.get('ConEmuPID'))
    shell_tty = os.environ.pop('IPDB_CONEMU_TTY', '') == '1'
    if not ((conemu and shell_tty) or (sys.stdin.isatty() and sys.stdout.isatty())):
        raise RuntimeError('System Vim mode requires an interactive Windows console')
    if not arguments:
        raise RuntimeError('Usage: ipdbVim.cmd script.py [arguments]')
    vim = SelectVim(conemu)
    base = Path(__file__).resolve().parent
    terminal = None if conemu else shutil.which('wt.exe')
    with tempfile.TemporaryDirectory(prefix='ipdb-vim-') as directory:
        root = Path(directory)
        config = {key: str(root / key) for key in ('state', 'ready', 'close', 'closed', 'result', 'done', 'vim_result', 'debug_pid')}
        from source_vim import VimCommand, WaitForDebugger
        config.update(vim=VimCommand(vim, base / 'source_vim.vim'),
                      debugger=[sys.executable, str(base / 'portable_ipdb.py'), '--plain'] + list(arguments), cwd=os.getcwd())
        if conemu:
            config['source_draw'] = str(root / 'source_draw')
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
