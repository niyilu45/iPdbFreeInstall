"""Preserve the command terminal across layout changes using a VT emulator."""
import pyte


class OutputHistory:
    def __init__(self, columns, rows, limit=10000):
        self.screen = pyte.HistoryScreen(columns, rows, history=limit)
        self.stream = pyte.ByteStream(self.screen)

    def Feed(self, data):
        self.stream.feed(data)

    def Tail(self, limit=100):
        lines = list(self.screen.history.top) + [self.screen.buffer[index] for index in range(self.screen.lines)]
        result = [''.join(line.get(column, self.screen.default_char).data
                          for column in range(self.screen.columns)).rstrip() for line in lines]
        while result and not result[-1]:
            result.pop()
        return '\n'.join(result[-limit:])

    def Resize(self, columns, rows):
        screen = self.screen
        if screen.columns == columns and screen.lines == rows:
            return
        previous = list(screen.history.top)
        cursor = len(previous) + screen.cursor.y
        last = screen.cursor.y
        for row in range(screen.lines):
            if any(cell.data.strip() for cell in screen.buffer[row].values()):
                last = max(last, row)
        previous.extend(screen.buffer[row] for row in range(last + 1))
        start = min(cursor, max(0, len(previous) - rows))
        screen.columns, screen.lines = columns, rows
        screen.buffer.clear()
        for index, line in enumerate(previous[start:start + rows]):
            screen.buffer[index] = line
        screen.history.top.clear()
        screen.history.top.extend(previous[:start])
        screen.history.bottom.clear()
        screen.history = screen.history._replace(position=screen.history.size)
        screen.cursor.y = cursor - start
        screen.cursor.x = min(screen.cursor.x, columns)
        screen.set_margins()
        screen.dirty.update(range(rows))

    @staticmethod
    def Style(cell):
        from source_theme import ColorIndex
        codes = ['0']
        for attribute, code in (('bold', '1'), ('italics', '3'), ('underscore', '4'), ('reverse', '7'), ('strikethrough', '9')):
            if getattr(cell, attribute, False):
                codes.append(code)
        names = {'black': 0, 'red': 1, 'green': 2, 'brown': 3, 'blue': 4, 'magenta': 5, 'cyan': 6, 'white': 7}
        for value, base, extended in ((cell.fg, 30, 38), (cell.bg, 40, 48)):
            if value in names:
                codes.append(str(base + names[value]))
            elif value != 'default' and len(value) == 6:
                try:
                    codes.extend([str(extended), '5', str(ColorIndex(value))])
                except ValueError:
                    pass
        return '\x1b[' + ';'.join(codes) + 'm'

    def Render(self, top):
        screen = self.screen
        output = []
        for index in range(screen.lines):
            output.append('\x1b[{};1H\x1b[0m\x1b[2K'.format(top + index))
            line = screen.buffer[index]
            last_style = None
            for column in range(screen.columns):
                cell = line.get(column, screen.default_char)
                style = self.Style(cell)
                if style != last_style:
                    output.append(style)
                    last_style = style
                output.append(cell.data)
        output.append('\x1b[0m\x1b[{};{}H'.format(top + screen.cursor.y, min(screen.cursor.x, screen.columns - 1) + 1))
        if screen.cursor.x >= screen.columns:
            cell = screen.buffer[screen.cursor.y].get(screen.columns - 1, screen.default_char)
            output.append(self.Style(cell) + cell.data)
        output.append(self.Style(screen.cursor.attrs))
        return ''.join(output)
