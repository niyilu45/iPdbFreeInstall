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


def Environment(config, origin=None):
    if origin is None:
        environment = dict(os.environ)
    else:
        # Keep the caller's Python environment and the new pane's own identity.
        environment = {name: value for name, value in origin.items() if not name.upper().startswith('CONEMU')}
        environment.update({name: value for name, value in os.environ.items() if name.upper().startswith('CONEMU')})
        environment.pop('IPY_TEST_SIMPLE_PROMPT', None)
    if 'conemu_helper' in config:
        environment['IPDB_CONEMU_HELPER'] = Path(config['conemu_helper']).as_posix()
        environment['IPDB_CONEMU_CONFIG'] = Path(config['config_path']).as_posix()
        environment['IPDB_CONEMU_PYTHON'] = Path(sys.executable).as_posix()
        environment['IPDB_CONEMU_LAUNCHER'] = Path(__file__).resolve().as_posix()
    return dict(environment, IPDB_VIM_STATE=Path(config['state']).as_posix(),
                IPDB_VIM_READY=Path(config['ready']).as_posix(), IPDB_VIM_CLOSE=Path(config['close']).as_posix(),
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
        origin = None
        if 'source_pane' in config:
            if not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
                raise RuntimeError('ConEmu did not attach a native debugger console')
            environment_file = Path(config['origin_environment'])
            origin = json.loads(environment_file.read_text(encoding='utf-8'))
            environment_file.unlink()
            ConfirmConEmuSplit(config['source_pane'])
        WaitReady(config)
        debugger = subprocess.Popen(config['debugger'], env=Environment(config, origin), cwd=config['cwd'])
        if 'debugger_pid' in config:
            try:
                WriteMarker(config['debugger_pid'], str(debugger.pid))
            except OSError as error:
                # The debugger is already running. Keep waiting for it.
                print('[ipdb] Could not record the debugger process: {}'.format(error), flush=True)
        code = WaitForDebugger(debugger)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
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


def WriteMarker(filename, content):
    path = Path(filename)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(content, encoding='ascii')
    temporary.replace(path)


def Worker(role, filename):
    try:
        config = json.loads(Path(filename).read_text())
        pane = 'source_pane' if role == '--vim' else 'debug_pane'
        if role == '--debug':
            WriteMarker(config['debug_pid'], str(os.getpid()))
        if pane in config:
            WriteMarker(config[pane], json.dumps({'server': os.environ.get('ConEmuServerPID'),
                                                  'draw': os.environ.get('ConEmuDrawHWND')}))
    except FileNotFoundError:
        print('[ipdb] The Vim session ended before this pane started.', flush=True)
        return 1
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
    if 'start' in config:
        try:
            WaitForLauncherStart(config)
        except RuntimeError as error:
            print('[ipdb] ' + str(error), flush=True)
            return 1
    return Debug(config)


def TerminalCommand(terminal, name, config_path):
    worker = str(Path(__file__).resolve())
    common = [sys.executable, worker]
    return [terminal, '-w', name, 'new-tab', '--title', 'ipdb source',
            *common, '--vim', str(config_path), ';', 'split-pane', '-H', '--size', '0.5',
            '--title', 'ipdb commands', *common, '--debug', str(config_path)]


def ConEmuHelper():
    directory = os.environ.get('ConEmuBaseDir')
    helper = shutil.which('ConEmuC64.exe', path=directory) or shutil.which('ConEmuC.exe', path=directory)
    if not helper:
        raise RuntimeError('ConEmuC was not found')
    return helper


def ConEmuMacro(macro, server=None, helper=None):
    helper = helper or os.environ.get('IPDB_CONEMU_HELPER') or ConEmuHelper()
    if server is None:
        current = os.environ.get('ConEmuServerPID', '')
        server = int(current) if current.isdecimal() else 0
    option = '-GuiMacro:{}'.format(server) if server else '-GuiMacro'
    # ConEmu reads the raw command-line remainder after -GuiMacro.
    call = subprocess.list2cmdline([helper, option]) + ' ' + macro
    result = subprocess.run(call, executable=helper, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5)
    if result.returncode:
        raise RuntimeError('ConEmu GUI macro failed with status {}'.format(result.returncode))
    return result.stdout.strip()


def ConEmuLaunch(verb, role, config_path):
    worker = str(Path(__file__).resolve())
    command = subprocess.list2cmdline([sys.executable, worker, role, str(config_path)])
    return 'Shell("new_console:{}","",{})'.format(verb, json.dumps(command, ensure_ascii=False))


def RequestConEmuPane(config, pane, macro, server, failure):
    try:
        reply = ConEmuMacro(macro, server=server, helper=config['conemu_helper'])
        detail = reply.decode('utf-8', errors='replace').strip()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        detail = str(error)
    # ConEmu may return a different macro reply even when the pane starts.
    # The actual worker writes its own identity before it can run the debugger.
    marker = Path(config[pane])
    deadline = time.monotonic() + 10
    while not marker.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('{} ({})'.format(failure, detail) if detail else failure)
        time.sleep(0.05)
    return json.loads(marker.read_text(encoding='ascii'))


def WaitForLauncherStart(config):
    start = Path(config['start'])
    close = Path(config['close'])
    deadline = time.monotonic() + 20
    while start.parent.is_dir() and not close.exists() and time.monotonic() < deadline:
        if start.exists():
            return
        time.sleep(0.05)
    raise RuntimeError('The launcher did not confirm the source and debugger panes')


def OpenConEmuVim(config, config_path):
    gui = os.environ.get('ConEmuPID', '')
    if not gui.isdecimal() or int(gui) == 0:
        raise RuntimeError('ConEmu window was not identified')
    # A clean tab in the existing window gives Vim the upper console. A new
    # native debugger console below it does not share Git Bash's MSYS pipe.
    source = RequestConEmuPane(config, 'source_pane', ConEmuLaunch('fnt:ipdb', '--vim', config_path),
                               int(gui), 'ConEmu did not create the Vim source tab')
    server = source.get('server') or ''
    if not server.isdecimal() or int(server) == 0:
        raise RuntimeError('ConEmu did not identify the Vim source pane')
    macro = 'Tab(7,-2);' + ConEmuLaunch('s50Vfn', '--debug', config_path)
    RequestConEmuPane(config, 'debug_pane', macro, int(server), 'ConEmu did not create the debugger pane below Vim')


def PaneBounds(handle):
    import ctypes
    from ctypes import wintypes

    gui = ctypes.WinDLL('user32', use_last_error=True)
    gui.IsWindowVisible.argtypes = [wintypes.HWND]
    gui.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    rect = wintypes.RECT()
    if gui.IsWindowVisible(handle) and gui.GetWindowRect(handle, ctypes.byref(rect)):
        if rect.top < rect.bottom and rect.left < rect.right:
            return rect.top, rect.bottom


def ConEmuSplitVisible(source, command):
    upper, lower = PaneBounds(source), PaneBounds(command)
    return bool(upper and lower and upper[1] <= lower[0])


def ConfirmConEmuSplit(source_pane):
    try:
        command = int(os.environ.get('ConEmuDrawHWND', ''), 16)
        source = int(json.loads(Path(source_pane).read_text(encoding='ascii')).get('draw') or '', 16)
    except (OSError, ValueError) as error:
        raise RuntimeError('ConEmu did not identify both pane windows') from error
    deadline = time.monotonic() + 3
    while not ConEmuSplitVisible(source, command):
        if time.monotonic() >= deadline:
            raise RuntimeError('ConEmu did not show Vim above the debugger pane')
        time.sleep(0.05)


def ConsoleRows():
    with open('CONOUT$', 'w') as console:
        return os.get_terminal_size(console.fileno()).lines


def ResizeConEmu(action, count):
    config = json.loads(Path(os.environ['IPDB_CONEMU_CONFIG']).read_text())
    source, debug = [json.loads(Path(config[name]).read_text(encoding='ascii'))
                     for name in ('source_pane', 'debug_pane')]
    current = os.environ.get('ConEmuServerPID')
    if current not in (source['server'], debug['server']):
        raise RuntimeError('ConEmu did not identify the active pane')
    upper, lower = [PaneBounds(int(pane['draw'], 16)) for pane in (source, debug)]
    if not upper or not lower or upper[1] > lower[0]:
        raise RuntimeError('ConEmu panes are not both visible')
    rows = ConsoleRows()
    if rows < 1:
        return
    upper_height = upper[1] - upper[0]
    lower_height = lower[1] - lower[0]
    source_active = current == source['server']
    active_height = upper_height if source_active else lower_height
    if action == '=':
        change = round((lower_height - upper_height) * rows / (2 * active_height))
    else:
        delta = max(1, min(count, 9999)) * (1 if action == '+' else -1)
        change = delta if source_active else -delta
    if not change:
        return
    shrinking_height = lower_height if change > 0 else upper_height
    steps = min(abs(change), round(shrinking_height * rows / active_height))
    # ConEmu rounds multi-row moves to the splitter grid and may lose a row.
    # Single-row moves keep the count exact and respect ConEmu's minimum size.
    move = 'Split(1,0,{})'.format(1 if change > 0 else -1)
    for offset in range(0, steps, 100):
        ConEmuMacro(';'.join([move] * min(100, steps - offset)))


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


def InstallConEmuKeys():
    from IPython.terminal.debugger import TerminalPdb
    from ipdb import __main__ as ipdb_main
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings

    original_init = TerminalPdb.pt_init
    original_class = ipdb_main._get_debugger_cls

    def Bindings(session):
        bindings = KeyBindings()
        count = None
        counted = Condition(lambda: count is not None)

        @bindings.add('<any>', filter=counted)
        def CancelCount(event):
            nonlocal count
            count = None
            event.key_processor.feed(event.key_sequence[0], first=True)

        def RememberDigit(event):
            nonlocal count
            count = min(9999, (count or 0) * 10 + int(event.data))

        for digit in '0123456789':
            bindings.add('c-w', digit)(RememberDigit)
            bindings.add(digit, filter=counted)(RememberDigit)

        def RunAction(event, action, *args):
            try:
                action(*args)
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
                event.app.output.write('\r\n[ipdb] {}'.format(error))
            event.app.invalidate()

        for key in '+-=':
            def Resize(event, operation=key):
                nonlocal count
                steps, count = count or 1, None
                RunAction(event, ResizeConEmu, operation, steps)
            bindings.add('c-w', key)(Resize)
            bindings.add(key, filter=counted)(Resize)

        directions = {'h': (-1, 0, 'left'), 'j': (0, 1, 'down'),
                      'k': (0, -1, 'up'), 'l': (1, 0, 'right')}
        for key, (dx, dy, arrow) in directions.items():
            def Move(event, x=dx, y=dy):
                RunAction(event, ConEmuMacro, 'Split(2,{},{})'.format(x, y))
            for suffix in (key, 'c-' + key, arrow):
                bindings.add('c-w', suffix)(Move)

        existing = session.key_bindings
        return merge_key_bindings([existing, bindings]) if existing is not None else bindings

    def Init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.pt_app.key_bindings = Bindings(self.pt_app)

    def DebuggerClass():
        original_class()  # Initialize the user's IPython shell normally.
        return TerminalPdb

    TerminalPdb.pt_init = Init
    ipdb_main._get_debugger_cls = DebuggerClass


def IsRunning(pid):
    """Return None when Windows cannot determine the process status."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        # ERROR_INVALID_PARAMETER for this PID means it no longer exists.
        # Access denied can also occur for a still-running elevated pane.
        return False if ctypes.get_last_error() == 87 else None
    try:
        status = kernel.WaitForSingleObject(handle, 0)
        return {0: False, 258: True}.get(status)
    finally:
        kernel.CloseHandle(handle)


def WaitForDebugPane(config, terminal):
    done = Path(config['done'])
    worker = Path(config['debug_pid'])
    debugger = Path(config['debugger_pid'])
    start = config.get('start')
    deadline = time.monotonic() + 20
    while True:
        try:
            if start is not None:
                WriteMarker(start, 'start')
                start = None
            if done.exists():
                return int(Path(config['result']).read_text())
            if worker.exists():
                if (IsRunning(int(worker.read_text())) is False
                        and (not debugger.exists() or IsRunning(int(debugger.read_text())) is False)
                        and not done.exists()):
                    raise RuntimeError('The {} debugger pane was closed'.format(terminal))
            elif time.monotonic() > deadline:
                raise RuntimeError('{} did not start the debugger pane'.format(terminal))
            time.sleep(0.1)
        except KeyboardInterrupt:
            print('[ipdb] Use q in the debugger pane, then Enter, to finish this session.', flush=True)


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
        config = {key: str(root / key) for key in
                  ('state', 'ready', 'close', 'closed', 'result', 'done', 'vim_result', 'debug_pid', 'debugger_pid')}
        from source_vim import VimCommand
        config.update(vim=VimCommand(vim, base / 'source_vim.vim'),
                      debugger=[sys.executable, str(base / 'portable_ipdb.py'), '--plain'] + list(arguments), cwd=os.getcwd())
        config_path = root / 'config.json'
        if conemu or terminal:
            config['start'] = str(root / 'start')
        if conemu:
            config.update(source_pane=str(root / 'source_pane'), debug_pane=str(root / 'debug_pane'),
                          conemu_helper=ConEmuHelper(), config_path=str(config_path),
                          origin_environment=str(root / 'origin_environment'))
            Path(config['origin_environment']).write_text(json.dumps(dict(os.environ)), encoding='utf-8')
        config_path.write_text(json.dumps(config))
        try:
            if conemu:
                print('[ipdb] Opening a ConEmu tab with Vim above ipdb.', flush=True)
                OpenConEmuVim(config, config_path)
                return WaitForDebugPane(config, 'ConEmu')
            if terminal:
                print('[ipdb] Opening Windows Terminal: system Vim above, ipdb below.', flush=True)
                subprocess.check_call(TerminalCommand(terminal, 'ipdb-' + uuid.uuid4().hex, config_path))
                return WaitForDebugPane(config, 'Windows Terminal')
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
    if len(sys.argv) == 4 and sys.argv[1] == '--resize' and sys.argv[2] in ('+', '-', '='):
        ResizeConEmu(sys.argv[2], int(sys.argv[3]))
