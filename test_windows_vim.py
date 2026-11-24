"""Windows launcher argument routing and lifecycle regression tests."""
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
            pane.write_text(json.dumps({'server': '456', 'draw': '0x25'}), encoding='ascii')
            config = dict(source_pane=str(pane), conemu_helper='ConEmuC64.exe')
            with mock.patch.dict(os.environ, {'ConEmuPID': '123'}), \
                    mock.patch('windows_vim.ConEmuMacro', side_effect=[b'OK', b'OK;OK']) as macro:
                windows_vim.OpenConEmuVim(config, Path(directory, 'session file.json'))
            first, second = macro.call_args_list
            self.assertEqual(first[1]['server'], 123)
            self.assertIn('Shell("new_console:fnt:ipdb"', first[0][0])
            self.assertIn('--vim', first[0][0])
            self.assertEqual(second[1]['server'], 456)
            self.assertIn('Tab(7,-2);Shell("new_console:s50Vfn"', second[0][0])
            self.assertIn('--debug', second[0][0])

    def test_conemu_rejects_inverted_or_hidden_splits(self):
        for bounds, expected in (([(0, 25), (25, 50)], True),
                                 ([(25, 50), (0, 25)], False),
                                 ([(0, 50), (25, 50)], False),
                                 ([None, (0, 50)], False)):
            with self.subTest(bounds=bounds), mock.patch('windows_vim.PaneBounds', side_effect=bounds):
                self.assertEqual(windows_vim.ConEmuSplitVisible(0x25, 0x12), expected)

    def test_conemu_resize_uses_current_pane_and_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {name: str(root / name) for name in ('source_pane', 'debug_pane')}
            (root / 'config').write_text(json.dumps(config))
            (root / 'source_pane').write_text(json.dumps({'server': '123', 'draw': '0x25'}))
            (root / 'debug_pane').write_text(json.dumps({'server': '456', 'draw': '0x12'}))
            for server, action, count, change in (('123', '+', 3, 3), ('456', '+', 5, -5),
                                                   ('123', '-', 3, -3), ('456', '-', 1, 1), ('123', '=', 1, -5)):
                with self.subTest(server=server, action=action), \
                        mock.patch.dict(os.environ, {'IPDB_CONEMU_CONFIG': str(root / 'config'), 'ConEmuServerPID': server}), \
                        mock.patch('windows_vim.PaneBounds', side_effect=[(0, 300), (303, 503)]), \
                        mock.patch('windows_vim.ConsoleRows', return_value=30), \
                        mock.patch('windows_vim.ConEmuMacro') as macro:
                    windows_vim.ResizeConEmu(action, count)
                    step = 'Split(1,0,{})'.format(1 if change > 0 else -1)
                    macro.assert_called_once_with(';'.join([step] * abs(change)))

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
