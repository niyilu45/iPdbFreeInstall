"""Real debugger restart after same-size, same-timestamp source edits."""
import os
import py_compile
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ReloadTests(unittest.TestCase):
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
                'b 3', 'c',
                "!open({!r}, 'w').write('VALUE = 20\\n')".format(str(helper)),
                "!__import__('os').utime({!r}, ({!r}, {!r}))".format(str(helper), helper_stamp, helper_stamp),
                "!open({!r}, 'w').write('import helper\\nprint(\"NEW\", helper.VALUE)\\nsentinel = 0\\n')".format(str(script)),
                'r', 'c', 'p helper.VALUE', 'q', '']
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(script)],
                input='\n'.join(commands), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, timeout=30, cwd=directory,
                env=dict(os.environ, IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('OLD 10', result.stdout)
            self.assertIn('NEW 20', result.stdout)
            self.assertIn('Reloading saved project source', result.stdout)
            self.assertNotIn('Traceback', result.stdout)


if __name__ == '__main__':
    unittest.main()
