"""Real GUI source following with isolated Vim configuration."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from launch_mode import SelectMode


class GvimTests(unittest.TestCase):
    def test_mode(self):
        self.assertEqual(SelectMode(['--source-gvim', 'a b.py']), ('source-gvim', ['a b.py']))

    @unittest.skipUnless(shutil.which('gvim') and (sys.platform == 'win32' or os.environ.get('DISPLAY')), 'gVim desktop required')
    def test_real_gui_source_plugins_syntax_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'sample.py'
            script.write_text('value = 41\nprint("GUI_RESULT", value + 1)\n')
            (root / 'plugin').mkdir()
            (root / 'plugin' / 'probe.vim').write_text('let g:probe_plugin = 42\n')
            vimrc = root / 'test.vim'
            vimrc.write_text('''set nocompatible
set loadplugins
execute 'set runtimepath^=' . fnameescape($IPDB_TEST_ROOT)
function! Probe(timer) abort
  if filereadable($IPDB_TEST_ROOT . '/close-once')
    call delete($IPDB_TEST_ROOT . '/close-once')
    qa!
  endif
  call writefile([json_encode({'pid': getpid(), 'gui': has('gui_running'), 'file': expand('%:p'), 'line': line('.'), 'syntax': &syntax, 'plugin': get(g:, 'probe_plugin', 0), 'tmux': $IPDB_TMUX_SOCKET})], $IPDB_TEST_ROOT . '/probe.json')
endfunction
call timer_start(50, function('Probe'), {'repeat': -1})
''')
            env = dict(os.environ, HOME=directory, USERPROFILE=directory, IPYTHONDIR=directory,
                       VIMINIT="execute 'source ' . fnameescape($IPDB_TEST_ROOT . '/test.vim')",
                       GVIMINIT='set guioptions-=T', IPDB_TEST_ROOT=directory, IPDB_VIM_CLEAN='0',
                       IPDB_TMUX_SOCKET='must-be-cleared', IPDB_TMUX_BIN='must-be-cleared', IPY_TEST_SIMPLE_PROMPT='1')
            launcher = Path(__file__).with_name('portable_ipdb.py')
            process = subprocess.Popen([sys.executable, '-B', str(launcher), '--source-gvim', str(script)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       universal_newlines=True, env=env)
            def Wait(line, previous_pid=None):
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    try:
                        report = json.loads((root / 'probe.json').read_text())
                        if Path(report['file']) == script and report['line'] == line and report['pid'] != previous_pid:
                            return report
                    except (OSError, ValueError):
                        pass
                    if process.poll() is not None:
                        self.fail(process.stdout.read())
                    time.sleep(0.05)
                self.fail('gVim did not follow line {}'.format(line))
            try:
                report = Wait(1)
                self.assertEqual(report['gui'], 1)
                self.assertEqual(report['plugin'], 42)
                self.assertEqual(report['syntax'], 'python')
                self.assertEqual(report['tmux'], '')
                process.stdin.write('n\n'); process.stdin.flush()
                report = Wait(2)
                (root / 'close-once').write_text('close')
                reopened = Wait(2, report['pid'])
                self.assertNotEqual(reopened['pid'], report['pid'])
                self.assertIsNone(process.poll())
                script.write_text('value = 50\nprint("GUI_NEW", value + 1)\n')
                process.stdin.write('r\n'); process.stdin.flush()
                Wait(1)
                output, _ = process.communicate('c\nq\n', timeout=15)
                self.assertEqual(process.returncode, 0, output)
                self.assertIn('GUI_NEW 51', output)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                process.stdin.close()
                process.stdout.close()


if __name__ == '__main__':
    unittest.main()
