"""Vim dark syntax colors and cell-aware source-line rendering."""
from functools import lru_cache
import unicodedata

RESET = '\x1b[0m'
BACKGROUND = 0
CURRENT_BACKGROUND = 238


@lru_cache(maxsize=128)
def ColorIndex(color):
    color = color.lstrip('#')
    if len(color) == 3:
        color = ''.join(value * 2 for value in color)
    target = tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))
    levels = (0, 95, 135, 175, 215, 255)
    palette = [(16 + 36 * r + 6 * g + b, (levels[r], levels[g], levels[b]))
               for r in range(6) for g in range(6) for b in range(6)]
    palette.extend((232 + index, (8 + 10 * index,) * 3) for index in range(24))
    return min(palette, key=lambda item: sum((a - b) ** 2 for a, b in zip(target, item[1])))[0]


@lru_cache(maxsize=8)
def Tokenize(source):
    from pygments import lex
    from pygments.lexers import PythonLexer
    from pygments.styles import get_style_by_name
    from pygments.token import Comment, String, Name
    theme = get_style_by_name('vim')
    rows = [[]]
    for token, text in lex(source, PythonLexer(stripnl=False, ensurenl=False)):
        style = theme.style_for_token(token)
        foreground = style['color'] or 'cccccc'
        # Vim dark equivalents keep comments/strings readable on CursorLine.
        if token in Comment:
            foreground = '80a0ff'
        elif token in String:
            foreground = 'ffa0a0'
        elif token in Name.Function:
            foreground = '40ffff'
        color = ColorIndex(foreground)
        parts = text.split('\n')
        for index, part in enumerate(parts):
            if part:
                rows[-1].append((part, color, bool(style['bold'])))
            if index < len(parts) - 1:
                rows.append([])
    return rows


def TokenRows(lines):
    source = ''.join(line if line.endswith('\n') else line + '\n' for line in lines)
    return Tokenize(source)


def PaintLine(spans, number, current, width, horizontal=0, cursor=None):
    active = number == current
    background = CURRENT_BACKGROUND if active else BACKGROUND
    prefix = '{} {:5d} '.format('>' if active else ' ', number)
    segments = [(prefix, 226 if active else 244, active)] + list(spans)
    output = ['\x1b[48;5;{}m'.format(background)]
    used = 0
    code_column = 0
    for index, (text, color, bold) in enumerate(segments):
        output.append('\x1b[{};38;5;{}m'.format(1 if bold else 22, color))
        for character in text:
            if character == '\t':
                characters = ' ' * (4 - code_column % 4)
            else:
                characters = character if character.isprintable() else ' '
            for visible in characters:
                size = 0 if unicodedata.combining(visible) else (2 if unicodedata.east_asian_width(visible) in ('W', 'F') else 1)
                if index:
                    previous = code_column
                    code_column += size
                    if code_column <= horizontal and (size or previous < horizontal):
                        continue
                    if previous < horizontal:
                        size = code_column - horizontal
                        visible = ' ' * size
                if used + size > width:
                    return ''.join(output) + ' ' * (width - used) + RESET
                if index and cursor is not None and previous <= cursor < code_column:
                    output.append('\x1b[48;5;250;38;5;16m' + visible + '\x1b[48;5;{};38;5;{}m'.format(background, color))
                else:
                    output.append(visible)
                used += size
    if cursor is not None and cursor >= code_column and used < width:
        output.append('\x1b[48;5;250m \x1b[48;5;{}m'.format(background))
        used += 1
    return ''.join(output) + ' ' * max(0, width - used) + RESET
