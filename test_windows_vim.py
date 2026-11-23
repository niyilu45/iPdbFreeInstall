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
            with self.assertRaisesRegex(RuntimeError, 'exited before initializing'):
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

    def test_conemu_launcher_keeps_debugger_in_current_pane(self):
        def OpenPane(config_path):
            config = json.loads(config_path.read_text())
            Path(config['vim_result']).write_text('0')

        with mock.patch.dict(os.environ, {'ConEmuPID': '123', 'IPDB_CONEMU_TTY': '1'}), \
                mock.patch('sys.stdin.isatty', return_value=False), mock.patch('sys.stdout.isatty', return_value=False), \
                mock.patch('windows_vim.shutil.which', return_value='vim.exe') as which, \
                mock.patch('windows_vim.subprocess.check_output', return_value=b'+timers +eval +signs'), \
                mock.patch('windows_vim.OpenConEmuVim', side_effect=OpenPane) as pane, \
                mock.patch('windows_vim.Debug', return_value=7) as debug, \
                mock.patch('windows_vim.subprocess.Popen') as spawn, mock.patch('windows_vim.subprocess.check_call') as terminal:
            self.assertEqual(windows_vim.Run(['sample.py', 'a&b']), 7)
            self.assertNotIn('IPDB_CONEMU_TTY', os.environ)
            which.assert_called_once_with('vim')
            pane.assert_called_once()
            self.assertEqual(debug.call_args[0][0]['debugger'][-2:], ['sample.py', 'a&b'])
            spawn.assert_not_called()
            terminal.assert_not_called()

    def test_pipe_requires_conemu_and_an_interactive_shell(self):
        for environment in ({'ConEmuPID': '123'}, {'IPDB_CONEMU_TTY': '1'}):
            with self.subTest(environment=environment), mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch('sys.stdin.isatty', return_value=False), mock.patch('sys.stdout.isatty', return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'requires an interactive Windows console'):
                    windows_vim.Run(['sample.py'])

    def test_conemu_split_failure_uses_plain_debugger(self):
        with mock.patch.dict(os.environ, {'ConEmuPID': '123'}), \
                mock.patch('sys.stdin.isatty', return_value=True), mock.patch('sys.stdout.isatty', return_value=True), \
                mock.patch('windows_vim.shutil.which', return_value='vim.exe'), \
                mock.patch('windows_vim.subprocess.check_output', return_value=b'+timers +eval +signs'), \
                mock.patch('windows_vim.OpenConEmuVim', side_effect=RuntimeError('split failed')), \
                mock.patch('source_vim.WaitForDebugger', return_value=7) as wait, \
                mock.patch('windows_vim.subprocess.Popen') as spawn, mock.patch('windows_vim.subprocess.check_call') as terminal:
            self.assertEqual(windows_vim.Run(['sample.py']), 7)
            self.assertEqual(spawn.call_args[0][0][-2:], ['--plain', 'sample.py'])
            self.assertNotIn('creationflags', spawn.call_args[1])
            wait.assert_called_once_with(spawn.return_value)
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
