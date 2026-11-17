"""Linux PTY regression for identical plain and split command editing."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


class KeyPreservationTests(unittest.TestCase):
    def test_unknown_window_chord_is_forwarded(self):
        from terminal_keys import PaneKeys
        for data in (b'\x17p', b'\x17\t', b'\x17\x7f'):
            keys = PaneKeys()
            events = keys.Feed(data[:1], True, 10) + keys.Feed(data[1:], True, 10)
            self.assertEqual(events, [('input', data)])
        self.assertEqual(PaneKeys().Feed(b'\x17\x17', True, 10), [('input', b'\x17')])

    def test_held_ctrl_and_partial_escape(self):
        from terminal_keys import PaneKeys
        self.assertEqual(PaneKeys().Feed(b'\x17\x0a', True, 10), [('focus', 'j')])
        keys = PaneKeys()
        keys.Feed(b'\x1b[', True, 10)
        keys.started = time.monotonic() - 1
        self.assertEqual(keys.Flush(), b'\x1b[')
        self.assertEqual(keys.Feed(b'p', True, 10), [('input', b'p')])
        self.assertEqual(PaneKeys().Feed(b'\x1b[<1;bad', True, 10),
                         [('input', b'\x1b[<1;b'), ('input', b'a'), ('input', b'd')])


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux PTY required')
class InputParityTests(unittest.TestCase):
    def test_plain_and_split_editing(self):
        import fcntl
        import pty
        import select
        import struct
        import termios
        for mode in ('--plain', '--tui'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                script = Path(directory) / 'sample.py'
                script.write_text('value = 41\nprint(value)\n')
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 120, 0, 0))
                process = subprocess.Popen([sys.executable, '-S', str(Path(__file__).resolve().parent / 'portable_ipdb.py'), mode, str(script)], stdin=slave, stdout=slave, stderr=slave, env=dict(os.environ, TERM='xterm-256color', IPYTHONDIR=directory))

                def ReadPrompt():
                    output = bytearray()
                    deadline = time.time() + 15
                    while time.time() < deadline:
                        if select.select([master], [], [], 0.1)[0]:
                            try:
                                output.extend(os.read(master, 65536))
                            except OSError:
                                break
                            if b'ipdb>' in output:
                                return bytes(output)
                    self.fail('No prompt: {!r}'.format(bytes(output[-2000:])))

                try:
                    ReadPrompt()
                    os.write(master, b'n\n')
                    ReadPrompt()
                    for command in (b'p valuX\x7fe\n', b'p valuX\x08e\n', b'p val\t\n', b'p valXue\x1b[D\x1b[D\x7f\n'):
                        os.write(master, command)
                        output = ReadPrompt()
                        self.assertIn(b'41', output)
                        self.assertNotIn(b'NameError', output)
                    os.write(master, b'\x1b[A\n')
                    self.assertIn(b'41', ReadPrompt())
                    os.write(master, b'q\n')
                    process.wait(timeout=10)
                    self.assertEqual(process.returncode, 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    os.close(slave)
                    os.close(master)


if __name__ == '__main__':
    unittest.main()
