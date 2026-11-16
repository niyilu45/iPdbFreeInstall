"""Real debugger restart after same-size, same-timestamp source edits."""
import os
import py_compile
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import re
from reload_code import Relocate


class ReloadTests(unittest.TestCase):
    def test_restart_from_post_mortem_keeps_session(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'broken.py'
            script.write_text('raise ValueError("original failure")\n')
            commands = ["!print('SESSION_PID', __import__('os').getpid())", 'c',
                        "!open({!r}, 'w').write('print(\"RECOVERED\")\\n')".format(str(script)),
                        'r', "!print('SESSION_PID', __import__('os').getpid())", 'c', 'q', '']
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(script)],
                input='\n'.join(commands), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, timeout=30, cwd=directory,
                env=dict(os.environ, IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('RECOVERED', result.stdout)
            self.assertNotIn('pdb.Restart', result.stdout)
            pids = re.findall(r'SESSION_PID (\d+)', result.stdout)
            self.assertEqual(len(pids), 2, result.stdout)
            self.assertEqual(pids[0], pids[1], result.stdout)

    def test_relocation_requires_unambiguous_source(self):
        old = 'def first():\n    value = 1\n    return value\ndef second():\n    return value\n'.splitlines(True)
        new = '# inserted\n\n' + ''.join(old)
        self.assertEqual(Relocate(old, new.splitlines(True), 3), 5)
        deleted = 'def first():\n    value = 1\ndef second():\n    return value\n'.splitlines(True)
        self.assertIsNone(Relocate(old, deleted, 3))
        ambiguous = 'value = 1\nvalue = 1\n'.splitlines(True)
        self.assertIsNone(Relocate(['value = 1\n'], ambiguous, 1))
        self.assertIsNone(Relocate(ambiguous, ['value = 1\n'], 1))

    def test_saved_script_and_import_reload_preserving_breakpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / 'helper.py'
            helper.write_text('VALUE = 10\n')
            script = root / 'sample.py'
            script.write_text('import helper\nprint("OLD", helper.VALUE)\nsentinel = 0\n')
            py_compile.compile(str(helper), doraise=True)
            helper_stamp = helper.stat().st_mtime
            commands = [
                "!print('SESSION_PID', __import__('os').getpid())",
                'b 3', 'c',
                "!open({!r}, 'w').write('VALUE = 20\\n')".format(str(helper)),
                "!__import__('os').utime({!r}, ({!r}, {!r}))".format(str(helper), helper_stamp, helper_stamp),
                "!open({!r}, 'w').write('this is invalid python !!!\\n')".format(str(script)),
                'r',
                "!open({!r}, 'w').write('# inserted\\n\\nimport helper\\nprint(\"NEW\", helper.VALUE)\\nsentinel = 0\\n')".format(str(script)),
                'r', 'c', 'p helper.VALUE',
                "!print('SESSION_PID', __import__('os').getpid())",
                "!print('BREAKPOINT_LINE', __import__('bdb').Breakpoint.bpbynumber[1].line)",
                'q', '']
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(script)],
                input='\n'.join(commands), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, timeout=30, cwd=directory,
                env=dict(os.environ, IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('OLD 10', result.stdout)
            self.assertIn('NEW 20', result.stdout)
            self.assertIn('Reloading saved project source', result.stdout)
            self.assertIn('Cannot reload saved source', result.stdout)
            self.assertIn('BREAKPOINT_LINE 5', result.stdout)
            pids = re.findall(r'SESSION_PID (\d+)', result.stdout)
            self.assertEqual(len(pids), 2, result.stdout)
            self.assertEqual(pids[0], pids[1], result.stdout)
            self.assertNotIn('Traceback', result.stdout)


if __name__ == '__main__':
    unittest.main()
