"""Exercise real Vim/ipdb/tmux panes and retain cell-level rendering evidence."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time


def Main():
    import fcntl
    import pty
    import struct
    import termios
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--export-size', action='store_true')
    parser.add_argument('--konsole', action='store_true')
    parser.add_argument('--stress-content', action='store_true')
    parser.add_argument('--vim-term')
    parser.add_argument('--source-rows', type=int, default=15)
    parser.add_argument('--swap-conflict', action='store_true')
    args = parser.parse_args()
    root = Path(args.output).resolve()
    if (root / 'results.json').exists():
        parser.error('Choose a new output directory to preserve previous evidence')
    root.mkdir(parents=True, exist_ok=True)
    source = root / 'scenario_main.py'
    helper = root / 'scenario_helper.py'
    lines = ['import scenario_helper', 'value = 0']
    for number in range(3, 241):
        lines.append('value = {} # MAIN_{:04d}_{}'.format(number, number, 'M' * (150 if number % 2 else 2)))
    lines[119] = 'result = scenario_helper.Work() # CALL_HELPER'
    lines.append('print("FINISHED", value)')
    helpers = ['def Work():', '    value = 0']
    for number in range(3, 90):
        helpers.append('    value = {} # HELPER_{:04d}_{}'.format(number, number, 'H' * (140 if number % 2 else 2)))
    helpers.append('    return value')
    if args.stress_content:
        for content in (lines, helpers):
            for index, line in enumerate(content):
                if re.search(r'(?:MAIN|HELPER)_\d+', line):
                    content[index] += '\t' + (chr(0x754c) + 'e\u0301' + chr(0x1f642) + ' ') * (80 if index % 3 else 2)
    source.write_text('\n'.join(lines) + '\n')
    helper.write_text('\n'.join(helpers) + '\n')
    (root / '.vimrc').write_text('set nocompatible\nset noswapfile\nsyntax on\nset background=dark\n')
    master, slave = pty.openpty()
    terminal_rows = 2 * args.source_rows + 4
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', terminal_rows, 234, 0, 0))
    env = dict(os.environ, HOME=str(root), TMPDIR=str(root), IPYTHONDIR=str(root / 'ipython'),
               TERM='xterm-256color', IPDB_PYTHON=sys.executable)
    if args.export_size:
        env.update(LINES=str(terminal_rows), COLUMNS='234')
    else:
        env.pop('LINES', None)
        env.pop('COLUMNS', None)
    if args.vim_term:
        shim = root / 'bin'
        shim.mkdir(exist_ok=True)
        executable = shim / 'vim'
        executable.write_text('#!/bin/sh\nTERM=' + shlex.quote(args.vim_term) + ' exec ' + shlex.quote(shutil.which('vim')) + ' "$@"\n')
        executable.chmod(0o755)
        env['PATH'] = str(shim) + ':' + env['PATH']
    launcher = Path(__file__).with_name('ipdbVim.sh')
    holder = None
    if args.swap_conflict:
        holder = subprocess.Popen(['vim', '-N', '-u', 'NONE', '-i', 'NONE', '-es', str(source), '-c', 'sleep 120'],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        deadline = time.monotonic() + 5
        while not list(root.glob('.*.swp')) and time.monotonic() < deadline:
            time.sleep(0.05)
        # Enable the second editor's normal swap check for this scenario.
        (root / '.vimrc').write_text('set nocompatible\nsyntax on\nset background=dark\n')
    command = ['sh', str(launcher), str(source)]
    if args.konsole:
        env['QT_QPA_PLATFORM'] = 'xcb'
        command = ['konsole', '--separate', '--fallback-profile', '--hide-menubar', '--hide-tabbar',
                   '-p', 'TerminalColumns=234', '-p', 'TerminalRows=' + str(terminal_rows), '-e'] + command
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    def Drain():
        with (root / 'terminal.bin').open('wb') as stream:
            while True:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                stream.write(chunk)
                stream.flush()
    reader = threading.Thread(target=Drain, daemon=True)
    reader.start()
    tmux = None
    records = []
    def Wait(predicate, label):
        end = time.monotonic() + 15
        while time.monotonic() < end:
            try:
                if predicate():
                    return
            except subprocess.CalledProcessError:
                pass
            if process.poll() is not None:
                raise RuntimeError('Launcher exited during ' + label)
            time.sleep(0.05)
        raise RuntimeError('Timed out: ' + label)
    def Tmux(*arguments):
        return subprocess.check_output(tmux + list(arguments), env=env, stderr=subprocess.PIPE, universal_newlines=True)
    def Capture(pane='0', history=False):
        extra = ['-S', '-1000'] if history else []
        return Tmux('capture-pane', '-p', '-t', 'debug:0.' + pane, *extra)
    def Vim(keys):
        Tmux('send-keys', '-t', 'debug:0.0', keys)
        time.sleep(0.18)
    def Debug(command, token):
        Tmux('send-keys', '-t', 'debug:0.1', '-l', command)
        Tmux('send-keys', '-t', 'debug:0.1', 'Enter')
        Wait(lambda: token in Capture('1', True), command)
        time.sleep(0.18)
    def Check(name, file_lines, prefix, target=None):
        time.sleep(0.15)
        screen = Capture()
        (root / (name + '.txt')).write_text(screen)
        expected = {line.strip() for line in file_lines}
        visible = [line.strip().lstrip('>').strip() for line in screen.splitlines() if re.search(r'(?:MAIN|HELPER)_\d+', line)]
        bad = [] if args.stress_content else [line for line in visible if line not in expected]
        ok = bool(visible) and not bad and (target is None or '{}_{:04d}_'.format(prefix, target) in screen)
        records.append({'case': name, 'ok': ok, 'bad_lines': bad, 'visible_rows': len(visible)})
        print(name, 'PASS' if ok else 'FAIL', flush=True)
        if args.stress_content:
            # Paging reportedly clears the defect. Restore the exact original
            # viewport afterward and compare both screen cell snapshots.
            Tmux('send-keys', '-t', 'debug:0.0', '-l', ':let g:probe_view=winsaveview() | execute "normal! \\<C-f>" | redraw | call winrestview(g:probe_view) | redraw!')
            Tmux('send-keys', '-t', 'debug:0.0', 'Enter')
            time.sleep(0.25)
            restored = Capture()
            (root / (name + '-after-page.txt')).write_text(restored)
            unchanged = screen.splitlines()[:-2] == restored.splitlines()[:-2]
            records.append({'case': name + '-page-roundtrip', 'ok': unchanged})
            print(name + '-page-roundtrip', 'PASS' if unchanged else 'FAIL', flush=True)
        return ok
    try:
        Wait(lambda: bool(list(root.glob('ipdb-vim-*/config.json'))), 'session config')
        config = json.loads(next(root.glob('ipdb-vim-*/config.json')).read_text())
        tmux = config['tmux']
        Wait(lambda: 'ipdb>' in Capture('1'), 'debugger prompt')
        if args.swap_conflict:
            Wait(lambda: '(E)dit anyway' in Capture() or 'MAIN_0003_' in Capture(), 'swap warning')
            warning = Capture()
            (root / 'swap-warning.txt').write_text(warning)
            if '(E)dit anyway' in warning:
                Tmux('send-keys', '-t', 'debug:0.0', 'e')
        Wait(lambda: 'MAIN_0003_' in Capture(), 'first source')
        diagnostic = ':call writefile([json_encode({"term":$TERM,"version":v:version,"lines":&lines,"columns":&columns,"view":winsaveview()})], ' + json.dumps(str(root / 'vim-state.json')) + ')'
        Tmux('send-keys', '-t', 'debug:0.0', '-l', diagnostic)
        Tmux('send-keys', '-t', 'debug:0.0', 'Enter')
        time.sleep(0.2)
        (root / 'versions.txt').write_text(subprocess.check_output(['vim', '--version'], env=env, text=True)
                                         + subprocess.check_output(['tmux', '-V'], env=env, text=True)
                                         + Tmux('display-message', '-p', '-t', 'debug:0.0', '#{pane_width}x#{pane_height}'))
        Check('initial', lines, 'MAIN', 3)
        for key, target in [('G', 240), ('10G', 10), ('190G', 190), ('35G', 35), ('120G', None)]:
            Vim(key)
            Check('jump-' + key, lines, 'MAIN', target)
        Vim('C-l')
        Check('ctrl-l', lines, 'MAIN')
        for key in ('C-f', 'C-b', 'C-d', 'C-u'):
            Vim(key)
            Check('page-' + key, lines, 'MAIN')
        Debug('b 120', 'Breakpoint 1')
        Debug('c', 'ipdb> c')
        Wait(lambda: 'CALL_HELPER' in Capture(), 'call location')
        Check('continue-to-call', lines, 'MAIN')
        Debug('s', 'ipdb> s')
        Wait(lambda: 'def Work' in Capture() or 'HELPER_' in Capture(), 'step to helper')
        Check('step-new-file', helpers, 'HELPER')
        for key, target in [('G', 89), ('8G', 8), ('65G', 65), ('15G', 15)]:
            Vim(key)
            Check('helper-' + key, helpers, 'HELPER', target)
        for number in range(5):
            Debug('!print("DEBUG_OUTPUT_{}" * 40)'.format(number), 'DEBUG_OUTPUT_{}'.format(number))
            Check('debug-output-' + str(number), helpers, 'HELPER', 15)
        Debug('!print("\\n".join("SCROLL_%04d" % i for i in range(150)))', 'SCROLL_0149')
        Check('debug-scroll', helpers, 'HELPER', 15)
        for number in range(8):
            Tmux('send-keys', '-t', 'debug:0.0', 'G8G65G15G')
            Tmux('send-keys', '-t', 'debug:0.1', '-l', 'n')
            Tmux('send-keys', '-t', 'debug:0.1', 'Enter')
            time.sleep(0.25)
            Check('interleaved-step-' + str(number), helpers, 'HELPER')
        if args.konsole:
            windows = subprocess.check_output(['xdotool', 'search', '--pid', str(process.pid), '--class', 'konsole'], env=env, text=True).splitlines()
            if not windows:
                raise RuntimeError('Konsole test window not found')
            window = windows[-1]
            for direction in ('5', '4'):
                subprocess.check_call(['xdotool', 'mousemove', '--window', window, '120', '100', 'click', '--repeat', '8', '--delay', '30', direction], env=env)
                time.sleep(0.2)
                Check('mouse-wheel-' + direction, helpers, 'HELPER')
            for width, height in ((1700, 900), (2200, 1300)):
                subprocess.check_call(['xdotool', 'windowsize', window, str(width), str(height)], env=env)
                time.sleep(0.3)
                Vim('35G')
                Check('konsole-resize-' + str(width), helpers, 'HELPER', 35)
        for height in (8, 23, 16):
            Tmux('resize-pane', '-t', 'debug:0.0', '-y', str(height))
            time.sleep(0.2)
            Vim('35G')
            Check('resize-' + str(height), helpers, 'HELPER', 35)
        for command in (':split', ':vsplit'):
            Vim(command)
            Vim('Enter')
            for key in ('G', '12G', '65G'):
                Vim(key)
                screen = Capture()
                name = command[1:] + '-' + key
                (root / (name + '.txt')).write_text(screen)
                # A vertical split has two independent code strings per row.
                pieces = re.findall(r'value = \d+ # HELPER_\d+_H+', screen)
                expected = {line.strip() for line in helpers}
                # Width truncation is valid, mixed row identifiers are not.
                bad = [piece for piece in pieces if not any(line.startswith(piece) for line in expected)]
                records.append({'case': name, 'ok': bool(pieces) and not bad, 'bad_lines': bad})
                print(name, 'PASS' if bool(pieces) and not bad else 'FAIL', flush=True)
            Vim(':q')
            Vim('Enter')
        Vim(':ls')
        Vim('Enter')
        before = Capture()
        time.sleep(0.4)
        after = Capture()
        records.append({'case': 'ls-output-stays', 'ok': before == after})
        (root / 'ls.txt').write_text(after)
        Vim('Enter')
        Vim('Escape')
        Tmux('send-keys', '-t', 'debug:0.0', '-l', ':IpdbViewDump ' + str(root / 'view-dump.json'))
        Tmux('send-keys', '-t', 'debug:0.0', 'Enter')
        Wait(lambda: (root / 'view-dump.json').exists(), 'diagnostic export')
        diagnostic = json.loads((root / 'view-dump.json').read_text())
        records.append({'case': 'diagnostic-export', 'ok': bool(diagnostic.get('tmux_screen')) and bool(diagnostic.get('vim_screen'))})
        Debug('q', 'Press Enter')
        Tmux('send-keys', '-t', 'debug:0.1', 'Enter')
        process.wait(timeout=10)
    finally:
        (root / 'results.json').write_text(json.dumps(records, indent=2))
        if tmux:
            for pane in ('0', '1'):
                try:
                    (root / ('final-pane-' + pane + '.txt')).write_text(Capture(pane))
                except subprocess.CalledProcessError:
                    pass
            subprocess.run(tmux + ['kill-server'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        os.close(master)
        if holder is not None and holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)
    return int(any(not record['ok'] for record in records))


if __name__ == '__main__':
    sys.exit(Main())
