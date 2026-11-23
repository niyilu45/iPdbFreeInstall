"""Command compatibility and real Linux checkpoint regression tests."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

import portable_ipdb
from gdb_commands import Translate
from IPython.core.debugger import Pdb


class CommandTests(unittest.TestCase):
    def test_partial_range_deletion(self):
        from unittest import mock
        from checkpoints import Manager
        output = io.StringIO()
        debugger = Pdb(stdin=io.StringIO(), stdout=output)
        debugger._portable_displays = {3: {'enabled': True}}
        debugger.onecmd(debugger.precmd('d disp 1-5'))
        self.assertEqual(debugger._portable_displays, {})
        self.assertIn('1, 2, 4, 5', output.getvalue())
        with mock.patch.object(type(Manager), 'entries', new_callable=mock.PropertyMock, return_value={3: {}}), mock.patch.object(Manager, 'Delete') as delete:
            debugger.onecmd(debugger.precmd('d check 1-5'))
            delete.assert_called_once_with(3)
        import bdb
        with mock.patch.object(bdb.Breakpoint, 'bpbynumber', [None, None, None, SimpleNamespace(number=3)]), mock.patch.object(debugger, 'clear_bpbynumber', return_value=None) as clear:
            debugger.onecmd(debugger.precmd('d 1-5'))
            clear.assert_called_once_with('3')

    def test_blank_breakpoint_moves_to_executable_line(self):
        import bdb
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'blank.py'
            path.write_text('value = 1\n\n# comment\n\nvalue = 2\n\n')
            output = io.StringIO()
            debugger = Pdb(stdin=io.StringIO(), stdout=output)
            debugger.curframe = sys._getframe()
            try:
                debugger.onecmd(debugger.precmd('b {}:2 if value == 1'.format(path)))
                entries = debugger.get_breaks(debugger.canonic(str(path)), 5)
                self.assertEqual(len(entries), 1, output.getvalue())
                self.assertEqual(entries[0].cond, 'value == 1')
                debugger.onecmd(debugger.precmd('b {}:6'.format(path)))
                self.assertIn('No executable line below', output.getvalue())
                self.assertIn('moved from line 2 to 5', output.getvalue())
            finally:
                debugger.clear_all_breaks()
                bdb.Breakpoint.bplist.clear()

    def test_print_numeric_formats(self):
        output = io.StringIO()
        debugger = Pdb(stdin=io.StringIO(), stdout=output)
        debugger.curframe = sys._getframe()
        value = 42
        for command, expected in [('p/t value', '00101010'), ('p/x value', '0x2a'),
                                  ('print/o value', '0o052'), ('p/d value', '42'),
                                  ('p/x -value', '0xd6'), ('p/c 65', "'A'"), ('p/x -128', '0x80'),
                                  ('p/x -129', '0xff7f'), ('p/x 128', '0x0080'),
                                  ('p/x -32768', '0x8000'), ('p/x -32769', '0xff7fff'),
                                  ('p/t -1', '11111111'), ('p/x 0', '0x00'),
                                  ('p/d -42', '-42'), ('p/o -42', '0o326')]:
            output.seek(0)
            output.truncate(0)
            debugger.onecmd(debugger.precmd(command))
            self.assertEqual(output.getvalue().strip(), expected)
        for command in ['p/x 1.5', 'p/x missing_name', 'p/x', 'p/z value']:
            output.seek(0)
            output.truncate(0)
            debugger.onecmd(debugger.precmd(command))
            self.assertIn('***', output.getvalue())

    def test_enable_disable_ranges_with_missing_ids(self):
        import bdb
        output = io.StringIO()
        debugger = Pdb(stdin=io.StringIO(), stdout=output)
        try:
            debugger.set_break(__file__, 1)
            entry = debugger.get_breaks(debugger.canonic(__file__), 1)[-1]
            missing = entry.number + 1
            debugger.onecmd(debugger.precmd('dis {}-{}'.format(entry.number, missing)))
            self.assertFalse(entry.enabled)
            self.assertIn('Unknown breakpoint IDs: {}'.format(missing), output.getvalue())
            debugger.onecmd(debugger.precmd('en {}-{}'.format(entry.number, missing)))
            self.assertTrue(entry.enabled)
        finally:
            debugger.clear_all_breaks()
            bdb.Breakpoint.bplist.clear()

    def test_translation(self):
        cases = {'bt': 'backtrace', 'bt 2': 'backtrace 2', 'finish': 'return',
                 'break sample.py:10 if x == 3': 'break sample.py:10, x == 3',
                 'b 10 if name == "a if b"': 'break 10, name == "a if b"',
                 'delete checkpoint 2': 'delete_checkpoint 2',
                 'disp values[0]': 'display values[0]',
                 'delete display 1 2': 'undisplay 1 2',
                 'disable display 2': 'disable_display 2',
                 'enable display': 'enable_display',
                 'd 1': 'clear 1', 'del 2': 'clear 2',
                 'dis disp 1': 'disable_display 1',
                 'en disp 1': 'enable_display 1',
                 'i disp': 'info display', 'i b': 'info breakpoints',
                 'i check': 'info checkpoints',
                 'del check 2': 'delete_checkpoint 2',
                 'd disp 1': 'undisplay 1', 'und 1': 'undisplay 1',
                 'cond 1 x > 0': 'condition 1 x > 0',
                 'check': 'checkpoint', 'res 1': 'restart 1',
                 'set v x = 3': 'set variable x = 3',
                 'dis b 2': 'disable 2',
                 'disp\tvalue': 'display value',
                 'delete 4': 'clear 4', 'r': 'run', '!print("if")': '!print("if")'}
        for command, expected in cases.items():
            self.assertEqual(Translate(command), expected)

    def test_ambiguous_prefix_does_not_repeat_command(self):
        for text in ('re', 'co', 'down'):
            with self.assertRaises(ValueError):
                Translate(text)
        output = io.StringIO()
        debugger = Pdb(stdin=io.StringIO(), stdout=output)
        debugger.lastcmd = 'continue'
        command = debugger.precmd('re')
        self.assertEqual(command, '_portable_noop')
        self.assertIsNone(debugger.onecmd(command))
        self.assertIn('restart, return', output.getvalue())
        self.assertEqual(Translate('!down = 3'), '!down = 3')

    def test_conemu_escape_sequences_do_not_become_commands(self):
        from unittest import mock
        debugger = Pdb(stdin=io.StringIO(), stdout=io.StringIO())
        debugger.lastcmd = 'continue'
        with mock.patch.dict(os.environ, {'ConEmuPID': '42'}), mock.patch('sys.platform', 'win32'), \
                mock.patch('sys.stdin.isatty', return_value=False):
            self.assertIsNone(debugger.onecmd(debugger.precmd('\x1b[I\x1b[O\x1b[A')))
            self.assertEqual(debugger.lastcmd, 'continue')
            self.assertEqual(debugger.precmd('\x1b[Ip value\x1b[O'), 'p value')
            self.assertEqual(debugger.precmd('\x1bq'), 'quit')
            self.assertEqual(debugger.precmd(r'p "\x1b[O"'), r'p "\x1b[O"')

    def test_abbreviated_display_management(self):
        debugger = Pdb(stdin=io.StringIO(), stdout=io.StringIO())
        debugger.curframe = sys._getframe()
        for command in ('disp 3', 'dis disp 1'):
            debugger.onecmd(debugger.precmd(command))
        self.assertFalse(debugger._portable_displays[1]['enabled'])
        debugger.onecmd(debugger.precmd('en disp 1'))
        self.assertTrue(debugger._portable_displays[1]['enabled'])
        debugger.onecmd(debugger.precmd('d disp 1'))
        self.assertEqual(debugger._portable_displays, {})

    def test_display_updates_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'display_sample.py'
            path.write_text('values = [1]\nvalues[0] = 2\nfinished = True\n')
            output = io.StringIO()
            commands = io.StringIO('disp values[0]\nnext\ndisp values[0]\nnext\nquit\n')
            debugger = Pdb(stdin=commands, stdout=output)
            debugger.use_rawinput = False
            debugger.run(compile(path.read_text(), str(path), 'exec'), {})
            text = output.getvalue()
            self.assertIn('NameError:', text)
            self.assertNotIn('1: values[0] = <NameError:', text)
            self.assertIn('1: values[0] = 1', text)
            self.assertIn('1: values[0] = 2', text)

    def test_failed_display_is_not_registered_or_numbered(self):
        class BrokenRepr:
            def __repr__(self):
                raise ValueError('cannot render')
        namespace = {'broken': BrokenRepr(), 'items': []}
        debugger = Pdb(stdin=io.StringIO(), stdout=io.StringIO())
        debugger.curframe = SimpleNamespace(f_globals={}, f_locals=namespace)
        debugger.do_info('display')
        for expression in ('missing_name', '1 / 0', 'items[1]', '1 +', 'broken'):
            debugger.do_display(expression)
            self.assertEqual(debugger._portable_displays, {})
            self.assertEqual(debugger._portable_display_next, 1)
        debugger.do_display('items.append(1)')
        self.assertEqual(namespace['items'], [1])
        self.assertEqual(list(debugger._portable_displays), [1])
        debugger.do_display('another_missing_name')
        self.assertEqual(list(debugger._portable_displays), [1])
        self.assertEqual(debugger._portable_display_next, 2)

    def test_display_management_and_instance_isolation(self):
        output = io.StringIO()
        debugger = Pdb(stdin=io.StringIO(), stdout=output)
        debugger.curframe = sys._getframe()
        debugger.do_display('10 + 2')
        debugger.do_display('20')
        debugger.do_disable_display('1 99')
        self.assertTrue(debugger._portable_displays[1]['enabled'])
        debugger.do_disable_display('1')
        self.assertFalse(debugger._portable_displays[1]['enabled'])
        output.seek(0)
        output.truncate()
        debugger.do_display('')
        self.assertNotIn('1:', output.getvalue())
        self.assertIn('2: 20 = 20', output.getvalue())
        debugger.do_enable_display('1')
        debugger.do_undisplay('2')
        debugger.do_display('30')
        self.assertEqual(list(debugger._portable_displays), [1, 3])
        second = Pdb(stdin=io.StringIO(), stdout=io.StringIO())
        second.do_info('display')
        self.assertEqual(second._portable_displays, {})
        debugger.do_undisplay('')
        self.assertEqual(debugger._portable_displays, {})

    def test_backtrace_and_frame_numbers(self):
        outer = SimpleNamespace(f_code=SimpleNamespace(co_name='outer', co_filename='sample.py'), f_locals={})
        inner = SimpleNamespace(f_code=SimpleNamespace(co_name='inner', co_filename='sample.py'), f_locals={'x': 3})
        messages = []
        selected = []
        debugger = SimpleNamespace(stack=[(outer, 10), (inner, 20)], curindex=1,
                                   message=messages.append, error=messages.append,
                                   print_stack_entry=selected.append)
        Pdb.do_backtrace(debugger, '')
        self.assertIn('#0 inner', messages[0])
        self.assertIn('#1 outer', messages[1])
        Pdb.do_frame(debugger, '1')
        self.assertIs(debugger.curframe, outer)
        self.assertEqual(selected[-1], (outer, 10))

    def test_conditional_breakpoint_execution(self):
        import bdb
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'condition_sample.py'
            path.write_text('for value in range(4):\n    marker = value\n')
            output = io.StringIO()
            debugger = Pdb(stdin=io.StringIO(), stdout=output)
            debugger.use_rawinput = False
            debugger.cmdqueue = ['break {}:2 if value == 2'.format(path), 'continue', 'print value', 'quit']
            try:
                debugger.run(compile(path.read_text(), str(path), 'exec'), {})
                text = output.getvalue()
                self.assertIn('Breakpoint', text)
                self.assertRegex(text, r'(?m)^2\s*$')
                self.assertRegex(text, r'Breakpoint \d+ hit; condition: value == 2')
            finally:
                debugger.clear_all_breaks()
                bdb.Breakpoint.bplist.clear()

    def test_condition_without_location(self):
        import bdb
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'condition_current.py'
            path.write_text('for value in range(4):\n    marker = value\n')
            output = io.StringIO()
            debugger = Pdb(stdin=io.StringIO(), stdout=output)
            debugger.use_rawinput = False
            debugger.cmdqueue = ['next', 'b if value == 2', 'continue',
                                 '!print("HIT=" + str(value))', 'quit']
            try:
                debugger.run(compile(path.read_text(), str(path), 'exec'), {})
                self.assertIn('HIT=2', output.getvalue())
                self.assertTrue(any(bp.cond == 'value == 2' and bp.line == 2
                                    for bp in bdb.Breakpoint.bpbynumber if bp))
            finally:
                debugger.clear_all_breaks()
                bdb.Breakpoint.bplist.clear()

    def test_set_local(self):
        value = 1
        frame = sys._getframe()
        errors = []
        debugger = SimpleNamespace(curframe=frame, error=errors.append)
        Pdb.do_set(debugger, 'var value = 7')
        self.assertEqual(value, 7)
        self.assertEqual(errors, [])

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux fork required')
    def test_checkpoint_memory_and_execution(self):
        root = Path(__file__).resolve().parent
        code = r'''
import sys
import portable_ipdb
from checkpoints import Manager, Supervise
from types import SimpleNamespace

def Run():
    value = [1]
    frame = sys._getframe()
    debug = SimpleNamespace(curframe=frame, stack=[(frame, frame.f_lineno)], curindex=0, restored=False)
    def Message(text):
        if text.startswith('Restored'):
            debug.restored = True
    debug.message = Message
    debug.print_stack_entry = lambda entry: None
    Manager.Create(debug)
    if debug.restored:
        assert value == [1], value
        assert list(Manager.entries) == [1]
        assert Manager.next_id == 2
        print('CHECKPOINT_RESTORED')
        return
    value.append(2)
    Manager.Restart(1)
    raise AssertionError('Restart returned to abandoned process')

sys.exit(Supervise(Run))
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=str(root),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b'CHECKPOINT_RESTORED', result.stdout)


    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux fork required')
    def test_checkpoint_switches_preserve_all_snapshots(self):
        root = Path(__file__).resolve().parent
        code = r'''
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import portable_ipdb
from checkpoints import Manager, Supervise

with tempfile.TemporaryDirectory() as directory:
    stage = Path(directory) / 'stage'
    stage.write_text('0')
    def Run():
        value = [1]
        frame = sys._getframe()
        debug = SimpleNamespace(curframe=frame, stack=[(frame, frame.f_lineno)], curindex=0, restored=False)
        def Message(text):
            if text.startswith('Restored'):
                debug.restored = True
        debug.message = Message
        debug.print_stack_entry = lambda entry: None
        def Verify(number):
            index = int(stage.read_text())
            expected = [(1, [1]), (2, [1, 2]), (1, [1]), (2, [1, 2])]
            assert (number, value) == expected[index], (number, value, index)
            assert sorted(Manager.entries) == [1, 2]
            assert Manager.next_id == 3
            if index < 3:
                value.append(99)
                stage.write_text(str(index + 1))
                Manager.Restart(expected[index + 1][0])
            Manager.Create(debug)
            assert sorted(Manager.entries) == [1, 2, 3]
            assert Manager.next_id == 4
            Manager.Delete(1)
            Manager.Delete(2)
            assert list(Manager.entries) == [3]
            try:
                Manager.Restart(1)
                raise AssertionError('Deleted checkpoint restored')
            except ValueError:
                pass
            Manager.Cleanup()
            assert not Manager.entries
            print('REPEATED_SWITCHES_PASSED')
        Manager.Create(debug)
        if debug.restored:
            Verify(1)
            return
        value.append(2)
        Manager.Create(debug)
        if debug.restored:
            Verify(2)
            return
        value.append(3)
        Manager.Restart(1)
    sys.exit(Supervise(Run))
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=str(root),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b'REPEATED_SWITCHES_PASSED', result.stdout)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux terminal required')
    def test_checkpoint_terminal_round_trip(self):
        import pexpect
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'sample.py'
            script.write_text('value = 10\nvalue = 20\nvalue = 30\nprint(value)\n')
            child = pexpect.spawn(sys.executable,
                ['-B', str(root / 'portable_ipdb.py'), '--plain', str(script)],
                env=dict(os.environ, TERM='dumb', IPYTHONDIR=directory),
                encoding='utf-8', timeout=10)
            def Command(command):
                child.sendline(command)
                child.expect_exact('ipdb> ')
                return child.before
            try:
                child.expect_exact('ipdb> ')
                Command('n')
                self.assertIn('Checkpoint 1 created', Command('check'))
                Command('n')
                self.assertIn('Checkpoint 2 created', Command('check'))
                for number, value, line in [(1, 10, 2), (2, 20, 3), (1, 10, 2), (2, 20, 3)]:
                    self.assertIn('Restored checkpoint {}'.format(number), Command('res {}'.format(number)))
                    self.assertIn('VALUE={}'.format(value), Command('!print("VALUE=" + str(value))'))
                    self.assertIn('sample.py:{}'.format(line), Command('i check'))
                Command('del check 1')
                self.assertIn('Unknown checkpoint 1', Command('res 1'))
                self.assertIn('Restored checkpoint 2', Command('res 2'))
                Command('r')
                self.assertIn('No checkpoints', Command('i check'))
                self.assertIn('Unknown checkpoint 2', Command('res 2'))
                self.assertIn('Checkpoint 1 created', Command('check'))
                self.assertIn('Restored checkpoint 1', Command('res 1'))
                Command('r')
                self.assertIn('Checkpoint 1 created', Command('check'))
                child.sendline('q')
                child.expect(pexpect.EOF)
                child.close()
                self.assertEqual(child.exitstatus, 0)
            finally:
                if child.isalive():
                    child.sendline('q')
                child.close(force=True)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux fork required')
    def test_checkpoint_restores_timing_without_overwriting_other_report(self):
        import pexpect
        launcher = Path(__file__).resolve().with_name('portable_ipdb.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'sample.py'
            script.write_text('def TimedChild():\n'
                              '    return 21\n'
                              'def TimedLeaf():\n'
                              '    return TimedChild()\n'
                              'value = TimedLeaf()\n'
                              'value += TimedLeaf()\n'
                              'print(value)\n')
            child = pexpect.spawn(sys.executable,
                ['-B', str(launcher), '--plain', str(script)], cwd=directory,
                env=dict(os.environ, TERM='dumb', IPYTHONDIR=directory),
                encoding='utf-8', timeout=10)

            def Command(command):
                child.sendline(command)
                child.expect_exact('ipdb> ')
                return child.before

            def ReportPath(output):
                prefix = 'Callgrind report: '
                paths = [Path(line[len(prefix):]) for line in output.splitlines()
                         if line.startswith(prefix)]
                self.assertEqual(len(paths), 1, output)
                self.assertEqual(paths[0].parent, root / 'ipdCache')
                return paths[0]

            def ReadReport(path, count):
                text = path.read_text(encoding='utf-8')
                self.assertTrue(text.startswith('# callgrind format\n'))
                marker = '\nfn=TimedLeaf:'
                self.assertIn(marker, text)
                leaf = text.split(marker, 1)[1].split('\n\n', 1)[0]
                self.assertRegex(leaf, r'(?m)^cfn=TimedChild:\d+\ncalls={} \d+$'.format(count))
                return text

            try:
                child.expect_exact('ipdb> ')
                Command('rt')
                Command('b 6')
                Command('b 7')
                original = ReportPath(Command('c'))
                ReadReport(original, 1)
                self.assertIn('Checkpoint 1 created', Command('check'))
                Command('c')
                advanced = ReadReport(original, 2)
                self.assertEqual(set(original.parent.glob('callgrind.out.*')), {original})

                output = Command('restart 1')
                self.assertIn('Restored checkpoint 1', output)
                restored = ReportPath(output)
                self.assertNotEqual(restored, original)
                ReadReport(restored, 1)
                self.assertEqual(original.read_text(encoding='utf-8'), advanced)
                self.assertIn('VALUE=21', Command('!print("VALUE=" + str(value))'))
                self.assertRegex(Command('i time TimedLeaf'), r'(?m)^ +1 +[^\r\n]+\(TimedLeaf\)\r?$')

                Command('c')
                ReadReport(restored, 2)
                self.assertEqual(original.read_text(encoding='utf-8'), advanced)
                self.assertEqual(set(original.parent.glob('callgrind.out.*')), {original, restored})
                child.sendline('q')
                child.expect(pexpect.EOF)
                child.close()
                self.assertEqual(child.exitstatus, 0)
            finally:
                if child.isalive():
                    child.sendline('q')
                child.close(force=True)


if __name__ == '__main__':
    unittest.main()

