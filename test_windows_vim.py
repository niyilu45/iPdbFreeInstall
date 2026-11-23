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
            with mock.patch('windows_vim.subprocess.call', return_value=7) as call, mock.patch('builtins.input', return_value=''):
                self.assertEqual(windows_vim.Debug(config), 7)
            self.assertEqual(call.call_args[0][0], config['debugger'])
            self.assertEqual((root / 'result').read_text(), '7')
            self.assertTrue((root / 'done').exists())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows cmd required')
    def test_cmd_entry_selects_vim_mode(self):
        launcher = Path(__file__).with_name('ipdbVim.cmd').resolve()
        result = subprocess.run([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(launcher), 'sample.py'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
            env=dict(os.environ, IPDB_PYTHON=sys.executable), timeout=20)
        self.assertIn('requires an interactive Windows console', result.stdout)
        self.assertNotEqual(result.returncode, 0)

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
