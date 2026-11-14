"""Read-only source cursor, literal search and per-file Vim marks."""
import unicodedata


def Cells(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in text.expandtabs(4))


class SourceBrowser:
    def __init__(self):
        self.marks = {}
        self.query = ''
        self.direction = 1
        self.mode = ''
        self.text = b''

    def Clamp(self, view, height, width):
        lines = view.get('lines', [])
        row = min(max(0, view.get('_cursor_row', view.get('line', 1) - view.get('offset', 1))), max(0, len(lines) - 1))
        line = lines[row].rstrip('\r\n') if lines else ''
        column = min(max(0, view.get('_cursor_col', 0)), max(0, len(line) - 1))
        view['_cursor_row'], view['_cursor_col'] = row, column
        top = view.get('_top', max(0, row - height // 2))
        view['_top'] = min(max(0, len(lines) - height), max(0, min(top, row) if row < top + height else row - height + 1))
        cell = Cells(line[:column])
        left = view.get('_left', 0)
        view['_left'] = max(0, min(left, cell) if cell < left + max(1, width - 11) else cell - max(1, width - 11) + 1)
        view['_cursor_cell'] = cell

    def Move(self, view, action, height, width):
        self.Clamp(view, height, width)
        row, column = view['_cursor_row'], view['_cursor_col']
        if action in ('up', 'down', 'page_up', 'page_down', 'half_up', 'half_down'):
            row += {'up': -1, 'down': 1, 'page_up': -height, 'page_down': height,
                    'half_up': -max(1, height // 2), 'half_down': max(1, height // 2)}[action]
        elif action in ('left', 'right'):
            column += -1 if action == 'left' else 1
        elif action == 'first':
            row = 0
        elif action == 'last':
            row = len(view.get('lines', [])) - 1
        elif action == 'line_start':
            column = 0
        elif action == 'line_end':
            column = len(view.get('lines', [''])[row].rstrip('\r\n')) if view.get('lines') else 0
        elif action == 'home':
            row, column = view.get('line', 1) - view.get('offset', 1), 0
            view.pop('_top', None)
        view['_cursor_row'], view['_cursor_col'] = row, column
        self.Clamp(view, height, width)

    def Search(self, view, direction, height, width):
        if not self.query:
            view['_status'] = 'No previous search'
            return
        self.Clamp(view, height, width)
        matches = []
        for row, line in enumerate(view.get('lines', [])):
            start = 0
            while True:
                column = line.find(self.query, start)
                if column < 0:
                    break
                matches.append((row, column))
                start = column + 1
        if not matches:
            view['_status'] = 'Not found: ' + self.query
            return
        current = (view['_cursor_row'], view['_cursor_col'])
        ordered = matches if direction > 0 else list(reversed(matches))
        candidates = [item for item in ordered if (item > current if direction > 0 else item < current)]
        row, column = (candidates or ordered)[0]
        view['_cursor_row'], view['_cursor_col'] = row, column
        view['_status'] = ('/' if direction > 0 else '?') + self.query
        self.Clamp(view, height, width)

    def Cancel(self, view):
        self.mode, self.text = '', b''
        view['_status'] = ''

    def Feed(self, data, view, height, width):
        output = bytearray()
        for value in data:
            if self.mode in ('/', '?'):
                if value == 27:
                    self.Cancel(view)
                elif value in (10, 13):
                    query = self.text.decode('utf-8', 'replace')
                    if query:
                        self.query = query
                    self.direction = 1 if self.mode == '/' else -1
                    self.mode, self.text = '', b''
                    self.Search(view, self.direction, height, width)
                elif value in (8, 127):
                    self.text = self.text.decode('utf-8', 'ignore')[:-1].encode('utf-8')
                elif value >= 32:
                    self.text += bytes([value])
                if self.mode:
                    view['_status'] = self.mode + self.text.decode('utf-8', 'replace')
                continue
            if self.mode in ('m', "'", '`'):
                mode, self.mode = self.mode, ''
                if ord('a') <= value <= ord('z'):
                    key = (view.get('file'), chr(value))
                    self.Clamp(view, height, width)
                    if mode == 'm':
                        self.marks[key] = (view['_cursor_row'], view['_cursor_col'])
                        view['_status'] = 'Mark ' + chr(value) + ' saved'
                    elif key in self.marks:
                        row, column = self.marks[key]
                        view['_cursor_row'], view['_cursor_col'] = row, column if mode == '`' else 0
                        self.Clamp(view, height, width)
                        view['_status'] = 'Mark ' + chr(value)
                    else:
                        view['_status'] = 'Mark ' + chr(value) + ' not set'
                else:
                    view['_status'] = 'Mark name must be a-z'
                continue
            if value in (ord('/'), ord('?'), ord('m'), ord("'"), ord('`')):
                self.mode, self.text = chr(value), b''
                view['_status'] = self.mode
            elif value in (ord('n'), ord('N')):
                self.Search(view, self.direction * (1 if value == ord('n') else -1), height, width)
            else:
                output.append(value)
        return bytes(output)
