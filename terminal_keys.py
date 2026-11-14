"""Incremental terminal-key decoding for source navigation and backspace."""
import time


class SourceKeys:
    sequences = {
        b'\x1b[A': 'up', b'\x1b[B': 'down', b'\x1b[C': 'right', b'\x1b[D': 'left',
        b'\x1bOA': 'up', b'\x1bOB': 'down', b'\x1bOC': 'right', b'\x1bOD': 'left',
        b'\x1b[5~': 'page_up', b'\x1b[6~': 'page_down',
        b'\x1b[H': 'home', b'\x1bOH': 'home', b'\x1b[1~': 'home',
    }

    def __init__(self):
        self.pending = b''
        self.started = 0

    def Feed(self, data, browse):
        output = bytearray()
        actions = []
        for value in data:
            if not self.pending and value == 27 and browse:
                self.pending = b'\x1b'
                self.started = time.monotonic()
                continue
            if self.pending:
                self.pending += bytes([value])
                if self.pending in self.sequences:
                    actions.append(self.sequences[self.pending])
                    self.pending = b''
                elif not any(sequence.startswith(self.pending) for sequence in self.sequences):
                    output.extend(self.pending)
                    self.pending = b''
                continue
            output.append(127 if value in (8, 127) else value)
        return bytes(output), actions

    def Flush(self):
        if self.pending and time.monotonic() - self.started >= 0.15:
            result, self.pending = self.pending, b''
            return result
        return b''


def ConfigureInput():
    import sys
    import termios
    attributes = termios.tcgetattr(sys.stdin.fileno())
    attributes[6][termios.VERASE] = b'\x7f'
    attributes[3] |= termios.ECHOE
    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, attributes)
    try:
        import readline
        readline.parse_and_bind('"\\C-h": backward-delete-char')
        readline.parse_and_bind('"\\C-?": backward-delete-char')
        readline.parse_and_bind('"\\C-p": previous-history')
        readline.parse_and_bind('"\\C-n": next-history')
    except ImportError:
        pass
    # This module can have been imported before the child environment changed.
    import IPython.terminal.debugger
    IPython.terminal.debugger._use_simple_prompt = True
