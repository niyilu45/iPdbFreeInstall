"""Offline loader regression tests, including Python 3.9 patch versions."""
from pathlib import Path
import tempfile
import unittest
import zipfile
import sys
import os
import subprocess
from unittest import mock
from bundle_loader import Locate


class LoaderTests(unittest.TestCase):
    def test_search_path_survives_ipdb_script_selection(self):
        import portable_ipdb
        import importlib.util
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'portable_loader_probe.py').write_text('value = 42')
            with mock.patch('bundle_loader.Locate', return_value=Path(directory)), mock.patch.object(sys, 'path', list(sys.path)):
                portable_ipdb.Activate()
                sys.path[0] = '/debuggee/directory'
                spec = importlib.util.find_spec('portable_loader_probe')
                self.assertIsNotNone(spec)
                self.assertEqual(Path(spec.origin).parent, Path(directory))

    def test_launcher_modules_survive_debuggee_path_replacement(self):
        import portable_ipdb
        from importlib.machinery import PathFinder
        base = Path(portable_ipdb.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch('bundle_loader.Locate', return_value=Path(directory)), mock.patch.object(sys, 'path', [str(base)]):
                portable_ipdb.Activate()
                portable_ipdb.Activate()
                self.assertEqual(sys.path[1:].count(str(base)), 1)
                sys.path[0] = '/debuggee/directory'
                for module in ('tmux_resize', 'reload_code', 'windows_vim', 'source_vim', 'source_panes',
                               'source_browser', 'source_theme', 'completion', 'checkpoints', 'terminal_keys',
                               'output_history', 'replay_checkpoints', 'replay_debugger_state', 'replay_session',
                               'replay_state'):
                    spec = PathFinder.find_spec(module, sys.path)
                    self.assertIsNotNone(spec, module)
                    self.assertEqual(Path(spec.origin).parent, base)

    def test_plain_startup_outside_launcher_directory(self):
        launcher = Path(__file__).resolve().parent / 'portable_ipdb.py'
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'sample.py'
            script.write_text('value = 41\nprint("RESULT", value + 1)\n')
            result = subprocess.run(
                [sys.executable, '-B', str(launcher), '--plain', str(script)],
                input="n\np value\np __import__('source_theme').__name__\nc\nq\n",
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, cwd=directory,
                env=dict(os.environ, IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory), timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("'source_theme'", result.stdout)
            self.assertIn('RESULT 42', result.stdout)
            self.assertNotIn('ModuleNotFoundError', result.stdout)

    def test_utf8_config_allows_plain_startup_in_legacy_locale(self):
        launcher = Path(__file__).resolve().parent / 'portable_ipdb.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'sample.py'
            script.write_text('print("CONFIG_STARTUP_OK")\n')
            (root / 'setup.cfg').write_text('# \u5145\n[ipdb]\ncontext = 3\n', encoding='utf-8-sig')
            (root / '.ipdb').write_text('# \u5145\ncontext = 4\n', encoding='utf-8')
            (root / 'pyproject.toml').write_text('# \u5145\n[tool.ipdb]\ncontext = 5\n', encoding='utf-8')
            env = dict(os.environ, HOME=directory, USERPROFILE=directory, IPYTHONDIR=directory,
                       IPY_TEST_SIMPLE_PROMPT='1', PYTHONUTF8='0', PYTHONCOERCECLOCALE='0', LC_ALL='C')
            for key in ('IPDB_CONFIG', 'IPDB_CONTEXT_SIZE'):
                env.pop(key, None)
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(script)],
                                    input=b'c\nq\n', stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    cwd=directory, env=env, timeout=30)
            output = result.stdout.decode('ascii', 'backslashreplace')
            self.assertEqual(result.returncode, 0, output)
            self.assertIn('CONFIG_STARTUP_OK\nThe program finished', output.replace('\r\n', '\n'))

    def test_ipdb_config_falls_back_to_legacy_windows_encoding(self):
        from portable_ipdb import ipdb_main
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory, 'legacy.ipdb')
            settings.write_text('context = 6\nname = \u5145\n', encoding='gbk')
            with mock.patch.dict(os.environ, {'IPDB_CONFIG': str(settings), 'HOME': directory}), \
                    mock.patch('locale.getpreferredencoding', return_value='cp936'):
                parser = ipdb_main.get_config()
            self.assertEqual(parser.getint('ipdb', 'context'), 6)
            self.assertEqual(parser.get('ipdb', 'name'), '\u5145')

    def MakeArchive(self, base, bad=False):
        (base / 'dist').mkdir()
        prefix = 'ipdb-linux-py39/vendor/linux-py39/'
        with zipfile.ZipFile(str(base / 'dist/ipdb-linux-py39.zip'), 'w') as archive:
            archive.writestr(prefix + 'ipdb/__init__.py', '# sample')
            archive.writestr(prefix + 'IPython/__init__.py', '# sample')
            archive.writestr(prefix + 'pyte/__init__.py', '# sample')
            archive.writestr(prefix + 'wcwidth/__init__.py', '# sample')
            if bad:
                archive.writestr(prefix + '../escape', 'unsafe')

    def test_checkout_extracts_and_reuses_py39(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self.MakeArchive(base)
            version = (3, 9, 12)
            result = Locate(base, 'linux', version[:2])
            self.assertEqual(result, base / 'vendor/linux-py39')
            self.assertTrue((result / 'ipdb/__init__.py').is_file())
            self.assertEqual(Locate(base, 'linux', version[:2]), result)

    def test_old_vendor_gets_missing_dependency_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self.MakeArchive(base)
            target = base / 'vendor/linux-py39'
            (target / 'ipdb').mkdir(parents=True)
            (target / 'ipdb/__init__.py').write_text('# existing')
            self.assertEqual(Locate(base, 'linux', (3, 9)), target)
            self.assertTrue((target / 'pyte/__init__.py').is_file())
            self.assertEqual((target / 'ipdb/__init__.py').read_text(), '# existing')

    def test_incomplete_bundle_without_archive_has_recovery_message(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'vendor/linux-py39/ipdb').mkdir(parents=True)
            (base / 'vendor/linux-py39/ipdb/__init__.py').write_text('# existing')
            with self.assertRaisesRegex(RuntimeError, 'pyte.*linux-py39'):
                Locate(base, 'linux', (3, 9))

    def test_wrong_python_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self.MakeArchive(base)
            Locate(base, 'linux', (3, 9))
            with self.assertRaisesRegex(RuntimeError, 'linux-py310.*linux-py39'):
                Locate(base, 'linux', (3, 10))

    def test_unsafe_archive_does_not_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self.MakeArchive(base, bad=True)
            with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
                Locate(base, 'linux', (3, 9))
            self.assertFalse((base / 'vendor/linux-py39').exists())


if __name__ == '__main__':
    unittest.main()
