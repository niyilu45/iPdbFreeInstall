"""Linux PTY regression for identical plain and split command editing."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


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
