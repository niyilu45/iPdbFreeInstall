"""Split tree for independently browsable source windows."""
from source_browser import SourceBrowser


class SourcePanes:
    def __init__(self):
        self.views = {0: {}}
        self.browsers = {0: SourceBrowser()}
        self.tree = 0
        self.active = 0
        self.next_id = 1

    def Layout(self, height, width):
        result = {}
        def Walk(node, y, x, h, w):
            if isinstance(node, int):
                result[node] = (y, x, h, w)
            else:
                axis, first, second = node
                if axis == 'v':
                    cut = w // 2
                    Walk(first, y, x, h, cut)
                    Walk(second, y, x + cut, h, w - cut)
                else:
                    cut = h // 2
                    Walk(first, y, x, cut, w)
                    Walk(second, y + cut, x, h - cut, w)
        Walk(self.tree, 0, 0, height, width)
        return result

    def Update(self, message, height, width):
        self.views[0] = message
        self.browsers[0].Cancel(message)
        y, x, h, w = self.Layout(height, width)[0]
        self.browsers[0].Clamp(message, max(1, h - 2), w)

    def Command(self, command, height, width):
        command = command.strip()
        view = self.views[self.active]
        if command in ('q', 'quit'):
            if self.active == 0:
                view['_status'] = 'Main source window cannot be closed'
                return
            closing = self.active
            def Remove(node):
                if isinstance(node, int):
                    return None if node == closing else node
                axis, first, second = node
                first, second = Remove(first), Remove(second)
                return second if first is None else first if second is None else (axis, first, second)
            self.tree = Remove(self.tree)
            del self.views[closing]
            del self.browsers[closing]
            self.active = 0
            return
        if command not in ('sp', 'split', 'vs', 'vsplit'):
            view['_status'] = 'Commands: split, vsplit, q'
            return
        axis = 'v' if command in ('vs', 'vsplit') else 'h'
        y, x, h, w = self.Layout(height, width)[self.active]
        if (axis == 'v' and w < 40) or (axis == 'h' and h < 8):
            view['_status'] = 'Window too small to split'
            return
        new = self.next_id
        self.next_id += 1
        def Replace(node):
            if isinstance(node, int):
                return (axis, node, new) if node == self.active else node
            return (node[0], Replace(node[1]), Replace(node[2]))
        self.tree = Replace(self.tree)
        self.views[new] = dict(view, _status='')
        browser = SourceBrowser()
        browser.marks = self.browsers[self.active].marks
        browser.query = self.browsers[self.active].query
        self.browsers[new] = browser
        self.active = new

    def Click(self, column, row, height, width):
        for key, (y, x, h, w) in self.Layout(height, width).items():
            if x <= column - 1 < x + w and y <= row - 1 < y + h:
                self.active = key
                view, browser = self.views[key], self.browsers[key]
                browser.Cancel(view)
                if y + 1 <= row - 1 < y + h - 1:
                    from split_ipdb import ViewStart
                    from source_browser import Cells
                    line = ViewStart(view, max(1, h - 2)) + row - y - 2
                    lines = view.get('lines', [])
                    if line < len(lines):
                        view['_cursor_row'] = line
                        cell = max(0, column - x - 10) + view.get('_left', 0)
                        text = lines[line].rstrip('\r\n')
                        index = 0
                        while index + 1 < len(text) and Cells(text[:index + 1]) <= cell:
                            index += 1
                        view['_cursor_col'] = index
                browser.Clamp(view, max(1, h - 2), w)
                return True
        return False

    def Focus(self, direction, source_focused, height, width):
        boxes = self.Layout(height, width)
        boxes[-1] = (height, 0, 1, width)
        current = self.active if source_focused else -1
        y, x, h, w = boxes[current]
        candidates = []
        for key, (ny, nx, nh, nw) in boxes.items():
            if key == current:
                continue
            vertical = direction in 'jk'
            overlap = min(x + w, nx + nw) - max(x, nx) if vertical else min(y + h, ny + nh) - max(y, ny)
            distance = {'j': ny - y - h, 'k': y - ny - nh, 'h': x - nx - nw, 'l': nx - x - w}[direction]
            if overlap > 0 and distance >= 0:
                candidates.append((distance, -overlap, key))
        if not candidates:
            return source_focused
        target = min(candidates)[2]
        if target >= 0:
            self.active = target
        return target >= 0
