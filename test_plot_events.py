"""Matplotlib main-thread event and nonblocking show regression tests."""
import importlib.util
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock
from plot_events import PatchPyplot, GuiPrompt


class PlotTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('matplotlib'), 'Matplotlib required')
    def test_agg_save_and_restart_close_old_figures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'plot.py'
            script.write_text('import matplotlib\nmatplotlib.use("Agg")\nimport matplotlib.pyplot as plt\nplt.plot([0, 1])\nplt.savefig({!r})\npaused = 1\n'.format(str(root / 'plot.png')))
            commands = ['b 6', 'c', 'r', '!print("OLD_FIGURES", __import__("matplotlib.pyplot", fromlist=["get_fignums"]).get_fignums())', 'q', '']
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('portable_ipdb.py')), '--plain', str(script)],
                input='\n'.join(commands), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                timeout=30, env=dict(os.environ, IPYTHONDIR=directory, MPLCONFIGDIR=directory, IPY_TEST_SIMPLE_PROMPT='1'))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('OLD_FIGURES []', result.stdout)
            self.assertTrue((root / 'plot.png').exists())

    @unittest.skipUnless(sys.platform == 'win32' and importlib.util.find_spec('matplotlib'), 'Windows Matplotlib required')
    def test_windows_tk_events_at_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script, marker, ready = root / 'plot.py', root / 'events', root / 'ready'
            lines = ['import matplotlib', 'matplotlib.use("TkAgg")', 'import matplotlib.pyplot as plt',
                     'import threading', 'fig, ax = plt.subplots()', 'ax.plot([0, 1], [1, 0])',
                     'def Tick():',
                     '    with open({!r}, "a") as stream: stream.write(str(threading.current_thread() is threading.main_thread()) + "\\n")'.format(str(marker)),
                     '    if __import__("os").path.exists({!r}):'.format(str(root / 'close-request')),
                     '        plt.close(fig)',
                     '        open({!r}, "w").write("closed")'.format(str(root / 'closed')),
                     'timer = fig.canvas.new_timer(interval=100)', 'timer.add_callback(Tick)', 'timer.start()',
                     'plt.show()', 'open({!r}, "w").write("ready")'.format(str(ready)),
                     'paused = 1', 'plt.close("all")', 'print("PLOT_DONE")']
            script.write_text('\n'.join(lines) + '\n')
            log = (root / 'output.log').open('w')
            process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).with_name('portable_ipdb.py')), '--plain', str(script)],
                stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, universal_newlines=True,
                env=dict(os.environ, IPYTHONDIR=directory, IPY_TEST_SIMPLE_PROMPT='1'))
            try:
                process.stdin.write('b {}\nc\n'.format(lines.index('paused = 1') + 1)); process.stdin.flush()
                deadline = time.monotonic() + 20
                while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
                    time.sleep(0.05)
                self.assertTrue(ready.exists(), (root / 'output.log').read_text(errors='replace'))
                count = len(marker.read_text().splitlines()) if marker.exists() else 0
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if marker.exists() and len(marker.read_text().splitlines()) >= count + 3:
                        break
                    time.sleep(0.05)
                events = marker.read_text().splitlines() if marker.exists() else []
                self.assertGreaterEqual(len(events), count + 3)
                self.assertEqual(set(events), {'True'})
                (root / 'close-request').write_text('close')
                deadline = time.monotonic() + 5
                while not (root / 'closed').exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue((root / 'closed').exists(), 'GUI close did not run while paused')
                process.communicate('c\nq\n', timeout=15)
                output = (root / 'output.log').read_text(errors='replace')
                self.assertEqual(process.returncode, 0, output)
                self.assertIn('PLOT_DONE', output)
            finally:
                if process.poll() is None:
                    process.terminate(); process.wait(timeout=10)
                process.stdin.close(); log.close()

    def test_show_preserves_explicit_block(self):
        original = mock.Mock()
        module = types.SimpleNamespace(show=original)
        PatchPyplot(module)
        module.show()
        original.assert_called_with(block=False)
        module.show(block=True)
        original.assert_called_with(block=True)

    def test_prompt_pumps_on_calling_thread(self):
        import threading
        ids = []
        def Prompt():
            time.sleep(0.1)
            return 'next'
        with mock.patch('plot_events.Pump', side_effect=lambda: ids.append(threading.get_ident())):
            self.assertEqual(GuiPrompt(Prompt), 'next')
        self.assertTrue(ids)
        self.assertEqual(set(ids), {threading.get_ident()})

    @unittest.skipUnless(sys.platform.startswith('linux') and os.environ.get('DISPLAY') and importlib.util.find_spec('matplotlib'), 'Linux Matplotlib desktop required')
    def test_real_gui_events_at_readline_prompt(self):
        import pty
        import select
        for backend in ('TkAgg', 'Qt5Agg'):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / 'events'
                script = root / 'plot.py'
                lines = [
                    'import matplotlib', 'matplotlib.use({!r})'.format(backend),
                    'import matplotlib.pyplot as plt', 'import threading',
                    'fig, ax = plt.subplots()', 'ax.plot([0, 1], [0, 1])',
                    'def Tick():',
                    '    with open({!r}, "a") as stream: stream.write(str(threading.current_thread() is threading.main_thread()) + "\\n")'.format(str(marker)),
                    '    if __import__("os").path.exists({!r}):'.format(str(root / 'close-request')),
                    '        plt.close(fig)',
                    '        open({!r}, "w").write("closed")'.format(str(root / 'closed')),
                    'timer = fig.canvas.new_timer(interval=100)', 'timer.add_callback(Tick)', 'timer.start()',
                    'plt.show()', 'paused = 1', 'plt.close("all")', 'print("PLOT_DONE")']
                script.write_text('\n'.join(lines) + '\n')
                master, slave = pty.openpty()
                process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).with_name('portable_ipdb.py')), '--plain', str(script)],
                    stdin=slave, stdout=slave, stderr=slave,
                    env=dict(os.environ, TERM='xterm', IPYTHONDIR=directory))
                output = bytearray()
                def Wait(needle):
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        if select.select([master], [], [], 0.05)[0]:
                            try:
                                output.extend(os.read(master, 65536))
                            except OSError:
                                break
                            if needle in re.sub(br'\x1b\[[0-?]*[ -/]*[@-~]', b'', bytes(output)):
                                return
                        if process.poll() is not None:
                            break
                    self.fail(bytes(output[-3000:]))
                try:
                    Wait(b'ipdb>')
                    output.clear()
                    os.write(master, ('b {}\n'.format(lines.index('paused = 1') + 1)).encode())
                    Wait(b'Breakpoint')
                    output.clear()
                    os.write(master, b'c\n')
                    Wait(b'paused = 1')
                    count = len(marker.read_text().splitlines()) if marker.exists() else 0
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if marker.exists() and len(marker.read_text().splitlines()) >= count + 3:
                            break
                        time.sleep(0.05)
                    events = marker.read_text().splitlines() if marker.exists() else []
                    self.assertGreaterEqual(len(events), count + 3, 'GUI timer stalled at the input prompt')
                    self.assertEqual(set(events), {'True'}, 'GUI events must run on the main thread')
                    (root / 'close-request').write_text('close')
                    deadline = time.monotonic() + 5
                    while not (root / 'closed').exists() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue((root / 'closed').exists(), 'GUI close did not run while paused')
                    output.clear()
                    os.write(master, b'c\n')
                    Wait(b'PLOT_DONE')
                    os.write(master, b'q\n')
                    process.wait(timeout=10)
                    self.assertEqual(process.returncode, 0)
                finally:
                    if process.poll() is None:
                        process.terminate(); process.wait(timeout=10)
                    os.close(master); os.close(slave)


if __name__ == '__main__':
    unittest.main()
