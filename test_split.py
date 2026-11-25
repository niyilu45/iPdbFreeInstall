"""Split-view unit and Linux PTY integration tests."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import re

from split_ipdb import KeyFilter, SourceRows, SourceBox, FitCells, ViewStart, ScrollSource
from terminal_keys import SourceKeys, PaneKeys
from unittest import mock
from types import SimpleNamespace
from launch_mode import SelectMode


class SplitTests(unittest.TestCase):
    def test_navigation_sequences_and_backspace(self):
        keys = SourceKeys()
        self.assertEqual(keys.Feed(b'\x1b[', True), (b'', []))
        self.assertEqual(keys.Feed(b'A', True), (b'', ['up']))
        self.assertEqual(keys.Feed(b'\x1bOB\x1b[C\x1b[D', True), (b'', ['down', 'right', 'left']))
        self.assertEqual(keys.Feed(b'\x1b[A', False), (b'\x1b[A', []))
        self.assertEqual(keys.Feed(b'p valuX\x08e\x7f', False), (b'p valuX\x7fe\x7f', []))
        self.assertEqual(keys.Feed(b'\t', True), (b'\t', []))
        self.assertEqual(keys.Feed(b'\x1b[3~', True), (b'\x1b[3~', []))
        keys.Feed(b'\x1b', True)
        keys.started -= 1
        self.assertEqual(keys.Flush(), b'\x1b')

    def test_mouse_click_decoder(self):
        keys = PaneKeys()
        self.assertEqual(keys.Feed(b'\x1b[<0;12;', True, 15), [])
        self.assertEqual(keys.Feed(b'20M', True, 15), [('click', (12, 20))])
        self.assertEqual(keys.Feed(b'\x1b[<0;12;20m', True, 15), [])

    def test_vim_navigation(self):
        keys = SourceKeys()
        self.assertEqual(keys.Feed(b'hjklggG\x02\x06\x15\x04zz0$', True),
                         (b'', ['left', 'down', 'up', 'right', 'first', 'last',
                                'page_up', 'page_down', 'half_up', 'half_down', 'home', 'line_start', 'line_end']))
        self.assertEqual(keys.Feed(b'hjklggG', False), (b'hjklggG', []))
        snapshot = {'lines': ['x' * 200] * 100}
        ScrollSource(snapshot, 'last', 10, 80)
        self.assertEqual(snapshot['_top'], 90)
        ScrollSource(snapshot, 'half_up', 10, 80)
        self.assertEqual(snapshot['_top'], 85)
        ScrollSource(snapshot, 'first', 10, 80)
        self.assertEqual(snapshot['_top'], 0)
        ScrollSource(snapshot, 'line_end', 10, 80)
        self.assertGreater(snapshot['_left'], 0)
        ScrollSource(snapshot, 'line_start', 10, 80)
        self.assertEqual(snapshot['_left'], 0)

    def test_pane_chords_and_mouse_fragments(self):
        keys = PaneKeys()
        events = []
        for value in b'\x17k\x1b[<64;10;3M\x17jnext':
            events.extend(keys.Feed(bytes([value]), True, 15))
        self.assertEqual(events[:3], [('focus', 'k'), ('scroll', ('up', 10, 3)), ('focus', 'j')])
        self.assertEqual(b''.join(value for kind, value in events[3:]), b'next')
        self.assertEqual(keys.Feed(b'\x1b[<65;10;20M', True, 15), [])
        self.assertEqual(keys.Feed(b'\x1b[<65;10;4M', True, 15), [('scroll', ('down', 10, 4))])
        self.assertEqual(keys.Feed(b'\x1b[A', True, 15), [('input', b'\x1b[A')])
        self.assertEqual(b''.join(value for kind, value in keys.Feed(b'\x17h', False, 0)), b'\x17h')

    def test_source_scroll_and_recenter(self):
        snapshot = {'line': 10, 'offset': 1, 'lines': ['x' * 200] * 40}
        initial = ViewStart(snapshot, 8)
        ScrollSource(snapshot, 'down', 8, 80)
        self.assertEqual(ViewStart(snapshot, 8), initial + 1)
        ScrollSource(snapshot, 'right', 8, 80)
        self.assertEqual(snapshot['_left'], 8)
        for _ in range(100):
            ScrollSource(snapshot, 'down', 8, 80)
        self.assertEqual(ViewStart(snapshot, 8), 32)
        ScrollSource(snapshot, 'home', 8, 80)
        self.assertEqual(ViewStart(snapshot, 8), initial)
        self.assertEqual(snapshot['_left'], 0)
        self.assertEqual(snapshot['line'], 10)

    def test_horizontal_scroll_preserves_gutter_and_colors(self):
        snapshot = {'file': 'demo.py', 'line': 1, 'lines': ['0123456789abcdef'], '_left': 8}
        plain = SourceBox(snapshot, 5, 50)
        colored = SourceBox(snapshot, 5, 50, color=True)
        self.assertIn('>     1 89abcdef', plain[1])
        self.assertEqual([re.sub(r'\x1b\[[0-9;]*m', '', row) for row in colored], plain)

    def test_focus_highlights_only_borders(self):
        import portable_ipdb
        snapshot = {'file': 'demo.py', 'line': 1, 'lines': ['value = 41']}
        inactive = SourceBox(snapshot, 5, 50, color=True)
        source = SourceBox(dict(snapshot, _source_focus=True), 5, 50, color=True)
        command = SourceBox(dict(snapshot, _command_border=True), 5, 50, color=True)
        self.assertIn('38;5;51m', source[0])
        self.assertIn('38;5;51m', command[-1])
        self.assertEqual(command[:-1], inactive[:-1])
        self.assertEqual(source[1:-1], [row.replace('38;5;244m|', '38;5;51m|')
                                      for row in inactive[1:-1]])

    def test_launcher_modes(self):
        self.assertEqual(SelectMode(['sample.py'], 'linux', True, 'xterm'), ('keys', ['sample.py']))
        self.assertEqual(SelectMode(['--tui', 'sample.py'], 'linux', True, 'xterm'), ('tui', ['sample.py']))
        self.assertEqual(SelectMode(['--plain', 'sample.py'], 'linux', True, 'xterm'), ('plain', ['sample.py']))
        self.assertEqual(SelectMode(['--pudb', 'sample.py'], 'linux', True, 'xterm'), ('pudb', ['sample.py']))
        for platform, interactive, terminal in [('win32', True, 'xterm'), ('linux', False, 'xterm'), ('linux', True, 'dumb')]:
            self.assertEqual(SelectMode(['sample.py'], platform, interactive, terminal), ('plain', ['sample.py']))
        self.assertEqual(SelectMode(['--help'], 'linux', True, 'xterm'), ('plain', ['--help']))

    def test_pudb_handoff_state(self):
        import portable_ipdb
        from IPython.core.debugger import Pdb
        from split_ipdb import InstallHook
        frame = SimpleNamespace(f_back=None, f_trace=None)
        trace = mock.Mock()
        owner = SimpleNamespace(botframe=frame, curframe=frame, breaks={'sample.py': [2]},
                                trace_dispatch=trace, _set_stopinfo=mock.Mock(), set_quit=mock.Mock())
        debugger = SimpleNamespace(reset=mock.Mock(), interaction=mock.Mock(),
                                   stopframe=frame, returnframe=None, stoplineno=9, quitting=False)
        fake = SimpleNamespace(_get_debugger=lambda: debugger)
        names = ('print_stack_entry', 'interaction', '_cmdloop', 'do_pudb', 'do_where', 'do_w')
        original = {name: Pdb.__dict__.get(name) for name in names}
        read_fd, write_fd = os.pipe()
        try:
            InstallHook(write_fd)
            with mock.patch.dict(sys.modules, {'pudb': fake}), mock.patch('sys.settrace') as install, mock.patch.dict(os.environ):
                self.assertTrue(Pdb.do_pudb(owner, ''))
                self.assertTrue(owner._portable_pudb)
                debugger.interaction.assert_called_once_with(frame, None)
                owner._set_stopinfo.assert_called_once_with(frame, None, 9)
                install.assert_called_once_with(trace)
                self.assertIs(frame.f_trace, trace)
                self.assertIs(debugger.breaks, owner.breaks)
        finally:
            os.close(read_fd)
            os.close(write_fd)
            for name, value in original.items():
                if value is None:
                    delattr(Pdb, name)
                else:
                    setattr(Pdb, name, value)

    def test_keys(self):
        keys = KeyFilter()
        self.assertEqual(keys.Feed(b'n\n\x18'), (b'n\n', 0, 0))
        self.assertEqual(keys.Feed(b'a'), (b'', 1, 0))
        self.assertEqual(keys.Feed(b'\x18\x01'), (b'', 1, 0))
        self.assertEqual(keys.Feed(b'\x18z\x03'), (b'\x18z\x03', 0, 0))
        self.assertEqual(keys.Feed(b'\x18s'), (b'', 0, 1))
        self.assertEqual(keys.Feed(b'\x18\x13'), (b'', 0, 1))
        self.assertEqual(keys.Feed(b'\x18S'), (b'', 0, 1))
        self.assertEqual(keys.Feed(b'\x18A'), (b'', 1, 0))
        self.assertEqual(keys.Feed(b'\x18o'), (b'', 0, 0))
        self.assertEqual(keys.focus_toggles, 1)
        keys.Feed(b'n\n')
        self.assertEqual(keys.focus_toggles, 0)

    def test_source(self):
        rows = SourceRows({'line': 3, 'offset': 1, 'lines': ['a', 'b', 'c', 'd']}, 3, 40)
        self.assertTrue(any(row.startswith('>     3 c') for row in rows))
        self.assertEqual(SourceRows({}, 5, 80), [])
        self.assertNotIn('\x1b', ''.join(SourceRows({'line': 1, 'lines': ['\x1b[2J']}, 5, 80)))

    def test_source_box(self):
        rows = SourceBox({'file': 'demo.py', 'line': 2, 'lines': ['value = 1', 'value += 1']}, 7, 80)
        self.assertEqual(len(rows), 7)
        self.assertTrue(rows[0].startswith('+ Source: demo.py:2'))
        self.assertTrue(rows[-1].startswith('+ Focus: COMMAND'))
        self.assertIn('Focus: SOURCE', SourceBox({'_source_focus': True}, 7, 80)[-1])
        self.assertTrue(all(len(row) == 79 for row in rows))
        self.assertTrue(all(row.startswith('|') and row.endswith('|') for row in rows[1:-1]))
        self.assertTrue(any('>     2 value += 1' in row for row in rows))
        self.assertEqual(FitCells('\u4e2d\u6587', 3), '\u4e2d ')
        self.assertEqual(SourceBox({}, 2, 80), [])

    def test_vim_syntax_and_current_line(self):
        from source_theme import TokenRows, CURRENT_BACKGROUND
        lines = ['def calculate(value):', '    return "result", 42  # comment']
        snapshot = {'file': 'demo.py', 'line': 2, 'lines': lines, 'syntax': TokenRows(lines)}
        plain = SourceBox(snapshot, 6, 90)
        colored = SourceBox(snapshot, 6, 90, color=True)
        strip = lambda text: re.sub(r'\x1b\[[0-9;]*m', '', text)
        self.assertEqual([strip(row) for row in colored], plain)
        active = [row for row in colored if '\x1b[48;5;{}m'.format(CURRENT_BACKGROUND) in row]
        self.assertEqual(len(active), 1)
        self.assertIn('>     2', strip(active[0]))
        self.assertTrue(all(row.endswith('\x1b[0m') for row in colored))
        colors = {span[1] for row in snapshot['syntax'] for span in row}
        self.assertGreaterEqual(len(colors), 4)

    def test_multiline_string_lexing(self):
        from source_theme import TokenRows
        rows = TokenRows(['text = """hello', 'return 42', 'end"""'])
        self.assertEqual(len({span[1] for span in rows[1]}), 1)
        self.assertNotEqual(rows[1][0][1], TokenRows(['return 42'])[0][0][1])

    def test_split_hides_location_but_preserves_displays(self):
        import portable_ipdb
        from IPython.core.debugger import Pdb
        from split_ipdb import InstallHook
        names = ('print_stack_entry', 'interaction', '_cmdloop', 'do_pudb', 'do_where', 'do_w')
        original = {name: Pdb.__dict__.get(name) for name in names}
        read_fd, write_fd = os.pipe()
        try:
            original_output = mock.Mock()
            Pdb.print_stack_entry = original_output
            state = bytearray([1])
            InstallHook(write_fd, split_state=state)
            frame = SimpleNamespace(f_code=SimpleNamespace(co_filename='<test>'), f_globals={})
            debugger = SimpleNamespace(_portable_show_displays=mock.Mock())
            Pdb.print_stack_entry(debugger, (frame, 1))
            original_output.assert_not_called()
            debugger._portable_show_displays.assert_called_once_with(frame)
            state[0] = 0
            Pdb.print_stack_entry(debugger, (frame, 1))
            original_output.assert_called_once()
            state[0] = 1
            debugger._portable_force_stack_output = True
            Pdb.print_stack_entry(debugger, (frame, 1))
            self.assertEqual(original_output.call_count, 2)
        finally:
            os.close(read_fd)
            os.close(write_fd)
            for name, value in original.items():
                if value is None:
                    delattr(Pdb, name)
                else:
                    setattr(Pdb, name, value)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux PTY required')
    def test_linux_session(self):
        import fcntl
        import pty
        import select
        import struct
        import termios
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'sample.py'
            script.write_text('value = 41\nprint("RESULT", value + 1)\n')
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
            env = dict(os.environ, TERM='xterm', IPYTHONDIR=directory)
            process = subprocess.Popen([sys.executable, '-S', str(root / 'portable_ipdb.py'), str(script)], stdin=slave, stdout=slave, stderr=slave, env=env)
            output = bytearray()

            def ReadUntil(needle):
                deadline = time.time() + 15
                while time.time() < deadline:
                    if needle in output:
                        return
                    if select.select([master], [], [], 0.1)[0]:
                        try:
                            chunk = os.read(master, 65536)
                        except OSError:
                            break
                        output.extend(chunk)
                    if process.poll() is not None:
                        break
                self.fail('Missing {!r}: {!r}'.format(needle, bytes(output[-4000:])))

            try:
                ReadUntil(b'ipdb>')
                ReadUntil(b'Shortcuts enabled:')
                output.clear()
                os.write(master, b'\x18a')
                ReadUntil(b'Ctrl-X A: split')
                self.assertNotIn(b'\x1b[2J', output)
                output.clear()
                os.write(master, b'n\n')
                ReadUntil(b'ipdb>')
                output.clear()
                os.write(master, b'\x17k:vsplit\n')
                ReadUntil(b'[VIEW 1]')
                output.clear()
                os.write(master, b'\x17h')
                ReadUntil(b'Focus: SOURCE 2:1')
                output.clear()
                os.write(master, b'k')
                ReadUntil(b'Focus: SOURCE 1:1')
                self.assertEqual(set(re.findall(rb'\x1b\[\d+;(\d+)H', output)), {b'1'})
                self.assertNotIn(b'\x1b[2K', output)
                output.clear()
                os.write(master, b'\x0c')
                ReadUntil(b'\x1b[1;51H')
                output.clear()
                os.write(master, b'\x17l:q\n')
                ReadUntil(b'Focus: SOURCE 1:1')
                output.clear()
                os.write(master, b'\x17k:vsplit\n:split\n:q\n\x17l:q\n/value\nmaggnN`a\x1b[<65;10;3M\x17jp valuX\x7fe\n')
                ReadUntil(b'41')
                output.clear()
                os.write(master, b'p valuX\x08e\n')
                ReadUntil(b'41')
                output.clear()
                os.write(master, b'p val\t\n')
                ReadUntil(b'41')
                ReadUntil(b'ipdb>')
                output.clear()
                os.write(master, b'p val')
                ReadUntil(b'p val')
                output.clear()
                os.write(master, b'\x0c')
                ReadUntil(b'p val')
                self.assertNotIn(b'\x1b[2J', output)
                self.assertNotIn(b'\x1b[1;1H', output)
                self.assertNotIn(b'\x1b[2K', output)
                self.assertIn(b'41', output)
                output.clear()
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 32, 104, 0, 0))
                ReadUntil(b'\x1b[17;32r')
                ReadUntil(b'p val')
                self.assertNotIn(b'\x1b[2J', output)
                output.clear()
                os.write(master, b'ue\n')
                ReadUntil(b'41')
                ReadUntil(b'ipdb>')
                output.clear()
                os.write(master, b'\x18a')
                ReadUntil(b'41')
                os.write(master, b'\x18a')
                output.clear()
                os.write(master, b'c\n')
                ReadUntil(b'RESULT 42')
                os.write(master, b'q\n')
                process.wait(timeout=10)
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                os.close(slave)
                os.close(master)


if __name__ == '__main__':
    unittest.main()
