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

    def test_command_pane_hides_automatic_source_but_keeps_displays(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'sample.py'
            script.write_text('value = 41\nvalue += 1\nprint(value)\n')
            state = root / 'state'
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(script)],
                input="n\ndisp value\nn\nbt\n!print('MANUAL_STACK')\nwhere\nq\n",
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                timeout=30, cwd=directory,
                env=dict(os.environ, IPDB_VIM_STATE=str(state), IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            before, after = result.stdout.split('MANUAL_STACK', 1)
            self.assertNotIn('---->', before)
            self.assertIn('value = 41', before)
            self.assertIn('value = 42', before)
            self.assertIn('#0', before)
            self.assertIn('sample.py', after)
            self.assertEqual(json.loads(state.read_text())['line'], 3)

    def test_step_into_function_publishes_new_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'helper.py').write_text('def helper():\n    return 42\n')
            (root / 'sample.py').write_text('from helper import helper\nresult = helper()\n')
            state = root / 'state'
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(root / 'sample.py')],
                input='n\ns\nq\n', stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                cwd=directory, timeout=30,
                env=dict(os.environ, IPDB_VIM_STATE=str(state), IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(Path(json.loads(state.read_text())['file']).name, 'helper.py')

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
            alternate = root / 'helper.py'
            alternate.write_text('def helper():\n    return 42\n')
            Publish(state, str(source), 2)
            script = root / 'test.vim'
            script.write_text('''execute 'source ' . fnameescape($IPDB_BRIDGE)
call assert_equal(42, get(g:, 'portable_test_plugin', 0))
call assert_equal('a', &mouse)
call assert_equal(0, &ttyscroll)
call assert_equal('', &t_ut)
call assert_equal(0, &lazyredraw)
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
call IpdbWindowMove('h')
call assert_equal(win_getid(1, 1), win_getid())
call win_gotoid(win_getid(1, 1))
call assert_equal(4, line('.'))
call assert_equal('', get(g:, 'ipdb_source_error', ''))
call win_gotoid(other)
let oldbuffer = bufnr('%')
let message.file = $IPDB_NEW_SOURCE
let message.line = 2
let message.event = 1000
call writefile([json_encode(message)], $IPDB_VIM_STATE)
call IpdbSourcePoll(0)
sleep 30m
call assert_equal(other, win_getid())
call assert_equal(oldbuffer, bufnr('%'))
call win_gotoid(win_getid(1, 1))
call assert_equal(fnamemodify($IPDB_NEW_SOURCE, ':p'), expand('%:p'))
call assert_equal(2, line('.'))
only
split
wincmd j
let oldheight = winheight(0)
execute "normal! \\<C-w>2+"
call assert_equal(oldheight + 2, winheight(0))
execute "normal! \\<C-w>="
call assert_true(abs(winheight(1) - winheight(2)) <= 1)
only
new
call setline(1, map(range(1, 200), 'repeat(string(v:val), 60)'))
vsplit
wincmd l
normal! gg
let still = win_getid()
wincmd h
normal! G
call assert_equal(200, line('.'))
call assert_equal(1, IpdbSourceRedraw(1))
call assert_true(winsaveview().topline > 1)
call win_gotoid(still)
call assert_equal(1, line('.'))
call IpdbSourceRedraw(1)
for target in [10, 190, 35, 120, 1, 200]
  execute 'normal! ' . target . 'G'
  call assert_equal(target, line('.'))
  call IpdbSourceRedraw(1)
endfor
setlocal nowrap
normal! 50G30zl
let horizontal = IpdbViewSignature()
normal! zh
call assert_notequal(horizontal, IpdbViewSignature())
setlocal wrap
normal! ggma
call search('150', 'W')
call assert_equal(150, line('.'))
normal! 'a
call assert_equal(1, line('.'))
execute "normal! \\<C-f>"
call IpdbSourceRedraw(1)
execute "normal! \\<C-b>"
call IpdbSourceRedraw(1)
setlocal foldmethod=manual
10,20fold
normal! 10G
let folded = IpdbViewSignature()
normal! zo
call assert_notequal(folded, IpdbViewSignature())
let unchanged = IpdbViewSignature()
call setline(10, 'replacement')
call assert_notequal(unchanged, IpdbViewSignature())
call writefile(v:errors, $IPDB_TEST_ERRORS)
qa!
''')
            env = dict(os.environ, IPDB_TMUX_SOCKET='test-only', IPDB_TMUX_BIN='tmux', IPDB_TEST_ROOT=str(root), IPDB_NEW_SOURCE=str(alternate), IPDB_BRIDGE=str(Path(__file__).with_name('source_vim.vim').resolve()), IPDB_VIM_STATE=str(state), IPDB_VIM_READY=str(root / 'ready'), IPDB_TEST_ERRORS=str(root / 'errors'))
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
                os.write(master, b'\x17k')
                time.sleep(0.2)
                os.write(master, b'\x17j')
                time.sleep(0.2)
                os.write(master, b'n\n')
                WaitFor(b'ipdb>')
                output.clear()
                os.write(master, b'p val\t\n')
                WaitFor(b'41')
                self.assertNotIn(b'NameError', output)
                output.clear()
                os.write(master, b'\x175+\x17=c\n')
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
