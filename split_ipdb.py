"""Linux PTY split view for ipdb. No additional packages are required."""
import json
import linecache
import os
import sys
import unicodedata
import re


def ViewStart(snapshot, height):
    maximum = max(0, len(snapshot.get('lines', [])) - height)
    automatic = snapshot.get('line', 0) - snapshot.get('offset', 1) - height // 2
    return min(max(0, snapshot.get('_top', automatic)), maximum)


def ScrollSource(snapshot, action, height, width):
    if action == 'home':
        snapshot.pop('_top', None)
        snapshot['_left'] = 0
        return
    if action in ('first', 'last'):
        snapshot['_top'] = 0 if action == 'first' else max(0, len(snapshot.get('lines', [])) - height)
        return
    if action in ('up', 'down', 'page_up', 'page_down', 'half_up', 'half_down'):
        amount = {'up': -1, 'down': 1, 'page_up': -height, 'page_down': height, 'half_up': -max(1, height // 2), 'half_down': max(1, height // 2)}[action]
        snapshot['_top'] = min(max(0, ViewStart(snapshot, height) + amount), max(0, len(snapshot.get('lines', [])) - height))
    else:
        maximum = snapshot.get('_max_width')
        if maximum is None:
            maximum = max([sum(0 if unicodedata.combining(c) else (2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1) for c in line.expandtabs(4).rstrip('\n')) for line in snapshot.get('lines', [])] or [0])
            snapshot['_max_width'] = maximum
        limit = max(0, maximum - max(1, width - 11))
        if action in ('line_start', 'line_end'):
            snapshot['_left'] = 0 if action == 'line_start' else limit
            return
        snapshot['_left'] = min(max(0, snapshot.get('_left', 0) + (8 if action == 'right' else -8)), limit)


def FitCells(text, width, fill=' '):
    result = []
    used = 0
    for character in text:
        if not character.isprintable():
            character = ' '
        size = 0 if unicodedata.combining(character) else (2 if unicodedata.east_asian_width(character) in ('W', 'F') else 1)
        if used + size > width:
            break
        result.append(character)
        used += size
    return ''.join(result) + fill * max(0, width - used)


def SourceBox(snapshot, height, width, color=False):
    if height < 3 or width < 4:
        return []
    inside = width - 3
    title = ' Source: {}:{} '.format(snapshot.get('file', 'Waiting for debugger'), snapshot.get('line', ''))
    rows = ['+' + FitCells(title, inside, '-') + '+']
    content = SourceRows(snapshot, height - 2, inside + 1)
    for index in range(height - 2):
        rows.append('|' + FitCells(content[index] if index < len(content) else '', inside) + '|')
    focus = 'SOURCE' if snapshot.get('_source_focus', False) else 'COMMAND'
    footer = ' Focus: {} | Ctrl-W hjkl: focus | Ctrl-X A: split | Ctrl-X S: PuDB '.format(focus)
    if snapshot.get('_source_focus'):
        footer = ' Focus: SOURCE {}:{} | {} '.format(snapshot.get('_cursor_row', 0) + snapshot.get('offset', 1), snapshot.get('_cursor_col', 0) + 1, snapshot.get('_status') or '/ find | m mark | apostrophe jump')
    rows.append('+' + FitCells(footer, inside, '-') + '+')
    if color:
        from source_theme import PaintLine, TokenRows, RESET
        lines = snapshot.get('lines', [])
        syntax = snapshot.get('syntax')
        if syntax is None:
            syntax = TokenRows(lines)
        current = snapshot.get('line', 0)
        offset = snapshot.get('offset', 1)
        start = ViewStart(snapshot, height - 2)
        border = '\x1b[48;5;0;38;5;244m'
        rows[0] = border + rows[0] + RESET
        rows[-1] = border + rows[-1] + RESET
        for index in range(height - 2):
            source_index = start + index
            if source_index < len(lines):
                body = PaintLine(syntax[source_index] if source_index < len(syntax) else [], offset + source_index, current, inside, snapshot.get('_left', 0), snapshot.get('_cursor_cell', 0) if snapshot.get('_source_focus') and source_index == snapshot.get('_cursor_row') else None)
            else:
                body = '\x1b[48;5;0m' + ' ' * inside + RESET
            rows[index + 1] = border + '|' + RESET + body + border + '|' + RESET
    return rows


class KeyFilter:
    def __init__(self):
        self.pending = False
        self.focus_toggles = 0

    def Feed(self, data):
        self.focus_toggles = 0
        output = bytearray()
        toggles = 0
        switches = 0
        for value in data:
            if self.pending:
                self.pending = False
                if value in (ord('a'), ord('A'), 1):
                    toggles += 1
                    continue
                if value in (ord('s'), ord('S'), 19):
                    switches += 1
                    continue
                if value in (ord('o'), ord('O'), 15):
                    self.focus_toggles += 1
                    continue
                output.append(24)
            if value == 24:
                self.pending = True
            else:
                output.append(value)
        return bytes(output), toggles, switches


def SourceRows(snapshot, height, width):
    lines = snapshot.get('lines', [])
    current = snapshot.get('line', 0)
    offset = snapshot.get('offset', 1)
    start = ViewStart(snapshot, height)
    rows = []
    for index in range(start, min(start + height, len(lines))):
        number = offset + index
        from source_theme import PaintLine
        text = PaintLine([(lines[index].rstrip('\n'), 252, False)], number, current, max(0, width - 1), snapshot.get('_left', 0))
        rows.append(re.sub(r'\x1b\[[0-9;]*m', '', text))
    return rows


def InstallHook(event_fd, ack_fd=None, split_state=None):
    from IPython.core.debugger import Pdb
    original = Pdb.print_stack_entry
    original_interaction = Pdb.interaction
    original_cmdloop = Pdb._cmdloop
    original_where = Pdb.do_where

    def Where(self, argument):
        self._portable_force_stack_output = True
        try:
            return original_where(self, argument)
        finally:
            self._portable_force_stack_output = False

    def Send(message):
        packet = (json.dumps(message) + '\n').encode('ascii')
        while packet:
            packet = packet[os.write(event_fd, packet):]

    def ShowPudb(self, frame, traceback=None):
        from pudb import _get_debugger
        os.environ['TERM'] = os.environ.get('IPDB_TUI_TERM', 'xterm')
        Send({'mode': 'pudb'})
        if ack_fd is not None and os.read(ack_fd, 1) != b'1':
            raise RuntimeError('Terminal handoff failed')
        debugger = _get_debugger()
        debugger.reset()
        debugger.botframe = self.botframe
        debugger.bottom_frame = self.botframe
        debugger.breaks = self.breaks
        debugger.interaction(frame, traceback)
        self.breaks = debugger.breaks
        if debugger.quitting:
            self.set_quit()
        else:
            self._set_stopinfo(debugger.stopframe, debugger.returnframe, debugger.stoplineno)
            current = frame
            while current:
                current.f_trace = self.trace_dispatch
                current = current.f_back
            sys.settrace(self.trace_dispatch)
        return True

    def DoPudb(self, argument):
        """Continue this debugging session using the PuDB interface."""
        from checkpoints import Manager
        if Manager.entries:
            self.error('Delete live checkpoints before switching to PuDB')
            return
        self._portable_pudb = True
        return ShowPudb(self, self.curframe)

    def Interaction(self, frame, traceback):
        if getattr(self, '_portable_pudb', False):
            return ShowPudb(self, frame, traceback)
        return original_interaction(self, frame, traceback)

    def CommandLoop(self):
        Send({'ready': True})
        try:
            return original_cmdloop(self)
        finally:
            Send({'ready': False})

    def PrintStackEntry(self, frame_lineno, *args, **kwargs):
        frame, number = frame_lineno
        filename = frame.f_code.co_filename
        lines = linecache.getlines(filename, frame.f_globals)
        offset = 0
        from source_theme import TokenRows
        message = {'file': filename, 'line': number, 'offset': offset + 1,
                   'lines': lines,
                   'syntax': TokenRows(lines)}
        try:
            packet = (json.dumps(message, ensure_ascii=True) + '\n').encode('ascii')
            while packet:
                packet = packet[os.write(event_fd, packet):]
        except OSError:
            pass
        if split_state is not None and split_state[0] and not getattr(self, '_portable_force_stack_output', False):
            show = getattr(self, '_portable_show_displays', None)
            if show is not None:
                show(frame)
            return
        return original(self, frame_lineno, *args, **kwargs)

    Pdb.print_stack_entry = PrintStackEntry
    Pdb.do_pudb = DoPudb
    Pdb.interaction = Interaction
    Pdb._cmdloop = CommandLoop
    Pdb.do_where = Where
    Pdb.do_w = Where


def Run(arguments, start_split=True):
    if not sys.platform.startswith('linux'):
        raise RuntimeError('Split view requires Linux; use --plain on other systems')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError('Split view requires an interactive terminal; use --plain for pipes')
    if os.environ.get('TERM', '') in ('', 'dumb'):
        raise RuntimeError('Split view requires an ANSI terminal (for example xterm)')
    import errno
    import fcntl
    import pty
    import select
    import signal
    import struct
    import termios
    import tty
    import mmap

    split_state = mmap.mmap(-1, 1)
    initial_size = os.get_terminal_size(sys.stdout.fileno())
    split_state[0] = int(start_split and initial_size.lines >= 10 and initial_size.columns >= 20)
    read_fd, write_fd = os.pipe()
    ack_read, ack_write = os.pipe()
    saved = termios.tcgetattr(sys.stdin.fileno())
    pid, master = pty.fork()
    if pid == 0:
        os.close(read_fd)
        os.close(ack_write)
        os.environ['IPDB_TUI_TERM'] = os.environ.get('TERM', 'xterm')
        os.environ['IPY_TEST_SIMPLE_PROMPT'] = '1'
        try:
            from terminal_keys import ConfigureInput
            ConfigureInput()
            InstallHook(write_fd, ack_read, split_state)
            sys.argv = [sys.argv[0]] + arguments
            from ipdb.__main__ import main
            from checkpoints import Supervise
            code = Supervise(main)
        finally:
            os.close(write_fd)
            os.close(ack_read)
        os._exit(code or 0)
    os.close(write_fd)
    os.close(ack_read)
    enabled = start_split
    pudb_active = False
    paused = False
    source_focused = False
    snapshot = {}
    from source_panes import SourcePanes
    panes = SourcePanes()
    snapshot = panes.views[0]
    browser = panes.browsers[0]
    event_buffer = b''
    keys = KeyFilter()
    from terminal_keys import SourceKeys, PaneKeys
    navigation = SourceKeys()
    pane_keys = PaneKeys()
    dimensions = None
    source_height = 0
    status = None
    from output_history import OutputHistory
    output_history = OutputHistory(initial_size.columns, initial_size.lines - (initial_size.lines // 2 if split_state[0] else 0))

    def Write(data):
        if isinstance(data, str):
            data = data.encode('utf-8', 'replace')
        while data:
            data = data[os.write(sys.stdout.fileno(), data):]

    def Current():
        y, x, h, w = panes.Layout(max(1, source_height), dimensions[1])[panes.active]
        return panes.views[panes.active], panes.browsers[panes.active], max(1, h - 2), w

    def DrawSource():
        if not source_height:
            return
        Write('\x1b7')
        for row in range(1, source_height + 1):
            Write('\x1b[{};1H\x1b[2K'.format(row))
        for key, (y, x, h, w) in panes.Layout(source_height, dimensions[1]).items():
            view = dict(panes.views[key], _source_focus=source_focused and key == panes.active)
            view['file'] = ('[MAIN] ' if key == 0 else '[VIEW {}] '.format(key)) + view.get('file', '')
            for row, text in enumerate(SourceBox(view, h, w, color=True)):
                Write('\x1b[{};{}H'.format(y + row + 1, x + 1) + text)
        Write('\x1b8')

    def Resize(force=False):
        nonlocal dimensions, source_height
        size = os.get_terminal_size(sys.stdout.fileno())
        new = (size.lines, size.columns)
        if not force and dimensions == new:
            return
        dimensions = new
        rows, columns = new
        source_height = rows // 2 if enabled and not pudb_active and rows >= 10 and columns >= 20 else 0
        split_state[0] = int(source_height > 0)
        Write('\x1b[?1000h\x1b[?1006h' if source_height else '\x1b[?1000l\x1b[?1006l')
        lower = max(1, rows - source_height)
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', lower, columns, 0, 0))
        Write('\x1b[r\x1b[2J\x1b[{};{}r\x1b[{};1H'.format(source_height + 1, rows, source_height + 1))
        if not pudb_active:
            output_history.Resize(columns, lower)
            Write(output_history.Render(source_height + 1))
        DrawSource()

    try:
        tty.setraw(sys.stdin.fileno())
        Write('\x1b[?1049h')
        Resize(True)
        banner = b'[ipdb] Initializing debugger session...\r\nShortcuts enabled: Ctrl-X then A = source; Ctrl-X then S = PuDB.\r\n'
        output_history.Feed(banner)
        Write(banner)
        while True:
            Resize()
            snapshot, browser, pane_height, pane_width = Current()
            expired = pane_keys.Flush() + navigation.Flush()
            if expired:
                if source_focused:
                    browser.Cancel(snapshot)
                    DrawSource()
                else:
                    os.write(master, expired)
            ready, _, _ = select.select([sys.stdin.fileno(), master, read_fd], [], [], 0.1)
            if read_fd in ready:
                chunk = os.read(read_fd, 65536)
                event_buffer += chunk
                while b'\n' in event_buffer:
                    packet, event_buffer = event_buffer.split(b'\n', 1)
                    try:
                        message = json.loads(packet.decode('ascii'))
                    except (ValueError, UnicodeError):
                        continue
                    if message.get('mode') == 'pudb':
                        pudb_active = True
                        Resize(True)
                        os.write(ack_write, b'1')
                        continue
                    if 'ready' in message:
                        paused = message['ready']
                        continue
                    panes.Update(message, max(1, source_height), dimensions[1])
                    snapshot, browser, pane_height, pane_width = Current()
                    DrawSource()
            if master in ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not chunk:
                    break
                if not pudb_active:
                    output_history.Feed(chunk)
                Write(chunk)
            if sys.stdin.fileno() in ready:
                chunk = os.read(sys.stdin.fileno(), 4096)
                if not chunk:
                    break
                if pudb_active:
                    os.write(master, chunk)
                    continue
                data, toggles, switches = keys.Feed(chunk)
                if keys.focus_toggles % 2 and source_height and paused:
                    source_focused = not source_focused
                    browser.Cancel(snapshot)
                    browser.Clamp(snapshot, pane_height, pane_width)
                    DrawSource()
                forwarded = bytearray()
                redraw = False
                for kind, value in pane_keys.Feed(data, bool(source_height), source_height):
                    if kind == 'focus':
                        browser.Cancel(snapshot)
                        source_focused = panes.Focus(value, source_focused, source_height, dimensions[1])
                        snapshot, browser, pane_height, pane_width = Current()
                        navigation.prefix = b''
                        browser.Cancel(snapshot)
                        browser.Clamp(snapshot, pane_height, pane_width)
                        redraw = True
                    elif kind == 'click':
                        browser.Cancel(snapshot)
                        navigation.prefix = b''
                        navigation.pending = b''
                        column, row = value
                        source_focused = panes.Click(column, row, source_height, dimensions[1])
                        snapshot, browser, pane_height, pane_width = Current()
                        redraw = True
                    elif kind == 'scroll':
                        direction, column, row = value
                        for key, (y, x, h, w) in panes.Layout(source_height, dimensions[1]).items():
                            if x <= column - 1 < x + w and y <= row - 1 < y + h:
                                for unused in range(3):
                                    ScrollSource(panes.views[key], direction, max(1, h - 2), w)
                                break
                        redraw = True
                    else:
                        if source_height and source_focused:
                            value = browser.Feed(value, snapshot, pane_height, pane_width)
                            if '_command' in snapshot:
                                panes.Command(snapshot.pop('_command'), source_height, dimensions[1])
                                snapshot, browser, pane_height, pane_width = Current()
                                browser.Clamp(snapshot, pane_height, pane_width)
                            redraw = True
                        typed, actions = navigation.Feed(value, bool(source_height and source_focused))
                        forwarded.extend(typed)
                        for action in actions:
                            browser.Move(snapshot, action, pane_height, pane_width)
                        redraw = redraw or bool(actions)
                data = bytes(forwarded)
                if redraw:
                    DrawSource()
                if switches:
                    if paused:
                        data += b'\x15pudb\n'
                    else:
                        Write('\r\nPause at an ipdb prompt before switching to PuDB.\r\n')
                if toggles % 2:
                    enabled = not enabled
                    source_focused = False
                    Resize(True)
                    os.write(master, b'\x0c')
                if b'\x0c' in data:
                    Resize(True)
                if data:
                    while data:
                        data = data[os.write(master, data):]
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
        Write('\x1b[?1000l\x1b[?1006l\x1b[r\x1b[0m\x1b[?1049l')
        recent_output = output_history.Tail()
        if recent_output:
            Write(recent_output.replace('\n', '\r\n') + '\r\n')
        os.close(master)
        os.close(read_fd)
        os.close(ack_write)
        split_state.close()
        waited, status = os.waitpid(pid, os.WNOHANG)
        if not waited:
            try:
                os.killpg(pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)
    if exit_code:
        Write('[ipdb] Debugger exited with status {}. See output above; retry with --plain for diagnostics.\r\n'.format(exit_code))
    return exit_code
