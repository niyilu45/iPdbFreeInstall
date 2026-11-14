"""Incremental terminal-key decoding for source navigation and backspace."""
import time
import re


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
        self.prefix = b''

    def Feed(self, data, browse):
        output = bytearray()
        actions = []
        for value in data:
            if browse and not self.pending:
                if self.prefix:
                    prefix, self.prefix = self.prefix, b''
                    if prefix == b'g' and value == ord('g'):
                        actions.append('first')
                        continue
                    if prefix == b'z' and value == ord('z'):
                        actions.append('home')
                        continue
                mapping = {ord('h'): 'left', ord('j'): 'down', ord('k'): 'up', ord('l'): 'right',
                           2: 'page_up', 6: 'page_down', 21: 'half_up', 4: 'half_down',
                           ord('G'): 'last', ord('0'): 'line_start', ord('$'): 'line_end'}
                if value in mapping:
                    actions.append(mapping[value])
                    continue
                if value in (ord('g'), ord('z')):
                    self.prefix = bytes([value])
                    continue
            if not browse:
                self.prefix = b''
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


class PaneKeys:
    """Decode Vim window chords and SGR mouse reports across read boundaries."""
    def __init__(self):
        self.pending = b''
        self.started = 0

    def Flush(self):
        if self.pending == b'\x1b' and time.monotonic() - self.started >= 0.15:
            self.pending = b''
            return b'\x1b'
        return b''

    def Feed(self, data, enabled, source_height):
        events = []
        for value in data:
            if not enabled:
                events.append(('input', self.pending + bytes([value])))
                self.pending = b''
                continue
            if self.pending == b'\x17':
                self.pending = b''
                direction = chr(value)
                if direction in 'hjkl':
                    events.append(('focus', direction))
                continue
            if self.pending:
                self.pending += bytes([value])
                if b'\x1b[<'.startswith(self.pending):
                    continue
                if self.pending.startswith(b'\x1b[<'):
                    if value in (ord('M'), ord('m')):
                        match = re.fullmatch(br'\x1b\[<(\d+);(\d+);(\d+)([Mm])', self.pending)
                        if match:
                            button, column, row = map(int, match.groups()[:3])
                            if button == 0 and match.group(4) == b'M':
                                events.append(('click', (column, row)))
                            if 1 <= row <= source_height and button & 64 and match.group(4) == b'M':
                                if button & 3 in (0, 1):
                                    events.append(('scroll', ('down' if button & 1 else 'up', column, row)))
                        self.pending = b''
                    elif len(self.pending) > 64 or value not in b'0123456789;':
                        self.pending = b''
                    continue
                events.append(('input', self.pending))
                self.pending = b''
                continue
            if value in (23, 27):
                self.pending = bytes([value])
                self.started = time.monotonic()
            else:
                events.append(('input', bytes([value])))
        return events


def ConfigureInput():
    import sys
    import termios
    saved = termios.tcgetattr(sys.stdin.fileno())
    attributes = list(saved)
    attributes[6] = list(saved[6])
    attributes[6][termios.VERASE] = b'\x7f'
    attributes[3] |= termios.ECHOE
    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, attributes)
    try:
        import readline
        readline.parse_and_bind('tab: complete')
        readline.parse_and_bind('set show-all-if-ambiguous on')
        readline.parse_and_bind('"\\C-h": backward-delete-char')
        readline.parse_and_bind('"\\C-?": backward-delete-char')
        readline.parse_and_bind('"\\C-p": previous-history')
        readline.parse_and_bind('"\\C-n": next-history')
        readline.parse_and_bind('"\\C-l": redraw-current-line')
    except ImportError:
        pass
    # This module can have been imported before the child environment changed.
    import IPython.terminal.debugger
    IPython.terminal.debugger._use_simple_prompt = True
    # IPython 7.16 has no _use_simple_prompt branch. Select its readline-
    # based core debugger explicitly, preserving ipdb's shell initialization.
    from IPython.core.debugger import Pdb
    from ipdb import __main__ as ipdb_main
    original = ipdb_main._get_debugger_cls

    def SimpleDebuggerClass():
        original()
        return Pdb

    ipdb_main._get_debugger_cls = SimpleDebuggerClass
    return saved


def RestoreInput(saved):
    import sys
    import termios
    termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
