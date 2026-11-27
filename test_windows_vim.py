"""Windows launcher argument routing and lifecycle regression tests."""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import windows_vim


class WindowsVimTests(unittest.TestCase):
    def test_terminal_command_uses_config_not_debuggee_shell_text(self):
        command = windows_vim.TerminalCommand('wt.exe', 'ipdb-test', 'C:/space dir/config.json')
        self.assertIn('split-pane', command)
        self.assertIn('-H', command)
        self.assertEqual(command.count('C:/space dir/config.json'), 2)
        self.assertEqual(command.count(';'), 1)

    def test_ready_failure_has_explanation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {'ready': str(root / 'ready'), 'vim_result': str(root / 'result')}
            (root / 'result').write_text('1')
            with self.assertRaisesRegex(RuntimeError, 'exited before debugging started'):
                windows_vim.WaitReady(config)

    def test_version_probe_accepts_non_ascii_output(self):
        vim = 'vim.exe'
        version = b'Compiled by \xe5\x85\x85\n+timers +eval +signs\n'
        with mock.patch('sys.stdin.isatty', return_value=True), mock.patch('sys.stdout.isatty', return_value=True), \
                mock.patch('windows_vim.shutil.which', return_value=vim), \
                mock.patch('windows_vim.subprocess.check_output', return_value=version) as probe, \
                mock.patch('windows_vim.tempfile.TemporaryDirectory', side_effect=RuntimeError('probe passed')):
            with self.assertRaisesRegex(RuntimeError, 'probe passed'):
                windows_vim.Run(['sample.py'])
            probe.assert_called_once_with([vim, '--version'])

    def test_debug_preserves_arguments_and_exit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {key: str(root / key) for key in ('state', 'ready', 'close', 'closed', 'result', 'done', 'vim_result')}
            config.update(debugger=['python', 'portable_ipdb.py', '--plain', 'space file.py', 'a&b'], cwd=directory)
            (root / 'ready').write_text('ready')
            with mock.patch('windows_vim.subprocess.Popen') as call, \
                    mock.patch('source_vim.WaitForDebugger', return_value=7), \
                    mock.patch('builtins.input', side_effect=['\x1b[O', '']) as read:
                self.assertEqual(windows_vim.Debug(config), 7)
            self.assertEqual(read.call_count, 2)
            self.assertEqual(call.call_args[0][0], config['debugger'])
            self.assertEqual((root / 'result').read_text(), '7')
            self.assertTrue((root / 'done').exists())

    def test_windows_process_probe_distinguishes_exit_from_access_denied(self):
        kernel = mock.Mock()
        kernel.OpenProcess.return_value = 0
        with mock.patch('ctypes.WinDLL', return_value=kernel, create=True), \
                mock.patch('ctypes.get_last_error', side_effect=[5, 87], create=True):
            self.assertIsNone(windows_vim.IsRunning(123))
            self.assertIs(windows_vim.IsRunning(123), False)
            kernel.OpenProcess.return_value = 1
            kernel.WaitForSingleObject.side_effect = [258, 0xFFFFFFFF, 0]
            self.assertIs(windows_vim.IsRunning(123), True)
            self.assertIsNone(windows_vim.IsRunning(123))
            self.assertIs(windows_vim.IsRunning(123), False)

    def test_conemu_runtime_navigation_surrounds_debugger_child(self):
        from contextlib import contextmanager
        from conemu_keys import PROMPT_EVENT

        steps = []

        @contextmanager
        def Navigation(environment, switch, resize):
            steps.append('listen')
            self.assertIs(switch, windows_vim.ConEmuMacro)
            resize('+', 5)
            environment[PROMPT_EVENT] = 'session-event'
            try:
                yield
            finally:
                steps.append('stop')

        def RunDebugger(*args, **options):
            steps.append('run')
            self.assertEqual(options['env'][PROMPT_EVENT], 'session-event')
            return mock.Mock()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {key: str(root / key) for key in ('state', 'ready', 'close', 'closed', 'result', 'done',
                                                       'vim_result', 'source_pane', 'origin_environment', 'config_path')}
            config.update(debugger=['python', 'portable_ipdb.py', 'sample.py'], cwd=directory)
            (root / 'ready').write_text('ready')
            (root / 'origin_environment').write_text('{}')
            with mock.patch('sys.stdin.isatty', return_value=True), mock.patch('sys.stdout.isatty', return_value=True), \
                    mock.patch('sys.stderr.isatty', return_value=True), mock.patch('windows_vim.ConfirmConEmuSplit'), \
                    mock.patch('conemu_keys.RuntimeNavigation', side_effect=Navigation), \
                    mock.patch('windows_vim.ResizeConEmu') as resize, \
                    mock.patch('windows_vim.subprocess.Popen', side_effect=RunDebugger), \
                    mock.patch('source_vim.WaitForDebugger', return_value=0), mock.patch('builtins.input', return_value=''):
                self.assertEqual(windows_vim.Debug(config), 0)
            resize.assert_called_once_with('+', 5, config_path=config['config_path'])
        self.assertEqual(steps, ['listen', 'run', 'stop'])

    def test_windows_waits_for_session_when_worker_is_unknown_or_debugger_is_alive(self):
        for statuses in ([None], [False, True], [False, None], [KeyboardInterrupt, True]):
            with self.subTest(statuses=statuses), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = {name: str(root / name) for name in ('done', 'result', 'debug_pid', 'debugger_pid')}
                (root / 'debug_pid').write_text('123')
                (root / 'debugger_pid').write_text('456')

                def Finish(_):
                    (root / 'result').write_text('7')
                    (root / 'done').touch()

                with mock.patch('windows_vim.IsRunning', side_effect=statuses) as probe, \
                        mock.patch('windows_vim.time.sleep', side_effect=Finish):
                    self.assertEqual(windows_vim.WaitForDebugPane(config, 'ConEmu'), 7)
                self.assertEqual(probe.call_count, len(statuses))

    def test_windows_reports_closed_pane_after_both_processes_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {name: str(root / name) for name in ('done', 'result', 'debug_pid', 'debugger_pid')}
            (root / 'debug_pid').write_text('123')
            (root / 'debugger_pid').write_text('456')
            with mock.patch('windows_vim.IsRunning', return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'ConEmu debugger pane was closed'):
                    windows_vim.WaitForDebugPane(config, 'ConEmu')

    def test_conemu_launcher_waits_for_debugger_in_new_pane(self):
        def OpenPane(config, config_path):
            self.assertEqual(json.loads(config_path.read_text()), config)
            self.assertEqual(config['debugger'][-2:], ['sample.py', 'a&b'])
            self.assertNotIn('IPDB_CONEMU_TTY', json.loads(Path(config['origin_environment']).read_text()))
            Path(config['vim_result']).write_text('0')

        with mock.patch.dict(os.environ, {'ConEmuPID': '123', 'IPDB_CONEMU_TTY': '1'}), \
                mock.patch('sys.stdin.isatty', return_value=False), mock.patch('sys.stdout.isatty', return_value=False), \
                mock.patch('windows_vim.shutil.which', return_value='vim.exe') as which, \
                mock.patch('windows_vim.subprocess.check_output', return_value=b'MS-Windows +timers +eval +signs'), \
                mock.patch('windows_vim.ConEmuHelper', return_value='ConEmuC64.exe'), \
                mock.patch('windows_vim.OpenConEmuVim', side_effect=OpenPane) as pane, \
                mock.patch('windows_vim.WaitForDebugPane', return_value=7) as wait, \
                mock.patch('windows_vim.subprocess.Popen') as spawn, mock.patch('windows_vim.subprocess.check_call') as terminal:
            self.assertEqual(windows_vim.Run(['sample.py', 'a&b']), 7)
            self.assertNotIn('IPDB_CONEMU_TTY', os.environ)
            which.assert_called_once_with('vim')
            pane.assert_called_once()
            wait.assert_called_once_with(pane.call_args[0][0], 'ConEmu')
            spawn.assert_not_called()
            terminal.assert_not_called()

    def test_conemu_opens_vim_before_splitting_below_it(self):
        with tempfile.TemporaryDirectory() as directory:
            pane = Path(directory, 'source_pane')
            debug = Path(directory, 'debug_pane')
            pane.write_text(json.dumps({'server': '456', 'draw': '0x25'}), encoding='ascii')
            debug.write_text(json.dumps({'server': '789', 'draw': '0x12'}), encoding='ascii')
            config = dict(source_pane=str(pane), debug_pane=str(debug), conemu_helper='ConEmuC64.exe')
            for reply in (b'OK', b'OK;OK', subprocess.TimeoutExpired('ConEmuC64.exe', 5)):
                with self.subTest(reply=reply), mock.patch.dict(os.environ, {'ConEmuPID': '123'}), \
                        mock.patch('windows_vim.ConEmuMacro', side_effect=[b'OK', reply]) as macro:
                    windows_vim.OpenConEmuVim(config, Path(directory, 'session file.json'))
                    first, second = macro.call_args_list
                    self.assertEqual(first[1]['server'], 123)
                    self.assertIn('Shell("new_console:fnt:ipdb"', first[0][0])
                    self.assertIn('--vim', first[0][0])
                    self.assertEqual(second[1]['server'], 456)
                    self.assertIn('Tab(7,-2);Shell("new_console:s50Vfnh32766"', second[0][0])
                    self.assertIn('--debug', second[0][0])

    def test_conemu_reply_alone_does_not_confirm_a_pane(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch('windows_vim.ConEmuMacro', return_value=b'OK;OK'), \
                mock.patch('windows_vim.time.monotonic', side_effect=[0, 11]):
            config = dict(debug_pane=str(Path(directory, 'debug_pane')), conemu_helper='ConEmuC64.exe')
            with self.assertRaisesRegex(RuntimeError, 'did not create the debugger pane'):
                windows_vim.RequestConEmuPane(config, 'debug_pane', 'Shell()', 456,
                                              'ConEmu did not create the debugger pane')

    def test_conemu_debugger_cannot_start_after_launcher_cancelled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {name: str(root / name) for name in ('source_pane', 'debug_pane', 'debug_pid', 'start', 'close')}
            config_file = root / 'config.json'
            config_file.write_text(json.dumps(config))
            (root / 'close').touch()
            with mock.patch('windows_vim.Debug') as debug:
                self.assertEqual(windows_vim.Worker('--debug', config_file), 1)
                debug.assert_not_called()

    def test_conemu_rejects_inverted_or_hidden_splits(self):
        for bounds, expected in (([(0, 25), (25, 50)], True),
                                 ([(25, 50), (0, 25)], False),
                                 ([(0, 50), (25, 50)], False),
                                 ([None, (0, 50)], False)):
            with self.subTest(bounds=bounds), mock.patch('windows_vim.PaneBounds', side_effect=bounds):
                self.assertEqual(windows_vim.ConEmuSplitVisible(0x25, 0x12), expected)

    def test_conemu_resize_helper_attaches_to_pane_console(self):
        kernel = mock.Mock()
        with mock.patch('ctypes.WinDLL', return_value=kernel, create=True):
            windows_vim.AttachConEmuConsole(123)
        self.assertEqual(kernel.mock_calls, [mock.call.FreeConsole(), mock.call.AttachConsole(123)])

    def test_conemu_resize_uses_current_pane_and_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {name: str(root / name) for name in ('source_pane', 'debug_pane')}
            (root / 'config').write_text(json.dumps(config))
            (root / 'source_pane').write_text(json.dumps({'server': '123', 'draw': '0x25'}))
            (root / 'debug_pane').write_text(json.dumps({'server': '456', 'draw': '0x12'}))
            cases = (
                ('123', '+', 3, (0, 330), ['Split(1,0,3)']),
                ('456', '+', 5, (276, 503), ['Split(1,0,-5)', 'Split(1,0,-1)']),
                ('123', '-', 3, (0, 290), ['Split(1,0,-3)', 'Split(1,0,-1);Split(1,0,-1)']),
                ('456', '-', 1, None, ['Split(1,0,1)']),
                ('123', '=', 1, (0, 240), ['Split(1,0,-5)', 'Split(1,0,1)']),
                ('123', '+', 9999, (0, 480), ['Split(1,0,18)', 'Split(1,0,1);Split(1,0,1)']),
            )
            for server, action, count, actual, commands in cases:
                with self.subTest(server=server, action=action), \
                        mock.patch.dict(os.environ, {'IPDB_CONEMU_CONFIG': str(root / 'config'), 'ConEmuServerPID': server}), \
                        mock.patch('windows_vim.PaneBounds', side_effect=[(0, 300), (303, 503), actual]), \
                        mock.patch('windows_vim.ConsoleRows', return_value=30) as rows, \
                        mock.patch.dict(sys.modules, {'msvcrt': mock.Mock()}), \
                        mock.patch('windows_vim.ConEmuMacro') as macro:
                    windows_vim.ResizeConEmu(action, count)
                    self.assertEqual([mock.call(command) for command in commands], macro.call_args_list)
                    rows.assert_called_once_with()

    def test_conemu_resizes_under_its_session_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory, 'config.json')
            locked = mock.Mock()
            locked.locking.side_effect = [OSError(errno.EACCES, 'lock busy'), None]
            with mock.patch.dict(os.environ, {'IPDB_CONEMU_CONFIG': str(config)}), \
                    mock.patch.dict(sys.modules, {'msvcrt': locked}), mock.patch('windows_vim.time.sleep'), \
                    mock.patch('windows_vim.ResizeConEmuPanes') as resize:
                windows_vim.ResizeConEmu('+', 3)
            self.assertEqual(2, locked.locking.call_count)
            self.assertTrue(config.with_suffix('.resize-lock').is_file())
            resize.assert_called_once_with(config, '+', 3)

    def test_conemu_focus_chord_is_consumed_without_invalidating_prompt(self):
        import asyncio

        try:
            import prompt_toolkit
            from IPython.terminal.debugger import TerminalPdb
            from ipdb import __main__ as ipdb_main
        except ImportError:
            self.skipTest('ConEmu debugger dependencies are required')
        if not hasattr(asyncio, 'run') or tuple(map(int, prompt_toolkit.__version__.split('.')[:3])) < (3, 0, 28):
            self.skipTest('A current prompt-toolkit event loop is required')

        from prompt_toolkit.application.current import set_app
        from prompt_toolkit.input import DummyInput
        from prompt_toolkit.key_binding.key_processor import KeyPress
        from prompt_toolkit.keys import Keys
        from prompt_toolkit.output import DummyOutput

        prompt = prompt_toolkit.PromptSession(input=DummyInput(), output=DummyOutput())
        debugger = mock.Mock(pt_app=prompt)
        with mock.patch.object(TerminalPdb, 'pt_init'), mock.patch.object(ipdb_main, '_get_debugger_cls'):
            windows_vim.InstallConEmuKeys()
            TerminalPdb.pt_init(debugger)

        async def CheckInput():
            with set_app(prompt.app), mock.patch.object(prompt.app, 'invalidate') as invalidate, \
                    mock.patch('windows_vim.ConEmuMacro') as macro:
                processor = prompt.app.key_processor
                processor.feed(KeyPress(Keys.ControlW))
                processor.feed(KeyPress('k'))
                processor.process_keys()
                macro.assert_called_once_with('Split(2,0,-1)')
                self.assertEqual(prompt.default_buffer.text, '')
                invalidate.assert_not_called()

                processor.feed(KeyPress('x'))
                processor.process_keys()
                self.assertEqual(prompt.default_buffer.text, 'x')
                invalidate.assert_called()

        asyncio.run(CheckInput())

    def test_conemu_prefers_a_supported_native_vim_over_msys_vim(self):
        features = b' +timers +eval +signs'
        with tempfile.TemporaryDirectory() as directory:
            msys = Path(directory, 'git', 'vim.exe')
            native = Path(directory, 'native', 'vim.exe')
            for candidate in (msys, native):
                candidate.parent.mkdir()
                candidate.touch()
            with mock.patch('windows_vim.shutil.which', return_value=str(msys)), \
                    mock.patch('windows_vim.os.get_exec_path', return_value=[str(msys.parent), str(native.parent)]), \
                    mock.patch('windows_vim.subprocess.check_output', side_effect=[b'MSYS' + features, b'MS-Windows' + features]) as probe:
                self.assertEqual(windows_vim.SelectVim(conemu=True), str(native))
                self.assertEqual(probe.call_count, 2)

    def test_pipe_requires_conemu_and_an_interactive_shell(self):
        for environment in ({'ConEmuPID': '123'}, {'IPDB_CONEMU_TTY': '1'}):
            with self.subTest(environment=environment), mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch('sys.stdin.isatty', return_value=False), mock.patch('sys.stdout.isatty', return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'requires an interactive Windows console'):
                    windows_vim.Run(['sample.py'])

    def test_conemu_split_failure_is_reported(self):
        with mock.patch.dict(os.environ, {'ConEmuPID': '123'}), \
                mock.patch('sys.stdin.isatty', return_value=True), mock.patch('sys.stdout.isatty', return_value=True), \
                mock.patch('windows_vim.shutil.which', return_value='vim.exe'), \
                mock.patch('windows_vim.subprocess.check_output', return_value=b'MS-Windows +timers +eval +signs'), \
                mock.patch('windows_vim.ConEmuHelper', return_value='ConEmuC64.exe'), \
                mock.patch('windows_vim.OpenConEmuVim', side_effect=RuntimeError('split failed')), \
                mock.patch('windows_vim.subprocess.Popen') as spawn, mock.patch('windows_vim.subprocess.check_call') as terminal:
            with self.assertRaisesRegex(RuntimeError, 'split failed'):
                windows_vim.Run(['sample.py'])
            spawn.assert_not_called()
            terminal.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows cmd required')
    def test_cmd_entry_selects_vim_mode(self):
        launcher = Path(__file__).with_name('ipdbVim.cmd').resolve()
        result = subprocess.run([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(launcher), 'sample.py'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
            env=dict(os.environ, IPDB_PYTHON=sys.executable), timeout=20)
        self.assertIn('requires an interactive Windows console', result.stdout)
        self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows path required')
    def test_source_bridge_uses_ex_path_separators(self):
        from source_vim import VimCommand
        command = VimCommand('vim.exe', Path(r'C:\Program Files\ipdb\source_vim.vim'))
        self.assertEqual(command[-2:], ['-S', 'C:/Program Files/ipdb/source_vim.vim'])

    @unittest.skipUnless(sys.platform == 'win32' and os.environ.get('IPDB_TEST_WINDOWS_CONSOLE') == '1', 'Opt-in native console test')
    def test_real_windows_console_and_debugger(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'sample file.py'
            script.write_text('value = 41\nprint("RESULT", value + 1)\n')
            code = "from unittest import mock; import windows_vim, sys;\nwith mock.patch('sys.stdin.isatty', return_value=True), mock.patch('sys.stdout.isatty', return_value=True):\n sys.exit(windows_vim.Run(sys.argv[1:]))"
            result = subprocess.run([sys.executable, '-B', '-c', code, str(script)],
                input='n\np value\nc\nq\n\n', stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, timeout=40, cwd=str(Path(__file__).resolve().parent),
                env=dict(os.environ, IPY_TEST_SIMPLE_PROMPT='1', VIMINIT='set nocompatible', HOME=directory, USERPROFILE=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('RESULT 42', result.stdout)


if __name__ == '__main__':
    unittest.main()
