"""Linux PTY regression for command editing and live interrupts."""
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
    def test_ctrl_c_pauses_live_loop(self):
        import fcntl
        import pty
        import select
        import signal
        import struct
        import termios

        launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
        for mode in ('--plain', '--tui'):
            for resume, timed, queued in ((b'c', False, False), (b'n', False, False),
                                          (b'c', True, False), (b'c', False, True)):
                with self.subTest(mode=mode, resume=resume, timed=timed, queued=queued), tempfile.TemporaryDirectory() as directory:
                    script = Path(directory) / 'loop.py'
                    source = (
                        'def Inner():\n'
                        '    gate = [True]\n'
                        '    token = 1439811\n'
                        "    print('LOOP' + '_READY', flush=True)\n"
                        '    count = 0\n'
                        '    while gate[0]:\n'
                        '        count += 1\n'
                        '    return token\n'
                        'def Outer():\n'
                        '    return Inner()\n'
                        'result = Outer()\n'
                        "print('DONE' + '_VALUE', result, flush=True)\n")
                    if timed:
                        source = source.replace('token = 1439811', 'token = BeforeLoop()')
                        source = source.replace('result = Outer()', 'def BeforeLoop():\n    return 1439811\nresult = Outer()')
                    script.write_text(source)
                    environment = dict(os.environ, TERM='xterm-256color', IPYTHONDIR=directory)
                    pid, master = pty.fork()
                    if pid == 0:
                        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack('HHHH', 32, 120, 0, 0))
                        os.chdir(directory)
                        os.execve(sys.executable, [sys.executable, '-S', str(launcher), mode, str(script)], environment)

                    def ReadUntil(marker, prompt_after=False):
                        output = bytearray()
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline:
                            if select.select([master], [], [], 0.1)[0]:
                                try:
                                    chunk = os.read(master, 65536)
                                except OSError:
                                    break
                                if not chunk:
                                    break
                                output.extend(chunk)
                                position = output.find(marker)
                                if position >= 0 and (not prompt_after or b'ipdb> ' in output[position + len(marker):]):
                                    return bytes(output)
                        self.fail('Missing {!r}: {!r}'.format(marker, bytes(output[-2000:])))

                    def Command(command):
                        os.write(master, command + b'\n')
                        return ReadUntil(b'ipdb> ')

                    try:
                        ReadUntil(b'ipdb> ')
                        if timed:
                            Command(b'rt')
                        else:
                            Command(b'n')
                            Command(b'n')
                        if queued:
                            # Set this on the debugger's actual PTY, including inside the split view.
                            os.write(master, b'!import termios; flags = termios.tcgetattr(0); '
                                             b'flags[3] |= termios.NOFLSH; termios.tcsetattr(0, termios.TCSANOW, flags); '
                                             b'print("KEEP_PENDING", bool(termios.tcgetattr(0)[3] & termios.NOFLSH))\n')
                            ReadUntil(b'\nKEEP_PENDING True', prompt_after=True)
                        os.write(master, resume + b'\n')
                        ReadUntil(b'LOOP_READY')
                        os.write(master, (b'\n' * 32 if queued else b'') + b'\x03')
                        ReadUntil(b'ipdb> ')
                        if queued:
                            # Uncanceled blank lines would continue the infinite loop before this query.
                            os.write(master, b'p token\n')
                            ReadUntil(b'\n1439811\r\n', prompt_after=True)
                        if timed:
                            cache = Path(directory) / 'ipdCache'
                            reports = list(cache.glob('callgrind.out.*'))
                            self.assertEqual(len(reports), 1, reports)
                            report_path = reports[0]
                            paused_report = report_path.read_text(encoding='utf-8')
                            self.assertRegex(paused_report, r'(?m)^events:\s+Time\s*$')
                            self.assertRegex(paused_report, r'(?m)^fn=BeforeLoop:\d+$')
                        backtrace = Command(b'bt')
                        self.assertRegex(backtrace, br'\* #0 Inner at [^\r\n]+loop\.py:[67]\b')
                        self.assertRegex(backtrace, br'#1 Outer at [^\r\n]+loop\.py:10\b')
                        self.assertRegex(Command(b'p token'), br'\r?\n1439811\r?\n')
                        Command(b'n')
                        self.assertRegex(Command(b'bt'), br'\* #0 Inner at [^\r\n]+loop\.py:[67]\b')
                        Command(b'!gate[0] = False')
                        os.write(master, b'c\n')
                        done = ReadUntil(b'ipdb> ')
                        self.assertIn(b'DONE_VALUE 1439811', done)
                        if timed:
                            self.assertEqual(list(cache.glob('callgrind.out.*')), [report_path])
                            completed_report = report_path.read_text(encoding='utf-8')
                            self.assertNotEqual(completed_report, paused_report)
                            self.assertRegex(completed_report, r'(?m)^fn=Inner:\d+$')
                        os.write(master, b'q\n')
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            child, status = os.waitpid(pid, os.WNOHANG)
                            if child:
                                pid = None
                                self.assertTrue(os.WIFEXITED(status), status)
                                self.assertEqual(os.WEXITSTATUS(status), 0)
                                break
                            time.sleep(0.01)
                        self.assertIsNone(pid, 'Debugger did not exit after quit')
                    finally:
                        if pid is not None:
                            try:
                                os.killpg(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            os.waitpid(pid, 0)
                        os.close(master)

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
