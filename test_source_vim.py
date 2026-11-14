"""System Vim bridge tests, including real Vim when available."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from launch_mode import SelectMode
from source_vim import Publish, Shell


class VimTests(unittest.TestCase):
    def test_mode_and_safe_arguments(self):
        self.assertEqual(SelectMode(['--source-vim', 'my file.py']), ('source-vim', ['my file.py']))
        values = ['python', '/tmp/a b.py', "quote'and$(literal)"]
        self.assertEqual(shlex.split(Shell(values)), ['exec'] + values)

    def test_atomic_source_message(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state'
            Publish(path, 'my file.py', 7)
            value = json.loads(path.read_text())
            self.assertEqual(value['line'], 7)
            self.assertTrue(os.path.isabs(value['file']))
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    @unittest.skipUnless(shutil.which('vim'), 'System Vim required')
    def test_real_vim_loads_plugins_and_only_moves_main(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'plugin').mkdir()
            (root / 'plugin/probe.vim').write_text('let g:portable_test_plugin = 42\n')
            (root / 'vimrc').write_text("set nocompatible\nset loadplugins\nexecute 'set runtimepath^=' . fnameescape($IPDB_TEST_ROOT)\n")
            source = root / "source file.py"
            source.write_text('one\ntwo\nthree\nfour\n')
            state = root / 'state'
            Publish(state, str(source), 2)
            script = root / 'test.vim'
            script.write_text('''execute 'source ' . fnameescape($IPDB_BRIDGE)
call assert_equal(42, get(g:, 'portable_test_plugin', 0))
call IpdbSourcePoll(0)
call assert_equal(2, line('.'))
vsplit
wincmd l
call cursor(1, 1)
let other = win_getid()
let message = json_decode(join(readfile($IPDB_VIM_STATE), "\\n"))
let message.line = 4
let message.event = 999
call writefile([json_encode(message)], $IPDB_VIM_STATE)
call IpdbSourcePoll(0)
call assert_equal(other, win_getid())
call assert_equal(1, line('.'))
call win_gotoid(win_getid(1, 1))
call assert_equal(4, line('.'))
call assert_equal('', get(g:, 'ipdb_source_error', ''))
call writefile(v:errors, $IPDB_TEST_ERRORS)
qa!
''')
            env = dict(os.environ, IPDB_TEST_ROOT=str(root), IPDB_BRIDGE=str(Path(__file__).with_name('source_vim.vim').resolve()), IPDB_VIM_STATE=str(state), IPDB_VIM_READY=str(root / 'ready'), IPDB_TEST_ERRORS=str(root / 'errors'))
            result = subprocess.run([shutil.which('vim'), '-N', '-u', str(root / 'vimrc'), '-i', 'NONE', '-n', '-es', '-S', str(script)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'ready').read_text().strip(), 'ready')
            self.assertEqual((root / 'errors').read_text(), '')

    @unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('vim') and shutil.which('tmux'), 'Linux Vim/tmux required')
    def test_linux_tmux_launch_and_exit(self):
        import pty
        import select
        import time
        import fcntl
        import struct
        import termios
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'sample.py'
            script.write_text('value = 41\nprint("RESULT", value + 1)\n')
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
            launcher = Path(__file__).with_name('ipdbVim.sh').resolve()
            process = subprocess.Popen(['sh', str(launcher), str(script)], stdin=slave, stdout=slave, stderr=slave,
                env=dict(os.environ, TERM='xterm', HOME=directory, IPYTHONDIR=directory, IPDB_PYTHON=sys.executable))
            output = bytearray()
            def WaitFor(needle):
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if select.select([master], [], [], 0.1)[0]:
                        try:
                            output.extend(os.read(master, 65536))
                        except OSError:
                            break
                        if needle in output:
                            return
                    if process.poll() is not None:
                        break
                self.fail('Missing {!r}: {!r}'.format(needle, bytes(output[-4000:])))
            try:
                WaitFor(b'ipdb>')
                output.clear()
                os.write(master, b'c\n')
                WaitFor(b'RESULT 42')
                output.clear()
                os.write(master, b'q\n')
                WaitFor(b'Press Enter')
                os.write(master, b'\n')
                process.wait(timeout=10)
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                os.close(master)
                os.close(slave)


if __name__ == '__main__':
    unittest.main()
