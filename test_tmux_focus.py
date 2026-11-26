"""Exercise window focus and interrupts through a real tmux client."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('vim') and shutil.which('tmux'), 'Linux Vim/tmux required')
class FocusTests(unittest.TestCase):
    @contextmanager
    def LaunchTmux(self, root, script, **environment_overrides):
        import fcntl
        import pty
        import struct
        import termios

        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
            environment = dict(os.environ, TERM='xterm-256color', TMPDIR=str(root), HOME=str(root),
                               IPYTHONDIR=str(root), IPDB_VIM_CLEAN='1', IPDB_PYTHON=sys.executable)
            environment.update(environment_overrides)
            process = subprocess.Popen(['sh', str(Path(__file__).with_name('ipdbVim.sh')), str(script)],
                                       stdin=slave, stdout=slave, stderr=slave, cwd=str(root), env=environment)
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)

        output = bytearray()

        def Drain():
            while True:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)

        threading.Thread(target=Drain, daemon=True).start()
        command = None

        def Wait(predicate, label, timeout=10):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if predicate():
                    return
                if process.poll() is not None:
                    break
                time.sleep(0.01)
            self.fail(label + ': ' + repr(bytes(output[-1200:])))

        def Call(*args):
            return subprocess.check_output(command + list(args), stderr=subprocess.PIPE, text=True).strip()

        try:
            Wait(lambda: b'ipdb>' in output, 'initial prompt')
            config = json.loads(next(root.glob('ipdb-vim-*/config.json')).read_text())
            command = config['tmux']
            yield master, process, output, config, Wait, Call
        finally:
            if command:
                subprocess.run(command + ['kill-server'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            os.close(master)

    def test_first_chord_switches_focus_and_resets_table(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'sample.py'
            script.write_text('value = 1\nprint(value)\n')
            inherited_close = root / 'other-session-close'
            inherited_close.write_text('close')
            owner = subprocess.Popen(['vim', '-N', '-u', 'NONE', '-i', 'NONE', '-es',
                str(script), '-c', 'preserve', '-c', 'sleep 60'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                time.sleep(0.2)
                with self.LaunchTmux(root, script, IPDB_VIM_CLOSE=str(inherited_close),
                                     IPDB_VIM_CLOSED=str(root / 'other-closed')) as session:
                    master, process, output, config, Wait, Call = session
                    self.assertFalse((root / 'other-closed').exists())

                    def Active():
                        return Call('display-message', '-p', '-t', 'debug:0', '#{pane_index}')

                    def Status():
                        return Call('display-message', '-p', '-t', 'debug:0', '#{E:window-status-current-format}')

                    self.assertEqual(config['cwd'], directory)
                    os.write(master, b'!print("CWD_MATCH=" + str(__import__("os").getcwd() == ' + repr(directory).encode() + b'))\n')
                    Wait(lambda: b'CWD_MATCH=True\r\n' in output, 'launch working directory changed')
                    os.write(master, b'\x17k')
                    Wait(lambda: Active() == '0', 'source with existing swap')
                    os.write(master, b':e\r')
                    time.sleep(0.2)
                    self.assertNotIn(b'E325', output)
                    os.write(master, b'\x17j!print(731923)\n')
                    Wait(lambda: b'731923\r\n' in output, 'input after edit with existing swap', 3)
                    os.write(master, b'\x17k')
                    Wait(lambda: Active() == '0', 'source before write all')
                    setup = ":set hidden shortmess= cmdheight=1 | for i in range(1, 25) | silent execute 'edit " + str(root) + "/save' . i . '.py' | call setline(1, 'value = 1') | endfor\r"
                    os.write(master, setup.encode())
                    time.sleep(0.3)
                    os.write(master, b':wa\r')
                    time.sleep(0.3)
                    os.write(master, b'\x17j!print(731922)\n')
                    Wait(lambda: b'731922\r\n' in output, 'input after write all', 3)
                    for index in range(1, 26):
                        self.assertEqual((root / ('save{}.py'.format(index))).read_text().strip(), 'value = 1')
                    for search in (b'/value\r', b'?value\r', b'/not_present_48291\r', b'/value\x1b'):
                        os.write(master, b'\x17k')
                        Wait(lambda: Active() == '0', 'source before search')
                        marker = str(915000 + len(output)).encode('ascii')
                        os.write(master, search + b'\x17j!print(' + marker + b')\n')
                        Wait(lambda: marker + b'\r\n' in output, 'command lost after search ' + repr(search), 3)
                    for iteration in range(20):
                        os.write(master, b'\x17k')
                        Wait(lambda: Active() == '0', 'first up chord {}'.format(iteration), 1)
                        self.assertIn('VIM', Status())
                        os.write(master, b'\x17j')
                        Wait(lambda: Active() == '1', 'first down chord {}'.format(iteration), 1)
                        self.assertIn('IPDB', Status())
                        self.assertEqual(Call('list-clients', '-F', '#{client_key_table}'), 'root')
                    os.write(master, b'\x17\x0b')
                    Wait(lambda: Active() == '0', 'Ctrl held while pressing K', 1)
                    os.write(master, b'\x17\x0a')
                    Wait(lambda: Active() == '1', 'Ctrl held while pressing J', 1)
                    # Native Vim splits must take precedence over the tmux boundary.
                    os.write(master, b'\x17k:split\r')
                    time.sleep(0.2)
                    os.write(master, b'\x17\x0a')
                    time.sleep(0.1)
                    self.assertEqual(Active(), '0')
                    os.write(master, b'\x17\x0a')
                    Wait(lambda: Active() == '1', 'split edge should cross to debugger', 1)
                    os.write(master, b'\x17k:only\r')
                    time.sleep(0.1)
                    os.write(master, b'\x17j')
                    Wait(lambda: Active() == '1', 'return after closing split', 1)
                    # A backend source update must not consume a half-entered chord.
                    os.write(master, b'\x17k')
                    Wait(lambda: Active() == '0', 'source focus')
                    os.write(master, b'\x17')
                    state = Path(config['state'])
                    current = json.loads(state.read_text())
                    current.update(line=2, event=time.time())
                    temp = state.with_suffix('.test-tmp')
                    temp.write_text(json.dumps(current)); os.replace(str(temp), str(state))
                    time.sleep(0.25)
                    os.write(master, b'j')
                    Wait(lambda: Active() == '1', 'source update consumed prefix', 1)
                    for iteration in range(20):
                        os.write(master, b'\x17k\x17j')
                        time.sleep(0.1)
                        self.assertEqual(Active(), '1', 'Back-to-back chords lost focus at {}'.format(iteration))
                    # Ordinary command typing must survive concurrent source polling.
                    for iteration in range(12):
                        marker = str(815000 + iteration).encode('ascii')
                        typed = b'!print(' + marker + b')\n'
                        state = Path(config['state'])
                        current = json.loads(state.read_text())
                        current.update(event=time.time(), line=1 + iteration % 2)
                        temp = state.with_suffix('.test-tmp')
                        temp.write_text(json.dumps(current))
                        os.replace(str(temp), str(state))
                        for character in typed:
                            os.write(master, bytes([character]))
                            time.sleep(0.002)
                        Wait(lambda: marker + b'\r\n' in output, 'normal command lost', 3)
                    os.write(master, b'\x17k')
                    Wait(lambda: Active() == '0', 'source before immediate input')
                    os.write(master, b'\x17j!print(731924)\n')
                    Wait(lambda: b'731924\r\n' in output, 'immediate command lost', 3)
                    # Check visible echo separately from eventual command execution.
                    for first in (b'p', b'!', b'b'):
                        os.write(master, b'\x17k')
                        Wait(lambda: Active() == '0', 'source before first-character echo')
                        os.write(master, b'\x17j' + first)
                        Wait(lambda: Active() == '1' and ('ipdb> ' + first.decode()) in [row.rstrip() for row in Call('capture-pane', '-p', '-t', 'debug:0.1').splitlines()],
                             'first character missing from command display', 3)
                        os.write(master, b'\x15')
                        time.sleep(0.05)
                    os.write(master, b'q\n')
                    Wait(lambda: b'Press Enter' in output, 'exit prompt')
                    os.write(master, b'\n')
                    process.wait(timeout=10)
            finally:
                owner.terminate()
                owner.wait(timeout=10)

    def test_ctrl_b_pages_vim_and_preserves_debugger_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'sample.py'
            script.write_text(''.join('row_{0:04d} = {0}\n'.format(number) for number in range(1, 130)))
            with self.LaunchTmux(root, script) as session:
                master, process, output, _config, Wait, Call = session

                def Active():
                    return Call('display-message', '-p', '-t', 'debug:0', '#{pane_index}')

                def Source():
                    return Call('capture-pane', '-p', '-t', 'debug:0.0')

                os.write(master, b'!print(73192)\x02\x023\n')
                Wait(lambda: '731923' in Call('capture-pane', '-p', '-t', 'debug:0.1').splitlines(),
                     'double Ctrl+B still moves the command cursor back')
                os.write(master, b'\x02k')
                Wait(lambda: Active() == '0' and 'row_0001' in Source(),
                     'Ctrl+B k switches from the prompt to Vim')
                os.write(master, b'\x06\x06')
                Wait(lambda: 'row_0040' in Source() and 'row_0001' not in Source(), 'page down in Vim')
                os.write(master, b'\x02')
                Wait(lambda: 'row_0020' in Source() and 'row_0040' not in Source(),
                     'Ctrl+B reaches Vim as page up')
                os.write(master, b'\x02\x1b[5~')
                Wait(lambda: 'row_0001' in Source(), 'rapid Ctrl+B and PageUp show the first page')
                self.assertEqual(Call('display-message', '-p', '-t', 'debug:0.0', '#{pane_in_mode}'), '0')
                os.write(master, b'\x17jq\n')
                Wait(lambda: b'Press Enter' in output, 'exit prompt')
                os.write(master, b'\n')
                self.assertEqual(process.wait(timeout=10), 0)

    def test_next_preserves_visible_source_position(self):
        import re

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'sample.py'
            script.write_text(''.join('row_{:04d} = {!r}\n'.format(number, '' if number == 42 else 'x' * 200)
                                      for number in range(1, 100)))
            with self.LaunchTmux(root, script) as session:
                master, process, output, _config, Wait, Call = session

                def Active():
                    return Call('display-message', '-p', '-t', 'debug:0', '#{pane_index}')

                def Source():
                    return Call('capture-pane', '-p', '-t', 'debug:0.0')

                def View():
                    match = re.search(r'step-view:(\d+):(\d+)', Source())
                    return tuple(map(int, match.groups())) if match else None

                os.write(master, b'b 40\nc\n')
                Wait(lambda: re.search(r'>>\s*row_0040', Source()), 'stopped at the breakpoint')
                os.write(master, b'\x17k')
                Wait(lambda: Active() == '0', 'focus source before positioning the viewport')
                os.write(master, b':set nowrap laststatus=2 statusline=step-view:%{winsaveview().topline}:%{winsaveview().leftcol}\rzt')
                Wait(lambda: View() == (40, 0), 'position line 40 at the top')
                os.write(master, b'\x17jn\n')
                Wait(lambda: re.search(r'>>\s*row_0041', Source()), 'step to line 41')
                self.assertEqual(View(), (40, 0))

                os.write(master, b'\x17k')
                Wait(lambda: Active() == '0', 'focus source before horizontal scrolling')
                os.write(master, b'70zl')
                Wait(lambda: View() and View()[1] > 0, 'scroll the long line horizontally')
                scrolled = View()
                os.write(master, b'\x17jn\n')
                Wait(lambda: Source().splitlines()[2].startswith('>>'), 'step to the short line')
                self.assertEqual(View(), scrolled)
                self.assertEqual(sum(line.startswith('>>') for line in Source().splitlines()), 1)
                os.write(master, b'q\n')
                Wait(lambda: b'Press Enter' in output, 'exit prompt')
                os.write(master, b'\n')
                self.assertEqual(process.wait(timeout=10), 0)

    def test_switch_preserves_queued_command_editing_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'sample.py'
            script.write_text('value = 41\nprint(value)\n')
            with self.LaunchTmux(root, script) as session:
                master, process, output, config, Wait, Call = session
                state = Path(config['state'])
                os.write(master, b'n\n')
                Wait(lambda: json.loads(state.read_text())['line'] == 2, 'frame with value')
                for sequence, result in (
                        (r'p val\<Tab> + 74000\<CR>', '74041'),
                        (r'!print(741923a\<BS>)\<CR>', '741923'),
                        (r'p 7419200\<Left>8\<CR>', '74192080'),
                        (r'!x=741925;print(x)\<CR>', '741925')):
                    os.write(master, b'\x17k')
                    Wait(lambda: Call('display-message', '-p', '-t', 'debug:0', '#{pane_index}') == '0',
                         'source before editing keys')
                    # Queue keys in Vim so they must be forwarded by the pane switch.
                    os.write(master, (r':call feedkeys("\<C-w>j' + sequence + '", "mt")' + '\r').encode())
                    Wait(lambda: result in Call('capture-pane', '-p', '-t', 'debug:0.1').splitlines(),
                         'missing forwarded editing keys for ' + sequence, 3)
                os.write(master, b'q\n')
                Wait(lambda: b'Press Enter' in output, 'exit prompt')
                os.write(master, b'\n')
                self.assertEqual(process.wait(timeout=10), 0)

    def test_fast_resize_keys_and_mouse_focus(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'sample.py'
            script.write_text('value = 41\n' + ''.join('# source {}\n'.format(i) for i in range(120)))
            with self.LaunchTmux(root, script) as session:
                master, process, output, config, Wait, Call = session

                def Height(pane):
                    return int(Call('display-message', '-p', '-t', 'debug:0.' + pane, '#{pane_height}'))

                def KeyTable():
                    return Call('list-clients', '-F', '#{client_key_table}')

                def Active():
                    return Call('display-message', '-p', '-t', 'debug:0', '#{pane_index}')

                def FirstSourceRow():
                    return next((row.strip() for row in Call('capture-pane', '-p', '-t', 'debug:0.0').splitlines()
                                 if row.strip().startswith('# source ')), None)

                original = Height('1')
                self.assertEqual(Call('show-options', '-wv', '-t', 'debug:0', 'window-style'), 'default')
                self.assertEqual(Call('show-options', '-wv', '-t', 'debug:0', 'window-active-style'), 'default')
                self.assertEqual(Call('show-options', '-wv', '-t', 'debug:0', 'pane-active-border-style'), 'fg=brightcyan')
                os.write(master, b'\x175+')
                Wait(lambda: Height('1') == original + 5 and KeyTable() == 'root', 'fast height increase')
                os.write(master, b'\x173-')
                Wait(lambda: Height('1') == original + 2 and KeyTable() == 'root', 'fast height decrease')
                os.write(master, b'\x17=')
                Wait(lambda: abs(Height('0') - Height('1')) <= 1 and KeyTable() == 'root', 'equal heights')

                os.write(master, b'\x17')
                Wait(lambda: KeyTable() == 'ipdb-nav', 'pending navigation before mouse click')
                os.write(master, b'\x1b[<0;10;3M\x1b[<0;10;3m')
                Wait(lambda: Active() == '0' and KeyTable() == 'root', 'source click resets navigation table')
                Wait(FirstSourceRow, 'source before wheel scroll')
                previous = FirstSourceRow()
                os.write(master, b'\x1b[<65;10;4M' * 4)
                Wait(lambda: FirstSourceRow() not in (None, previous), 'wheel scroll')
                self.assertEqual(Call('display-message', '-p', '-t', 'debug:0.0', '#{pane_in_mode}'), '0')

                row = int(Call('display-message', '-p', '-t', 'debug:0.1', '#{pane_top}')) + 2
                click = '\x1b[<0;10;{0}M\x1b[<0;10;{0}m'.format(row).encode()
                for index, mode_keys in enumerate(('emacs', 'vi')):
                    time.sleep(0.55)  # Separate single clicks from tmux's double-click sequence.
                    Call('set-window-option', '-t', 'debug:0', 'mode-keys', mode_keys)
                    Call('copy-mode', '-t', 'debug:0.1')
                    self.assertEqual(Call('display-message', '-p', '-t', 'debug:0.1', '#{pane_in_mode}'), '1')
                    os.write(master, click)
                    Wait(lambda: Active() == '1' and KeyTable() == 'root'
                         and Call('display-message', '-p', '-t', 'debug:0.1', '#{pane_in_mode}') == '0',
                         'debugger click leaves copy mode')
                    result = str(841923 + index)
                    os.write(master, '!print({})\n'.format(result).encode())
                    Wait(lambda: result in Call('capture-pane', '-p', '-t', 'debug:0.1').splitlines(),
                         'command after mouse focus')
                    if index == 0:
                        os.write(master, b'\x17k')
                        Wait(lambda: Active() == '0', 'source focus before testing vi copy mode')

                os.write(master, b'q\n')
                Wait(lambda: b'Press Enter' in output, 'exit prompt')
                os.write(master, b'\n')
                self.assertEqual(process.wait(timeout=10), 0)

    def test_two_sessions_keep_source_and_input_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / 'first', root / 'second'
            for holder in (first, second):
                holder.mkdir()
                (holder / 'sample.py').write_text('value = 41\nprint(value)\n')
            with self.LaunchTmux(first, first / 'sample.py') as session1, \
                    self.LaunchTmux(second, second / 'sample.py') as session2:
                master1, process1, output1, config1, wait1, _ = session1
                master2, process2, output2, config2, wait2, call2 = session2
                source1, source2 = Path(config1['state']), Path(config2['state'])
                self.assertNotEqual(config1['tmux'], config2['tmux'])
                os.write(master1, b'n\n')
                wait1(lambda: json.loads(source1.read_text())['line'] == 2, 'step in first session')
                self.assertEqual(json.loads(source2.read_text())['line'], 1)
                os.write(master1, b'q\n')
                wait1(lambda: b'Press Enter' in output1, 'first session exit prompt')
                os.write(master1, b'\n')
                self.assertEqual(process1.wait(timeout=10), 0)
                os.write(master2, b'!print(841925)\n')
                wait2(lambda: '841925' in call2('capture-pane', '-p', '-t', 'debug:0.1').splitlines(),
                      'second session accepts a command after first session exits')
                os.write(master2, b'q\n')
                wait2(lambda: b'Press Enter' in output2, 'second session exit prompt')
                os.write(master2, b'\n')
                self.assertEqual(process2.wait(timeout=10), 0)

    def test_ctrl_c_pauses_loop_in_command_pane(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'sample.py'
            script.write_text(
                'def Loop():\n'
                '    gate = [True]\n'
                "    print('TMUX' + '_LOOP_READY', flush=True)\n"
                '    while gate[0]:\n'
                '        pass\n'
                'Loop()\n'
                "print('TMUX' + '_LOOP_DONE', flush=True)\n")
            with self.LaunchTmux(root, script) as session:
                master, process, output, config, Wait, Call = session
                state = Path(config['state'])

                def DebugPane():
                    return Call('capture-pane', '-p', '-t', 'debug:0.1')

                os.write(master, b'c\n')
                Wait(lambda: 'TMUX_LOOP_READY' in DebugPane().splitlines(), 'loop started in command pane')
                os.write(master, b'\x03')
                Wait(lambda: json.loads(state.read_text())['line'] in (4, 5) and 'ipdb>' in DebugPane(),
                     'Ctrl+C stopped at the live source line', 3)
                self.assertEqual(Path(json.loads(state.read_text())['file']), script)
                self.assertNotIn('Press Enter', DebugPane())
                os.write(master, b'bt\n')
                Wait(lambda: '* #0 Loop at ' in DebugPane(), 'loop is the selected frame', 3)
                os.write(master, b'!gate[0] = False\nc\n')
                Wait(lambda: 'TMUX_LOOP_DONE' in DebugPane().splitlines() and 'ipdb>' in DebugPane(),
                     'continued the same loop after inspecting the stop', 3)
                os.write(master, b'q\n')
                Wait(lambda: b'Press Enter' in output, 'exit prompt')
                os.write(master, b'\n')
                self.assertEqual(process.wait(timeout=10), 0)


if __name__ == '__main__':
    unittest.main()
