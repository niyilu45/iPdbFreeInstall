"""Exercise opt-in checkpoints through a native Windows debugger session."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest


NOTICE = b'Restoring a replay checkpoint reruns the script from the start in a separate process.'


@unittest.skipUnless(sys.platform == 'win32', 'Native Windows Python required')
class ReplayTests(unittest.TestCase):
    @contextmanager
    def Session(self, script):
        launcher = Path(__file__).with_name('portable_ipdb.py')
        env = dict(os.environ, IPY_TEST_SIMPLE_PROMPT='1')
        process = subprocess.Popen([sys.executable, str(launcher), '--plain', '--checkpoint-replay', str(script)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        output = bytearray()

        # readline() cannot see an interactive prompt until its trailing newline.
        def DrainBytes():
            descriptor = process.stdout.fileno()
            try:
                while True:
                    chunk = os.read(descriptor, 8192)
                    if not chunk:
                        return
                    output.extend(chunk)
            except OSError:
                pass

        reader = threading.Thread(target=DrainBytes, daemon=True)
        reader.start()
        start = 0

        def Wait(*markers):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if all(marker in output[start:] for marker in markers):
                    return bytes(output[start:])
                if process.poll() is not None:
                    reader.join(1)
                    break
                time.sleep(0.01)
            self.fail('Missing {!r}: {!r}'.format(markers, bytes(output)))

        def Command(line, *markers):
            nonlocal start
            start = len(output)
            process.stdin.write(line + b'\n')
            process.stdin.flush()
            return Wait(b'ipdb>', *markers)

        try:
            Wait(b'ipdb>')
            yield Command, output
            process.stdin.write(b'q\n')
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=10), 0)
            reader.join(2)
        finally:
            if process.poll() is None:
                process.stdin.close()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            reader.join(2)
            if not process.stdin.closed:
                process.stdin.close()
            process.stdout.close()

    def test_replay_restores_multiple_checkpoints_and_notifies_once_per_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'sample loop.py'
            script.write_text('value = 11\nvalue = 16\nvalue = 23\nprint(value)\n')
            with self.Session(script) as (command, output):
                command(b'n')
                command(b'checkpoint', NOTICE, b'Replay checkpoint 1 created')
                command(b'n')
                self.assertNotIn(NOTICE, command(b'checkpoint', b'Replay checkpoint 2 created'))
                command(b'n')
                for number, value in ((1, b'11'), (2, b'16'), (1, b'11')):
                    command(('restart {}'.format(number)).encode(),
                            ('Restored replay checkpoint {}'.format(number)).encode())
                    command(b'p value', value + b'\r\n')
                command(b'r')
                command(b'n')
                self.assertNotIn(NOTICE, command(b'checkpoint', b'Replay checkpoint 1 created'))
                self.assertEqual(output.count(NOTICE), 1)
            with self.Session(script) as (command, output):
                command(b'n')
                command(b'checkpoint', NOTICE, b'Replay checkpoint 1 created')
                self.assertEqual(output.count(NOTICE), 1)

    def test_edited_source_keeps_the_original_frame_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'sample.py'
            script.write_text('value = 11\nvalue = 16\nprint(value)\n')
            with self.Session(script) as (command, _):
                command(b'n')
                command(b'checkpoint', b'Replay checkpoint 1 created')
                command(b'n')
                script.write_text('value = 91\nvalue = 92\nprint(value)\n')
                command(b'restart 1', b'The saved source changed')
                command(b'p value', b'16\r\n')
                command(b'info checkpoints', b'1  ')

    def test_restores_distinct_iterations_at_the_same_conditional_breakpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'ReplayCase'
            root.mkdir()
            script = root / 'SampleCase.py'
            location = str(Path(root.name) / script.name).encode('ascii') + b':4'
            script.write_text('def collect():\n'
                              '    values = []\n'
                              '    for value in range(4):\n'
                              '        values.append(value)\n'
                              '    return values\n'
                              'result = collect()\n'
                              'print(result)\n')
            with self.Session(script) as (command, _):
                command(b'b 4 if value >= 1', b'Breakpoint 1', location)
                command(b'c', b'value >= 1')
                command(b'up')
                command(b'checkpoint', NOTICE, b'innermost script frame')
                command(b'frame 0')
                self.assertNotIn(NOTICE, command(b'checkpoint', b'Replay checkpoint 1 created'))
                command(b'c', b'value >= 1')
                command(b'checkpoint', b'Replay checkpoint 2 created')
                command(b'restart 1', b'Restored replay checkpoint 1')
                command(b'p values', b'[0]\r\n')
                command(b'restart 2', b'Restored replay checkpoint 2')
                command(b'p values', b'[0, 1]\r\n')
                command(b'info breakpoints', b'value >= 1', location)
                command(b'bt', b'#0 collect at ', location)
