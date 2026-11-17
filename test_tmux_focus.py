"""Exercise complete and interrupted Ctrl-W chords through a real client."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('vim') and shutil.which('tmux'), 'Linux Vim/tmux required')
class FocusTests(unittest.TestCase):
    def test_first_chord_switches_focus_and_resets_table(self):
        import fcntl
        import pty
        import struct
        import termios
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'sample.py'
            script.write_text('value = 1\nprint(value)\n')
            owner = subprocess.Popen(['vim', '-N', '-u', 'NONE', '-i', 'NONE', '-es',
                str(script), '-c', 'preserve', '-c', 'sleep 60'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.2)
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
            process = subprocess.Popen(['sh', str(Path(__file__).with_name('ipdbVim.sh')), str(script)],
                stdin=slave, stdout=slave, stderr=slave,
                env=dict(os.environ, TERM='xterm-256color', TMPDIR=directory, HOME=directory,
                         IPYTHONDIR=directory, IPDB_VIM_CLEAN='1', IPDB_PYTHON=sys.executable))
            os.close(slave)
            output = bytearray()
            def Drain():
                while True:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
            threading.Thread(target=Drain, daemon=True).start()
            command = None
            def Wait(predicate, label, timeout=10):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if predicate():
                        return
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                self.fail(label + ': ' + repr(bytes(output[-1200:])))
            def Call(*args):
                return subprocess.check_output(command + list(args), stderr=subprocess.PIPE, text=True).strip()
            def Active():
                return Call('display-message', '-p', '-t', 'debug:0', '#{pane_index}')
            def Status():
                return Call('display-message', '-p', '-t', 'debug:0', '#{E:window-status-current-format}')
            try:
                Wait(lambda: b'ipdb>' in output, 'initial prompt')
                config = json.loads(next(root.glob('ipdb-vim-*/config.json')).read_text())
                command = config['tmux']
                os.write(master, b'\x17k')
                Wait(lambda: Active() == '0', 'source with existing swap')
                os.write(master, b':e\r')
                time.sleep(0.2)
                self.assertNotIn(b'E325', output)
                os.write(master, b'\x17j!print(731923)\n')
                Wait(lambda: b'731923\r\n' in output, 'input after edit with existing swap', 3)
                for iteration in range(20):
                    os.write(master, b'\x17k')
                    Wait(lambda: Active() == '0', 'first up chord {}'.format(iteration), 1)
                    self.assertIn('VIM', Status())
                    os.write(master, b'\x17j')
                    Wait(lambda: Active() == '1', 'first down chord {}'.format(iteration), 1)
                    self.assertIn('IPDB', Status())
                    self.assertEqual(Call('list-clients', '-F', '#{client_key_table}'), 'root')
                os.write(master, b'\x17\x0b')
                Wait(lambda: Active() == '0', 'Ctrl held while pressing K', 1)
                os.write(master, b'\x17\x0a')
                Wait(lambda: Active() == '1', 'Ctrl held while pressing J', 1)
                # Native Vim splits must take precedence over the tmux boundary.
                os.write(master, b'\x17k:split\r')
                time.sleep(0.2)
                os.write(master, b'\x17\x0a')
                time.sleep(0.1)
                self.assertEqual(Active(), '0')
                os.write(master, b'\x17\x0a')
                Wait(lambda: Active() == '1', 'split edge should cross to debugger', 1)
                os.write(master, b'\x17k:only\r')
                time.sleep(0.1)
                os.write(master, b'\x17j')
                Wait(lambda: Active() == '1', 'return after closing split', 1)
                # A backend source update must not consume a half-entered chord.
                os.write(master, b'\x17k')
                Wait(lambda: Active() == '0', 'source focus')
                os.write(master, b'\x17')
                state = Path(config['state'])
                current = json.loads(state.read_text())
                current.update(line=2, event=time.time())
                temp = state.with_suffix('.test-tmp')
                temp.write_text(json.dumps(current)); os.replace(str(temp), str(state))
                time.sleep(0.25)
                os.write(master, b'j')
                Wait(lambda: Active() == '1', 'source update consumed prefix', 1)
                for iteration in range(20):
                    os.write(master, b'\x17k\x17j')
                    time.sleep(0.1)
                    self.assertEqual(Active(), '1', 'Back-to-back chords lost focus at {}'.format(iteration))
                # Ordinary command typing must survive concurrent source polling.
                for iteration in range(12):
                    marker = str(815000 + iteration).encode('ascii')
                    typed = b'!print(' + marker + b')\n'
                    state = Path(config['state'])
                    current = json.loads(state.read_text())
                    current.update(event=time.time(), line=1 + iteration % 2)
                    temp = state.with_suffix('.test-tmp')
                    temp.write_text(json.dumps(current))
                    os.replace(str(temp), str(state))
                    for character in typed:
                        os.write(master, bytes([character]))
                        time.sleep(0.002)
                    Wait(lambda: marker + b'\r\n' in output, 'normal command lost', 3)
                os.write(master, b'\x17k')
                Wait(lambda: Active() == '0', 'source before immediate input')
                os.write(master, b'\x17j!print(731924)\n')
                Wait(lambda: b'731924\r\n' in output, 'immediate command lost', 3)
                os.write(master, b'q\n')
                Wait(lambda: b'Press Enter' in output, 'exit prompt')
                os.write(master, b'\n')
                process.wait(timeout=10)
            finally:
                owner.terminate()
                owner.wait(timeout=10)
                if command:
                    subprocess.run(command + ['kill-server'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if process.poll() is None:
                    process.terminate(); process.wait(timeout=10)
                os.close(master)


if __name__ == '__main__':
    unittest.main()
