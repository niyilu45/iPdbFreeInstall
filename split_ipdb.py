"""Linux PTY split view for ipdb. No additional packages are required."""
import json
import linecache
import os
import sys


class KeyFilter:
    def __init__(self):
        self.pending = False

    def Feed(self, data):
        output = bytearray()
        toggles = 0
        for value in data:
            if self.pending:
                self.pending = False
                if value in (ord('a'), 1):
                    toggles += 1
                    continue
                output.append(24)
            if value == 24:
                self.pending = True
            else:
                output.append(value)
        return bytes(output), toggles


def SourceRows(snapshot, height, width):
    lines = snapshot.get('lines', [])
    current = snapshot.get('line', 0)
    offset = snapshot.get('offset', 1)
    start = max(0, current - offset - height // 2)
    start = min(start, max(0, len(lines) - height))
    rows = []
    for index in range(start, min(start + height, len(lines))):
        number = offset + index
        marker = '>' if number == current else ' '
        text = ''.join(c if c.isprintable() else ' ' for c in lines[index].expandtabs(4))
        rows.append(('{} {:5d} {}'.format(marker, number, text))[:max(0, width - 1)])
    return rows


def InstallHook(event_fd):
    from IPython.core.debugger import Pdb
    original = Pdb.print_stack_entry

    def PrintStackEntry(self, frame_lineno, *args, **kwargs):
        frame, number = frame_lineno
        filename = frame.f_code.co_filename
        lines = linecache.getlines(filename, frame.f_globals)
        offset = max(0, number - 100)
        message = {'file': filename, 'line': number, 'offset': offset + 1,
                   'lines': [line[:1000] for line in lines[offset:number + 100]]}
        try:
            packet = (json.dumps(message, ensure_ascii=True) + '\n').encode('ascii')
            while packet:
                packet = packet[os.write(event_fd, packet):]
        except OSError:
            pass
        return original(self, frame_lineno, *args, **kwargs)

    Pdb.print_stack_entry = PrintStackEntry


def Run(arguments):
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

    read_fd, write_fd = os.pipe()
    saved = termios.tcgetattr(sys.stdin.fileno())
    pid, master = pty.fork()
    if pid == 0:
        os.close(read_fd)
        os.environ['TERM'] = 'dumb'
        os.environ['IPY_TEST_SIMPLE_PROMPT'] = '1'
        try:
            InstallHook(write_fd)
            sys.argv = [sys.argv[0]] + arguments
            from ipdb.__main__ import main
            main()
        finally:
            os.close(write_fd)
        os._exit(0)
    os.close(write_fd)
    enabled = True
    snapshot = {}
    event_buffer = b''
    keys = KeyFilter()
    dimensions = None
    source_height = 0
    status = None

    def Write(data):
        if isinstance(data, str):
            data = data.encode('utf-8', 'replace')
        while data:
            data = data[os.write(sys.stdout.fileno(), data):]

    def DrawSource():
        if not source_height:
            return
        rows, columns = dimensions
        title = '{}:{}'.format(snapshot.get('file', 'Waiting for debugger'), snapshot.get('line', ''))
        title = ''.join(c if c.isprintable() else ' ' for c in title)
        Write('\x1b7\x1b[1;1H\x1b[2K\x1b[7m' + title[:columns - 1] + '\x1b[0m')
        content = SourceRows(snapshot, source_height - 2, columns)
        for row in range(2, source_height):
            text = content[row - 2] if row - 2 < len(content) else ''
            Write('\x1b[{};1H\x1b[2K'.format(row) + text)
        footer = ' Source | Ctrl-X A: toggle | Ctrl-L: redraw | ipdb commands below '
        Write('\x1b[{};1H\x1b[2K\x1b[7m'.format(source_height) + footer[:columns - 1] + '\x1b[0m\x1b8')

    def Resize(force=False):
        nonlocal dimensions, source_height
        size = os.get_terminal_size(sys.stdout.fileno())
        new = (size.lines, size.columns)
        if not force and dimensions == new:
            return
        dimensions = new
        rows, columns = new
        source_height = rows // 2 if enabled and rows >= 10 else 0
        lower = max(1, rows - source_height)
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', lower, columns, 0, 0))
        Write('\x1b[r\x1b[2J\x1b[{};{}r\x1b[{};1H'.format(source_height + 1, rows, source_height + 1))
        DrawSource()

    try:
        tty.setraw(sys.stdin.fileno())
        Write('\x1b[?1049h')
        Resize(True)
        while True:
            Resize()
            ready, _, _ = select.select([sys.stdin.fileno(), master, read_fd], [], [], 0.1)
            if read_fd in ready:
                chunk = os.read(read_fd, 65536)
                event_buffer += chunk
                while b'\n' in event_buffer:
                    packet, event_buffer = event_buffer.split(b'\n', 1)
                    try:
                        snapshot = json.loads(packet.decode('ascii'))
                    except (ValueError, UnicodeError):
                        continue
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
                Write(chunk)
            if sys.stdin.fileno() in ready:
                chunk = os.read(sys.stdin.fileno(), 4096)
                if not chunk:
                    break
                data, toggles = keys.Feed(chunk)
                if toggles % 2:
                    enabled = not enabled
                    Resize(True)
                    os.write(master, b'\x0c')
                if b'\x0c' in data:
                    Resize(True)
                if data:
                    while data:
                        data = data[os.write(master, data):]
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
        Write('\x1b[r\x1b[0m\x1b[?1049l')
        os.close(master)
        os.close(read_fd)
        waited, status = os.waitpid(pid, os.WNOHANG)
        if not waited:
            try:
                os.killpg(pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            _, status = os.waitpid(pid, 0)
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)
