"""System Vim source view in an isolated tmux session."""
import errno
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


def Publish(path, filename, line):
    target = Path(path)
    message = {'file': os.path.abspath(filename), 'line': line, 'event': time.time()}
    for delay in (0.02, 0.05, 0.1, 0):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=str(target.parent),
                                             prefix=target.name + '.', suffix='.tmp', delete=False) as stream:
                temporary = stream.name
                json.dump(message, stream)
            os.replace(temporary, str(target))
            temporary = None
            return
        except (FileNotFoundError, PermissionError) as error:
            if isinstance(error, FileNotFoundError) and not target.parent.is_dir():
                raise FileNotFoundError(errno.ENOENT, 'Vim session directory is missing', str(target.parent)) from error
            if not delay:
                raise
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except (FileNotFoundError, PermissionError):
                    pass
        time.sleep(delay)


def ViewCode(before, after):
    if before['pane'] != after['pane'] or before['size'] != after['size']:
        return 'R4'
    if before['text'] != after['text']:
        return 'R1'
    if before['style'] != after['style']:
        return 'R2'
    return 'R3'


def CaptureView():
    command = [os.environ['IPDB_TMUX_BIN'], '-L', os.environ['IPDB_TMUX_SOCKET']]
    def Call(*arguments):
        return subprocess.check_output(command + list(arguments), stderr=subprocess.PIPE, universal_newlines=True)
    panes = Call('list-panes', '-t', os.environ['TMUX_PANE'], '-F', '#{pane_index} #{pane_id}')
    pane = next(line.split()[1] for line in panes.splitlines() if line.split()[0] == '0')
    size = Call('display-message', '-p', '-t', pane, '#{pane_width}x#{pane_height}').strip()
    # Ignore Vim's command/status rows, which can change on a focus switch.
    text = Call('capture-pane', '-p', '-t', pane).splitlines()[:-2]
    style = Call('capture-pane', '-p', '-e', '-t', pane).splitlines()[:-2]
    return {'pane': pane, 'size': size, 'text': text, 'style': style}


def ProbeCode(snapshot, report):
    if snapshot['size'] != '{}x{}'.format(report['columns'], report['lines']):
        return 'V3 vim={}x{} tmux={}'.format(report['columns'], report['lines'], snapshot['size'])
    if not report['mode'].startswith('n'):
        return 'V4 mode=' + report['mode']
    checked, different = 0, []
    for index, (actual, expected) in enumerate(zip(snapshot['text'], report['screen'][:-2])):
        # Avoid guessing character-cell widths for wide/combining characters.
        if any(ord(character) > 126 for character in actual + expected):
            continue
        checked += 1
        if actual.rstrip() != expected.rstrip():
            different.append(index + 1)
    if not checked:
        return 'V4 no-ascii-rows'
    if different:
        return 'V2 rows={} first={}'.format(len(different), different[0])
    return 'V1 rows={}'.format(checked)


def ProbeView(path):
    before = CaptureView()
    request = Path(str(path) + '.probe-request')
    response = Path(str(path) + '.probe-response')
    token = '{}-{}'.format(os.getpid(), time.time())
    temporary = request.with_suffix('.tmp')
    temporary.write_text(json.dumps({'token': token}))
    os.replace(str(temporary), str(request))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            report = json.loads(response.read_text())
            if report.get('token') == token:
                after = CaptureView()
                if before['pane'] != after['pane'] or before['size'] != after['size'] or before['text'] != after['text']:
                    return 'V5 changing; run vd again when idle'
                return ProbeCode(after, report)
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    return 'V0 timeout; restart with the updated Vim bridge'


