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
                 'b 10 if name == "a if b"': 'b 10, name == "a if b"',
                 'delete checkpoint 2': 'delete_checkpoint 2',
                 'delete 4': 'clear 4', 'r': 'run', '!print("if")': '!print("if")'}
        for command, expected in cases.items():
            self.assertEqual(Translate(command), expected)

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
        assert not Manager.entries
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


if __name__ == '__main__':
    unittest.main()
