"""System Vim source view in an isolated tmux session."""
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
    temporary = str(path) + '.' + str(os.getpid()) + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as stream:
        json.dump({'file': os.path.abspath(filename), 'line': line, 'event': time.time()}, stream)
    os.replace(temporary, str(path))


def InstallHook(path):
    from IPython.core.debugger import Pdb
    original = Pdb.print_stack_entry
    original_where = Pdb.do_where

    def StackEntry(self, frame_lineno, *args, **kwargs):
        if getattr(self, '_portable_vim_explicit_stack', False):
            return original(self, frame_lineno, *args, **kwargs)
        frame, line = frame_lineno
        try:
            Publish(path, frame.f_code.co_filename, line)
        except OSError as error:
            self.error('Vim source sync failed: {}'.format(error))
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
    Pdb.do_where = Where
    Pdb.do_w = Where


def Shell(arguments):
    return 'exec ' + ' '.join(shlex.quote(str(value)) for value in arguments)


def VimCommand(vim, bridge):
    command = [str(vim)]
    if os.environ.get('IPDB_VIM_CLEAN') == '1':
        command.extend(['-N', '-u', 'NONE', '-i', 'NONE'])
    command.extend(['-S', str(bridge)])
    return command


def Worker(config_path):
    config = json.loads(Path(config_path).read_text())
    environment = dict(os.environ, IPDB_VIM_STATE=config['state'])
    code = subprocess.call(config['command'], env=environment)
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
    version = subprocess.check_output([vim, '--version'], universal_newlines=True)
    if any(feature not in version for feature in ('+timers', '+eval', '+signs')):
        raise RuntimeError('System Vim needs +timers, +eval and +signs (Vim 8 or newer)')
    base = Path(__file__).resolve().parent
    environment = dict(os.environ)
    environment.pop('TMUX', None)
    command = [tmux, '-L', 'ipdb-' + uuid.uuid4().hex, '-f', '/dev/null']
    def Tmux(*args):
        result = subprocess.run(command + list(args), env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if result.returncode:
            raise RuntimeError('tmux: ' + result.stderr.strip())
        return result.stdout.strip()
    if os.environ.get('IPDB_VIM_CLEAN') == '1':
        print('[ipdb] Diagnostic mode: user vimrc and plugins disabled for this session.', flush=True)
    print('[ipdb] Starting system Vim with your vimrc and plugins; Ctrl-B then k/j switches Vim/ipdb.', flush=True)
    with tempfile.TemporaryDirectory(prefix='ipdb-vim-') as directory:
        root = Path(directory)
        environment['IPDB_TMUX_BIN'] = tmux
        environment['IPDB_TMUX_SOCKET'] = command[2]
        environment['IPDB_VIM_STATE'] = str(root / 'source.json')
        environment['IPDB_VIM_READY'] = str(root / 'ready')
        config = {'state': environment['IPDB_VIM_STATE'], 'result': str(root / 'result'),
                  'command': [sys.executable, str(base / 'portable_ipdb.py'), '--plain'] + list(arguments), 'tmux': command}
        (root / 'config.json').write_text(json.dumps(config))
        size = shutil.get_terminal_size()
        try:
            source_id = Tmux('new-session', '-P', '-F', '#{pane_id}', '-d', '-s', 'debug', '-x', str(size.columns), '-y', str(size.lines), Shell(VimCommand(vim, base / 'source_vim.vim')))
            deadline = time.monotonic() + 20
            while not (root / 'ready').exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            if not (root / 'ready').exists():
                raise RuntimeError('Vim did not initialize in 20 seconds. Check vimrc/plugins. ' + Tmux('capture-pane', '-p', '-t', 'debug:0.0'))
            ready = (root / 'ready').read_text().strip()
            if ready != 'ready':
                raise RuntimeError(ready)
            Tmux('split-window', '-v', '-l', str(max(3, size.lines // 2)), '-t', 'debug:0.0', Shell([sys.executable, base / 'source_vim.py', '--worker', root / 'config.json']))
            Tmux('set-option', '-t', 'debug', 'mouse', 'on')
            mouse_action = 'select-pane -t = ; switch-client -T root ; if-shell -F "#{pane_in_mode}" "send-keys -X cancel" ; send-keys -M'
            Tmux('bind-key', '-n', 'MouseDown1Pane', mouse_action)
            Tmux('bind-key', '-T', 'ipdb-nav', 'MouseDown1Pane', mouse_action)
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
            Tmux('bind-key', '-T', 'ipdb-nav', 'C-w', 'send-keys C-w ; switch-client -T root')
            Tmux('select-pane', '-t', 'debug:0.1')
            code = subprocess.call(command + ['attach-session', '-t', 'debug'], env=environment)
            return int((root / 'result').read_text()) if (root / 'result').exists() else code
        finally:
            subprocess.run(command + ['kill-server'], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        sys.exit(Worker(sys.argv[2]))