def RefreshClient():
    command = [os.environ['IPDB_TMUX_BIN'], '-L', os.environ['IPDB_TMUX_SOCKET']]
    session = subprocess.check_output(command + ['display-message', '-p', '-t', os.environ['TMUX_PANE'], '#{session_id}'], universal_newlines=True).strip()
    clients = subprocess.check_output(command + ['list-clients', '-t', session, '-F', '#{client_name}'], universal_newlines=True).splitlines()
    for client in clients:
        subprocess.check_call(command + ['refresh-client', '-t', client], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return len(clients)


def InstallHook(path):
    from IPython.core.debugger import Pdb
    original = Pdb.print_stack_entry
    original_where = Pdb.do_where

    def ViewDiagnostic(self, argument):
        """vd: compare Vim's cached cells with tmux without changing focus."""
        try:
            self.message(ProbeView(path))
        except (KeyError, OSError, subprocess.CalledProcessError, StopIteration) as error:
            self.error('V0: {}'.format(type(error).__name__))

    def ViewRefresh(self, argument):
        """vr: ask tmux to repaint attached clients, without changing Vim state."""
        try:
            count = RefreshClient()
            self.message('F1: Repainted {} client(s). Did the residue disappear?'.format(count))
        except (KeyError, OSError, subprocess.CalledProcessError) as error:
            self.error('F0: {}'.format(type(error).__name__))

    def ViewCheck(self, argument):
        """vc: capture first, compare on the next call after a focus switch."""
        action = argument.strip()
        action = {'s': 'start', 'e': 'end'}.get(action, action)
        if not action:
            action = 'end' if getattr(self, '_portable_view_before', None) is not None else 'start'
        if action not in ('start', 'end'):
            self.message('Usage: vc; switch to Vim and back without moving; vc again. Explicit: vc s / vc e.')
            return
        try:
            snapshot = CaptureView()
        except (KeyError, OSError, subprocess.CalledProcessError, StopIteration) as error:
            self.error('R0: Cannot capture source pane ({})'.format(type(error).__name__))
            return
        if action == 'start':
            self._portable_view_before = snapshot
            self.message('S0: Captured. Switch to Vim and back without typing or resizing, then run vc again.')
            return
        before = getattr(self, '_portable_view_before', None)
        if before is None:
            self.error('R0: Run vc first while the defect is visible.')
            return
        code = ViewCode(before, snapshot)
        meanings = {'R1': 'tmux source text changed', 'R2': 'text unchanged; terminal styles changed',
                    'R3': 'tmux text and styles unchanged; compare client rendering',
                    'R4': 'pane or size changed; repeat without resizing'}
        self.message('{}: {}'.format(code, meanings[code]))
        self._portable_view_before = None

    def SyncSource(self, filename, line):
        try:
            Publish(path, filename, line)
        except OSError as error:
            self.error('Vim source sync failed: {}'.format(error))
            return False
        return True

    def StackEntry(self, frame_lineno, *args, **kwargs):
        if getattr(self, '_portable_vim_explicit_stack', False):
            return original(self, frame_lineno, *args, **kwargs)
        frame, line = frame_lineno
        location = getattr(self, '_portable_syntax_location', (frame.f_code.co_filename, line))
        if not SyncSource(self, *location):
            return original(self, frame_lineno, *args, **kwargs)
        show = getattr(self, '_portable_show_displays', None)
        if show is not None:
            show(frame)

    def Where(self, argument):
        previous = getattr(self, '_portable_vim_explicit_stack', False)
        self._portable_vim_explicit_stack = True
        try:
            return original_where(self, argument)
        finally:
            self._portable_vim_explicit_stack = previous

    Pdb.print_stack_entry = StackEntry
    Pdb._portable_vim_sync = SyncSource
    Pdb.do_where = Where
    Pdb.do_w = Where
    Pdb.do_viewcheck = ViewCheck
    Pdb.do_vc = ViewCheck
    Pdb.do_vd = ViewDiagnostic
    Pdb.do_vr = ViewRefresh


def Shell(arguments):
    return 'exec ' + ' '.join(shlex.quote(str(value)) for value in arguments)


def VimCommand(vim, bridge):
    command = [str(vim)]
    if os.environ.get('IPDB_VIM_CLEAN') == '1':
        command.extend(['-N', '-u', 'NONE', '-i', 'NONE'])
    # Vim parses -S as an Ex filename, where Windows backslashes are escapes.
    command.extend(['-S', Path(bridge).as_posix()])
    return command


def WaitForDebugger(process):
    # The terminal delivers Ctrl+C to both the launcher and the debugger.
    # Keep waiting while the debugger handles it in the running program.
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            continue


def Worker(config_path):
    config = json.loads(Path(config_path).read_text())
    environment = dict(os.environ, IPDB_VIM_STATE=config['state'])
    debugger = subprocess.Popen(config['command'], env=environment, cwd=config.get('cwd'))
    code = WaitForDebugger(debugger)
    Path(config['result']).write_text(str(code))
    print('[ipdb] Debugger exited with status {}. Press Enter to close Vim and return.'.format(code), flush=True)
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass
    subprocess.call(config['tmux'] + ['kill-session', '-t', 'debug'])
    return code


def Run(arguments):
    if sys.platform == 'win32':
        from windows_vim import Run as RunWindows
        return RunWindows(arguments)
    if not sys.platform.startswith('linux') or not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError('--source-vim requires an interactive Linux terminal')
    vim, tmux = shutil.which('vim'), shutil.which('tmux')
    if not vim or not tmux:
        raise RuntimeError('--source-vim requires system vim and tmux on PATH; use --tui for the built-in view')
    if not arguments:
        raise RuntimeError('Usage: ./ipdb.sh --source-vim script.py [arguments]')
    version = subprocess.check_output([vim, '--version'])
    if any(feature not in version for feature in (b'+timers', b'+eval', b'+signs')):
        raise RuntimeError('System Vim needs +timers, +eval and +signs (Vim 8 or newer)')
    base = Path(__file__).resolve().parent
    environment = dict(os.environ)
    for key in ('TMUX', 'TMUX_PANE', 'IPDB_VIM_CLOSE', 'IPDB_VIM_CLOSED', 'IPDB_VIM_READY', 'IPDB_VIM_STATE', 'IPDB_TMUX_BIN', 'IPDB_TMUX_SOCKET'):
        environment.pop(key, None)
    command = [tmux, '-L', 'ipdb-' + uuid.uuid4().hex, '-f', '/dev/null']
    def Tmux(*args):
        result = subprocess.run(command + list(args), env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if result.returncode:
            raise RuntimeError('tmux: ' + result.stderr.strip())
        return result.stdout.strip()
    if os.environ.get('IPDB_VIM_CLEAN') == '1':
        print('[ipdb] Diagnostic mode: user vimrc and plugins disabled for this session.', flush=True)
        print('[ipdb] To restore your configuration, launch with IPDB_VIM_CLEAN=0.', flush=True)
    else:
        print('[ipdb] Starting system Vim with your vimrc and plugins; Ctrl-B then k/j switches Vim/ipdb.', flush=True)
    with tempfile.TemporaryDirectory(prefix='ipdb-vim-') as directory:
        root = Path(directory)
        environment['IPDB_TMUX_BIN'] = tmux
        environment['IPDB_TMUX_SOCKET'] = command[2]
        environment['IPDB_VIM_STATE'] = str(root / 'source.json')
        environment['IPDB_VIM_READY'] = str(root / 'ready')
        config = {'cwd': os.getcwd(), 'state': environment['IPDB_VIM_STATE'], 'result': str(root / 'result'),
                  'command': [sys.executable, str(base / 'portable_ipdb.py'), '--plain'] + list(arguments), 'tmux': command}
        (root / 'config.json').write_text(json.dumps(config))
        size = shutil.get_terminal_size()
        try:
            source_id = Tmux('new-session', '-P', '-F', '#{pane_id}', '-d', '-s', 'debug', '-c', config['cwd'], '-x', str(size.columns), '-y', str(size.lines), Shell(VimCommand(vim, base / 'source_vim.vim')))
            deadline = time.monotonic() + 20
            while not (root / 'ready').exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            if not (root / 'ready').exists():
                raise RuntimeError('Vim did not initialize in 20 seconds. Check vimrc/plugins. ' + Tmux('capture-pane', '-p', '-t', 'debug:0.0'))
            ready = (root / 'ready').read_text().strip()
            if ready != 'ready':
                raise RuntimeError(ready)
            Tmux('split-window', '-c', config['cwd'], '-v', '-l', str(max(3, size.lines // 2)), '-t', 'debug:0.0', Shell([sys.executable, base / 'source_vim.py', '--worker', root / 'config.json']))
            Tmux('set-option', '-t', 'debug', 'mouse', 'on')
            Tmux('set-option', '-g', 'focus-events', 'on')
            # Show actual focus, not the asynchronously renamed window command.
            Tmux('set-window-option', '-t', 'debug:0', 'automatic-rename', 'off')
            Tmux('set-window-option', '-t', 'debug:0', 'window-status-current-format',
                 '#I:#{?#{==:#{pane_id},' + source_id + '},VIM,IPDB}#F')
            mouse_action = 'select-pane -t = ; switch-client -T root ; if-shell -F "#{pane_in_mode}" "send-keys -X cancel" ; send-keys -M'
            for table in ('root', 'ipdb-nav', 'copy-mode', 'copy-mode-vi'):
                Tmux('bind-key', '-T', table, 'MouseDown1Pane', mouse_action)
            Tmux('bind-key', 'k', 'select-pane', '-U')
            Tmux('bind-key', 'j', 'select-pane', '-D')
            def ResizeCommand(key):
                return Shell([sys.executable, base / 'tmux_resize.py', tmux, command[2], '#{pane_id}', '#{client_name}', key])
            Tmux('bind-key', '-n', 'C-w', 'if-shell', '-F', '#{==:#{pane_id},' + source_id + '}', 'send-keys C-w', 'set-option -p @ipdb-resize-count "" ; switch-client -T ipdb-nav')
            for key in '0123456789':
                Tmux('bind-key', '-T', 'ipdb-nav', key,
                     'set-option -p -F @ipdb-resize-count "#{@ipdb-resize-count}' + key + '" ; switch-client -T ipdb-nav')
            for key in '+-=':
                Tmux('bind-key', '-T', 'ipdb-nav', key, 'run-shell ' + shlex.quote(ResizeCommand(key)) + ' ; switch-client -T root')
            for key, direction in (('h', '-L'), ('j', '-D'), ('k', '-U'), ('l', '-R')):
                Tmux('bind-key', '-T', 'ipdb-nav', key, 'select-pane ' + direction + ' ; switch-client -T root')
                Tmux('bind-key', '-T', 'ipdb-nav', 'C-' + key, 'select-pane ' + direction + ' ; switch-client -T root')
            Tmux('bind-key', '-T', 'ipdb-nav', 'C-w', 'send-keys C-w ; switch-client -T root')
            # A fast Ctrl-W sequence is navigation, even when all keys arrive together.
            Tmux('set-option', '-t', 'debug', 'assume-paste-time', '0')
            Tmux('select-pane', '-t', 'debug:0.1')
            code = subprocess.call(command + ['attach-session', '-t', 'debug'], env=environment)
            return int((root / 'result').read_text()) if (root / 'result').exists() else code
        finally:
            subprocess.run(command + ['kill-server'], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        sys.exit(Worker(sys.argv[2]))
