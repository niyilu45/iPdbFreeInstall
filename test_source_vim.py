"""System Vim bridge tests, including real Vim when available."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from launch_mode import SelectMode
from source_vim import Publish, Shell


class VimTests(unittest.TestCase):
    def test_cached_screen_diagnostic_codes(self):
        from source_vim import ProbeCode
        snapshot = {'size': '10x3', 'text': ['code']}
        report = {'columns': 10, 'lines': 3, 'mode': 'n', 'screen': ['code      ', '', '']}
        self.assertTrue(ProbeCode(snapshot, report).startswith('V1'))
        self.assertTrue(ProbeCode(dict(snapshot, text=['old code']), report).startswith('V2'))
        self.assertTrue(ProbeCode(dict(snapshot, size='11x3'), report).startswith('V3'))
        self.assertTrue(ProbeCode(snapshot, dict(report, mode='c')).startswith('V4'))

    def test_focus_diagnostic_codes(self):
        from source_vim import ViewCode
        before = {'pane': '%0', 'size': '234x34', 'text': ['code'], 'style': ['styled code']}
        self.assertEqual(ViewCode(before, dict(before)), 'R3')
        self.assertEqual(ViewCode(before, dict(before, text=['new code'])), 'R1')
        self.assertEqual(ViewCode(before, dict(before, style=['other style'])), 'R2')
        self.assertEqual(ViewCode(before, dict(before, size='234x33')), 'R4')

    def test_mode_and_safe_arguments(self):
        self.assertEqual(SelectMode(['--source-vim', 'my file.py']), ('source-vim', ['my file.py']))
        values = ['python', '/tmp/a b.py', "quote'and$(literal)"]
        self.assertEqual(shlex.split(Shell(values)), ['exec'] + values)

    def test_clean_vim_is_opt_in(self):
        from source_vim import VimCommand
        with mock.patch.dict(os.environ, {'IPDB_VIM_CLEAN': '1'}):
            self.assertEqual(VimCommand('vim', 'bridge.vim'), ['vim', '-N', '-u', 'NONE', '-i', 'NONE', '-S', 'bridge.vim'])
        with mock.patch.dict(os.environ, {'IPDB_VIM_CLEAN': ''}):
            self.assertEqual(VimCommand('vim', 'bridge.vim'), ['vim', '-S', 'bridge.vim'])

    def test_atomic_source_message(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state'
            Publish(path, 'my file.py', 7)
            value = json.loads(path.read_text())
            self.assertEqual(value['line'], 7)
            self.assertTrue(os.path.isabs(value['file']))
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_source_sync_retries_a_removed_or_locked_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state'
            original_replace = os.replace
            attempts = []

            def Replace(temporary, target):
                attempts.append(temporary)
                if len(attempts) == 1:
                    os.unlink(temporary)
                if len(attempts) == 2:
                    raise PermissionError('Sharing violation')
                original_replace(temporary, target)

            with mock.patch('source_vim.os.replace', side_effect=Replace), mock.patch('source_vim.time.sleep'):
                Publish(path, 'my file.py', 7)
            self.assertEqual(len(set(attempts)), 3)
            self.assertEqual(json.loads(path.read_text())['line'], 7)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_missing_session_displays_the_current_source_in_ipdb(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'sample.py'
            script.write_text('value = 42\n')
            state = root / 'missing-session' / 'state'
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(script)],
                input='q\n', stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                cwd=directory, timeout=30,
                env=dict(os.environ, IPDB_VIM_STATE=str(state), IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('Vim session directory is missing', result.stdout)
            self.assertIn('value = 42', re.sub(r'\x1b\[[0-9;]*m', '', result.stdout))
            self.assertFalse(state.parent.exists())

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
            (root / 'helper.py').write_text('def helper():\n    """Return a number."""\n    return 42\n')
            (root / 'sample.py').write_text('from helper import helper\nresult = helper()\n')
            state = root / 'state'
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(root / 'sample.py')],
                input='n\ns\nq\n', stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                cwd=directory, timeout=30,
                env=dict(os.environ, IPDB_VIM_STATE=str(state), IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertNotIn('--Call--', result.stdout)
            position = json.loads(state.read_text())
            self.assertEqual(Path(position['file']).name, 'helper.py')
            self.assertEqual(position['line'], 3)

    def test_step_into_generated_function_reports_missing_source_and_keeps_the_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'sample.py'
            state = root / 'state'
            source.write_text("helper = eval(compile('lambda value: value + 1', '<generated>', 'eval'))\n"
                              'result = helper(31)\n')
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(source)],
                input='n\ns\np value\nbt\nq\n', stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, cwd=directory, timeout=30,
                env=dict(os.environ, IPDB_VIM_STATE=str(state), IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('No source file for <lambda> at <generated>:1', result.stdout)
            self.assertIn('ipdb> 31\n', result.stdout)
            self.assertIn('#0 <lambda> at <generated>:1', result.stdout)
            self.assertNotIn('--Call--', result.stdout)
            position = json.loads(state.read_text())
            self.assertEqual(Path(position['file']).resolve(), source.resolve())
            self.assertEqual(position['line'], 2)

    def test_syntax_error_publishes_the_source_and_can_be_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'broken source.py'
            state, initial, after_edit = [root / name for name in ('state', 'initial', 'after_edit')]
            source.write_text('value = 41\nif :\n    value += 1\n')
            invalid_edit = 'value = 41\n\nif :\n    value += 1\n'
            valid_edit = 'value = 41\nprint("RECOVERED", value + 1)\n'
            pid = "!print('SYNTAX_SESSION_PID', __import__('os').getpid())"
            copy = "!__import__('shutil').copyfile({!r}, {!r})"
            commands = [pid, copy.format(str(state), str(initial)),
                        "!open({!r}, 'w').write({!r})".format(str(source), invalid_edit), 'r',
                        copy.format(str(state), str(after_edit)),
                        "!open({!r}, 'w').write({!r})".format(str(source), valid_edit),
                        'r', pid, 'c', 'q', '']
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(source)],
                input='\n'.join(commands), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                cwd=directory, timeout=30,
                env=dict(os.environ, IPDB_VIM_STATE=str(state), IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('SyntaxError: invalid syntax', result.stdout)
            self.assertIn('Cannot reload saved source', result.stdout)
            self.assertIn('RECOVERED 42', result.stdout)
            pids = re.findall(r'SYNTAX_SESSION_PID (\d+)', result.stdout)
            self.assertEqual(len(pids), 2, result.stdout)
            self.assertEqual(pids[0], pids[1])
            for snapshot, line in ((initial, 2), (after_edit, 3)):
                position = json.loads(snapshot.read_text())
                self.assertEqual(Path(position['file']).resolve(), source.resolve())
                self.assertEqual(position['line'], line)

    def test_can_quit_at_the_initial_syntax_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'broken.py'
            source.write_text('if :\n    pass\n')
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(source)],
                input='q\n', stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                cwd=directory, timeout=30,
                env=dict(os.environ, IPDB_VIM_STATE=str(root / 'state'), IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(result.stdout.count('SyntaxError: invalid syntax'), 1, result.stdout)

    @unittest.skipIf(sys.version_info < (3, 7), 'Module launching requires Python 3.7')
    def test_module_syntax_error_publishes_the_real_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'broken_module.py'
            source.write_text('value = 41\nif :\n    value += 1\n')
            state = root / 'state'
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', '-m', 'broken_module'],
                input='q\n', stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                cwd=directory, timeout=30,
                env=dict(os.environ, IPDB_VIM_STATE=str(state), IPY_TEST_SIMPLE_PROMPT='1',
                         IPYTHONDIR=directory, PYTHONPATH=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            position = json.loads(state.read_text())
            self.assertEqual(Path(position['file']).resolve(), source.resolve())
            self.assertEqual(position['line'], 2)

    @unittest.skipUnless(shutil.which('vim'), 'System Vim required')
    def test_real_vim_loads_plugins_and_only_moves_main(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'plugin').mkdir()
            (root / 'plugin/probe.vim').write_text('let g:portable_test_plugin = 42\n')
            (root / 'vimrc').write_text("set nocompatible\nset loadplugins\nexecute 'set runtimepath^=' . fnameescape($IPDB_TEST_ROOT)\n")
            source = root / "source file.py"
            source.write_text('def one():\n    return 2\nthree\nfour\n')
            state = root / 'state'
            alternate = root / 'helper.py'
            alternate.write_text('def helper():\n    return 42\n')
            Publish(state, str(source), 2)
            script = root / 'test.vim'
            script.write_text('''execute 'source ' . fnameescape($IPDB_BRIDGE)
call assert_equal(42, get(g:, 'portable_test_plugin', 0))
call assert_true(exists('g:syntax_on'))
call assert_match('IpdbSearchRedraw', execute('autocmd PortableIpdbRedraw CmdlineChanged'))
let old_search_signature = IpdbViewSignature()
let @/ = 'search_probe'
call assert_notequal(old_search_signature, IpdbViewSignature())

call assert_equal('a', &mouse)
call assert_equal(0, &ttyscroll)
call assert_equal('', &t_ut)
call assert_equal(0, &lazyredraw)
let initial_buffer = bufnr('%')
call IpdbSourcePoll(1)
call assert_equal(initial_buffer, bufnr('%'))
call assert_equal('', expand('%'))
IpdbViewInfo
call assert_match('background_erase', execute('messages'))
call IpdbSourcePoll(0)
call assert_equal(2, line('.'))
call assert_equal('pythonStatement', synIDattr(synID(1, 1, 1), 'name'))
setlocal cursorline
call IpdbExecutionCursor()
call assert_equal(0, &l:cursorline)
call cursor(1, 1)
call IpdbExecutionCursor()
call assert_equal(1, &l:cursorline)
call cursor(2, 1)
call IpdbExecutionCursor()
call assert_equal(0, &l:cursorline)
call assert_match('linehl=IpdbExecution', execute('sign list IpdbExecution'))

execute 'IpdbViewDump ' . fnameescape($IPDB_TEST_ROOT . '/view-dump.json')
let diagnostic = json_decode(join(readfile($IPDB_TEST_ROOT . '/view-dump.json'), "\\n"))
call assert_equal(expand('%:p'), diagnostic.file)
call assert_equal(2, diagnostic.view.lnum)
call assert_true(has_key(diagnostic.options, 't_ce'))
call assert_true(has_key(diagnostic, 'vim_screen'))
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
let sign_group = exists('*sign_place') ? ' group=IpdbExecution' : ''
let signs = execute('sign place' . sign_group . ' buffer=' . bufnr('%'))
call assert_match('line=4 .*name=IpdbExecution', signs)
call assert_notmatch('line=2 .*name=IpdbExecution', signs)
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
call assert_notmatch('name=IpdbExecution', execute('sign place' . sign_group . ' buffer=' . oldbuffer))
call assert_equal('yes', &l:signcolumn)
enew
call assert_equal('auto', &l:signcolumn)
call win_gotoid(win_getid(1, 1))
call assert_equal(fnamemodify($IPDB_NEW_SOURCE, ':p'), expand('%:p'))
call assert_equal('python', &syntax)
call assert_equal(2, line('.'))
only
call writefile(['# saved edit', 'def helper():', '    return 99'], $IPDB_NEW_SOURCE)
let message.line = 3
let message.event = 1001
call writefile([json_encode(message)], $IPDB_VIM_STATE)
call IpdbSourcePoll(0)
call assert_equal('    return 99', getline(3))
call assert_equal(3, line('.'))
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
normal! gg0
let first_viewport = IpdbViewSignature()
normal! 2l
call assert_equal(first_viewport, IpdbViewSignature())
normal! G
call assert_equal(200, line('.'))
call assert_equal(0, IpdbSourceRedraw(1))
call assert_true(winsaveview().topline > 1)
call assert_notequal(first_viewport, IpdbViewSignature())
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
            result = subprocess.run([shutil.which('vim'), '-V1' + str(root / 'vim.log'), '-N', '-u', str(root / 'vimrc'), '-i', 'NONE', '-n', '-es', '-S', str(script)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            self.assertEqual(result.returncode, 0, (root / 'vim.log').read_text(errors='replace'))
            self.assertEqual((root / 'ready').read_text().strip(), 'ready')
            self.assertEqual((root / 'errors').read_text(), '')

    @unittest.skipUnless(shutil.which('vim'), 'System Vim required')
    def test_native_vim_cursor_motion_does_not_queue_a_bridge_redraw(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'cursor.vim'
            script.write_text('''execute 'source ' . fnameescape($IPDB_BRIDGE)
let before = map(timer_info(), 'v:val.id')
call setline(1, 'cursor motion')
normal! 3l
doautocmd CursorMoved
call assert_equal(before, map(timer_info(), 'v:val.id'))
call writefile(v:errors, $IPDB_TEST_ERRORS)
qa!
''')
            env = dict(os.environ, IPDB_TMUX_SOCKET='', IPDB_TEST_ERRORS=str(root / 'errors'),
                       IPDB_VIM_READY=str(root / 'ready'), IPDB_VIM_STATE=str(root / 'state'),
                       IPDB_BRIDGE=str(Path(__file__).with_name('source_vim.vim').resolve()))
            result = subprocess.run([shutil.which('vim'), '-N', '-u', 'NONE', '-i', 'NONE', '-n', '-es', '-S', str(script)],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'errors').read_text(), '')

    @unittest.skipUnless(shutil.which('vim'), 'System Vim required')
    def test_vim_publishes_loaded_python_buffers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / 'One' / 'Shared.py'
            second = root / 'Two' / 'Shared.py'
            uppercase = root / 'UpperCase.PY'
            for source in (first, second, uppercase):
                source.parent.mkdir(exist_ok=True)
                source.write_text('value = 1\n')
            script = root / 'buffers.vim'
            script.write_text('''execute 'source ' . fnameescape($IPDB_BRIDGE)
set hidden
function! s:BufferFiles() abort
  call IpdbSourcePoll(0)
  return json_decode(join(readfile($IPDB_VIM_STATE . '.buffers'), ""))
endfunction
execute 'edit ' . fnameescape($IPDB_TEST_ROOT . '/One/Shared.py')
let first = bufnr('%')
let snapshots = [s:BufferFiles()]
execute 'edit ' . fnameescape($IPDB_TEST_ROOT . '/Two/Shared.py')
execute 'edit ' . fnameescape($IPDB_TEST_ROOT . '/UpperCase.PY')
call add(snapshots, s:BufferFiles())
execute 'bunload ' . first
call add(snapshots, s:BufferFiles())
call writefile([json_encode(snapshots)], $IPDB_TEST_ROOT . '/snapshots')
qa!
''')
            env = dict(os.environ, IPDB_TMUX_SOCKET='', IPDB_VIM_CLOSE='', IPDB_TEST_ROOT=root.as_posix(),
                       IPDB_VIM_READY=str(root / 'ready'), IPDB_VIM_STATE=str(root / 'state'),
                       IPDB_BRIDGE=Path(__file__).with_name('source_vim.vim').resolve().as_posix())
            result = subprocess.run([shutil.which('vim'), '-N', '-u', 'NONE', '-i', 'NONE', '-n', '-es', '-S', script.as_posix()],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'ready').read_text().strip(), 'ready')
            snapshots = json.loads((root / 'snapshots').read_text(encoding='utf-8'))
            self.assertEqual([set(map(Path, paths)) for paths in snapshots],
                             [{first}, {first, second, uppercase}, {second, uppercase}])

    @unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('vim') and shutil.which('tmux'), 'Linux Vim/tmux required')
    def test_paging_repaints_missing_terminal_rows(self):
        import time
        import uuid

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'rows.py'
            row = 'same_source_text = "unchanged across pages"'
            source.write_text((row + '\n') * 200)
            state = root / 'state'
            Publish(state, str(source), 1)
            command = ['tmux', '-L', 'ipdb-missing-rows-' + uuid.uuid4().hex, '-f', '/dev/null']
            env = dict(os.environ, TERM='xterm-256color', HOME=directory, IPDB_TMUX_SOCKET=command[2],
                       IPDB_VIM_STATE=str(state), IPDB_VIM_READY=str(root / 'ready'))

            def Tmux(*args):
                return subprocess.check_output(command + list(args), env=env, universal_newlines=True)

            def SourceRows():
                return Tmux('capture-pane', '-p', '-t', 'page:0.0').splitlines()

            def Ex(statement):
                Tmux('send-keys', '-t', 'page:0.0', '-l', ':' + statement)
                Tmux('send-keys', '-t', 'page:0.0', 'Enter')

            try:
                Vim = Shell(['vim', '-N', '-u', 'NONE', '-i', 'NONE', '-n', '-S', Path(__file__).with_name('source_vim.vim').resolve()])
                Tmux('new-session', '-d', '-s', 'page', '-x', '90', '-y', '24', Vim)
                Tmux('split-window', '-v', '-t', 'page:0.0', str(shutil.which('cat')))
                tty = Tmux('display-message', '-p', '-t', 'page:0.0', '#{pane_tty}').strip()
                deadline = time.monotonic() + 5
                while row not in SourceRows()[2] and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertIn(row, SourceRows()[2])
                # Control when the deferred repair runs, after ordinary drawing.
                Ex('set eventignore=all | call timer_stop(g:ipdb_source_timer)')
                time.sleep(0.2)

                scrolled = root / 'scrolled'
                scrolled_path = str(scrolled).replace("'", "''")
                previous_top = 1
                for key in ('C-f', 'C-d', 'C-e', 'C-u', 'C-b'):
                    Ex('execute "normal! \\<{}>" | redraw | call writefile([string(line(\'w0\'))], \'{}\')'.format(
                        key, scrolled_path))
                    deadline = time.monotonic() + 2
                    while not scrolled.exists() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue(scrolled.exists(), key)
                    top = int(scrolled.read_text())
                    self.assertNotEqual(top, previous_top, key)
                    previous_top = top
                    scrolled.unlink()
                    # Erase a row in tmux while Vim still believes it is drawn.
                    with open(tty, 'wb', buffering=0) as terminal:
                        terminal.write(b'\x1b7\x1b[3;1H\x1b[2K\x1b8')
                    self.assertEqual(SourceRows()[2], '')
                    Ex('call timer_start(20, {timer -> IpdbSourceRedraw(0)})')
                    deadline = time.monotonic() + 2
                    while row not in SourceRows()[2] and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertIn(row, SourceRows()[2], key)
            finally:
                subprocess.run(command + ['kill-server'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    @unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('vim') and shutil.which('tmux'), 'Linux Vim/tmux required')
    def test_tmux_source_cells_after_jump_and_file_change(self):
        import time
        import uuid
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'rows.py'
            rows = ["row_{:04d} = {!r}".format(i, 'X' * (170 if i % 2 else 3)) for i in range(1, 201)]
            source.write_text('\n'.join(rows) + '\n')
            other = root / 'other.py'
            other.write_text('def other():\n    return 99\n')
            state = root / 'state'
            Publish(state, str(source), 1)
            command = ['tmux', '-L', 'ipdb-cells-' + uuid.uuid4().hex, '-f', '/dev/null']
            env = dict(os.environ, TERM='xterm-256color', IPDB_TMUX_SOCKET=command[2],
                       IPDB_VIM_STATE=str(state), IPDB_VIM_READY=str(root / 'ready'), HOME=directory)
            def Tmux(*args):
                return subprocess.check_output(command + list(args), env=env, universal_newlines=True)
            from source_vim import Shell
            try:
                Tmux('new-session', '-d', '-s', 'cells', '-x', '234', '-y', '34',
                     Shell(['vim', '-N', '-u', 'NONE', '-i', 'NONE', '-n', '-S', Path(__file__).with_name('source_vim.vim').resolve()]))
                deadline = time.monotonic() + 10
                while not (root / 'ready').exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue((root / 'ready').exists())
                while 'row_0001' not in Tmux('capture-pane', '-p', '-t', 'cells:0.0') and time.monotonic() < deadline:
                    time.sleep(0.05)
                def VisibleRows():
                    screen = Tmux('capture-pane', '-p', '-t', 'cells:0.0')
                    visible = [line.strip().lstrip('>').strip() for line in screen.splitlines() if 'row_' in line]
                    self.assertTrue(visible and all(line in rows for line in visible), screen)
                    numbers = [int(line[4:8]) for line in visible]
                    self.assertEqual(numbers, list(range(numbers[0], numbers[-1] + 1)), screen)
                    return numbers

                for target in (200, 12, 180, 35, 1):
                    Tmux('send-keys', '-t', 'cells:0.0', str(target) + 'G')
                    time.sleep(0.2)
                    self.assertIn(target, VisibleRows())
                previous = VisibleRows()[0]
                for key, direction in (('C-f', 1), ('C-b', -1), ('C-d', 1), ('C-u', -1),
                                       ('PageDown', 1), ('PageUp', -1)):
                    Tmux('send-keys', '-t', 'cells:0.0', key)
                    time.sleep(0.2)
                    current = VisibleRows()[0]
                    self.assertGreater((current - previous) * direction, 0, key)
                    previous = current
                Publish(state, str(source), 100)
                time.sleep(0.3)
                centered = VisibleRows()[0]
                Tmux('send-keys', '-t', 'cells:0.0', 'C-e')
                time.sleep(0.2)
                scrolled = VisibleRows()[0]
                self.assertGreater(scrolled, centered)
                Publish(state, str(source), 100)
                time.sleep(0.3)
                self.assertEqual(VisibleRows()[0], scrolled)
                Publish(state, str(other), 2)
                time.sleep(0.3)
                screen = Tmux('capture-pane', '-p', '-t', 'cells:0.0')
                self.assertIn('return 99', screen)
                self.assertNotIn('row_', screen)
            finally:
                subprocess.run(command + ['kill-server'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

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
            script.write_text('value = 41\nprint("RESULT", value + 1)\n' +
                              ''.join('# source row {:03d}\n'.format(row) for row in range(1, 121)))
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
            launcher = Path(__file__).with_name('ipdbVim.sh').resolve()
            binary = root / 'bin'
            binary.mkdir()
            refresh_log = root / 'refresh.log'
            wrapper = binary / 'tmux'
            wrapper.write_text('#!/bin/sh\ncase " $* " in\n*" refresh-client "*) printf "refresh\\n" >> ' + shlex.quote(str(refresh_log)) + ';;\nesac\nexec ' + shlex.quote(shutil.which('tmux')) + ' "$@"\n')
            wrapper.chmod(0o755)
            process = subprocess.Popen(['sh', str(launcher), str(script)], stdin=slave, stdout=slave, stderr=slave,
                env=dict(os.environ, TERM='xterm', HOME=directory, IPYTHONDIR=directory, IPDB_PYTHON=sys.executable,
                         PATH=str(binary) + os.pathsep + os.environ['PATH'], IPDB_VIM_CLIENT_REFRESH='1'))
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
                time.sleep(0.25)
                self.assertFalse(refresh_log.exists(), 'Focusing Vim must not repaint the client')
                os.write(master, b'j')
                time.sleep(0.25)
                self.assertFalse(refresh_log.exists(), 'Cursor motion must not repaint the client')
                # Simulate older Vim or dropped events. Ctrl+E can scroll without moving the cursor.
                os.write(master, b":let &eventignore = exists('##WinScrolled') ? 'CursorMoved,WinScrolled' : 'CursorMoved'\r")
                time.sleep(0.15)
                os.write(master, b'\x05')
                time.sleep(0.4)
                self.assertFalse(refresh_log.exists(), 'Automatic scrolling must stay within the source pane')
                os.write(master, b':set eventignore&\r')
                time.sleep(0.15)
                output.clear()
                os.write(master, b'G')
                WaitFor(b'source row 120')
                time.sleep(0.25)
                self.assertFalse(refresh_log.exists(), 'Viewport jumps must not repaint the tmux client')
                os.write(master, b'\x0c')
                deadline = time.monotonic() + 3
                while not refresh_log.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(refresh_log.exists(), 'Ctrl+L must explicitly repaint the tmux client')
                time.sleep(0.2)
                self.assertEqual(len(refresh_log.read_text().splitlines()), 1, 'Idle sessions must not repaint repeatedly')
                os.write(master, b'\x17j')
                time.sleep(0.2)
                os.write(master, b'n\n')
                WaitFor(b'ipdb>')
                output.clear()
                os.write(master, b'p val\t\n')
                WaitFor(b'41')
                self.assertNotIn(b'NameError', output)
                time.sleep(0.2)
                output.clear()
                os.write(master, b'vc\n')
                WaitFor(b'S0:')
                os.write(master, b'\x17k')
                time.sleep(0.2)
                os.write(master, b'\x17j')
                time.sleep(0.2)
                output.clear()
                os.write(master, b'vc\n')
                WaitFor(b'R3:')
                output.clear()
                os.write(master, b'vd\n')
                WaitFor(b'V1 rows=')
                output.clear()
                os.write(master, b'vr\n')
                WaitFor(b'F1:')
                script.write_text('# saved edit\nvalue = 41\nprint("NEW_RESULT", value + 1)\n')
                output.clear()
                os.write(master, b'r\n')
                WaitFor(b'Reloading saved project source')
                self.assertIsNone(process.poll())
                output.clear()
                for key in b'\x175+\x17=c\n':
                    os.write(master, bytes([key]))
                    time.sleep(0.1)
                WaitFor(b'NEW_RESULT 42')
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
