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
            debugger = Pdb(stdin=io.StringIO(), stdout=output)
            debugger.use_rawinput = False
            debugger.cmdqueue = ['disp values[0]', 'next', 'disp values[0]', 'next', 'quit']
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
                self.assertIn('Checkpoint 3 created', Command('check'))
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
