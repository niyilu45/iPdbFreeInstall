"""Offline loader regression tests, including Python 3.9 patch versions."""
from pathlib import Path
import tempfile
import unittest
import zipfile
import sys
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

    def MakeArchive(self, base, bad=False):
        (base / 'dist').mkdir()
        prefix = 'ipdb-linux-py39/vendor/linux-py39/'
        with zipfile.ZipFile(str(base / 'dist/ipdb-linux-py39.zip'), 'w') as archive:
            archive.writestr(prefix + 'ipdb/__init__.py', '# sample')
            archive.writestr(prefix + 'IPython/__init__.py', '# sample')
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
