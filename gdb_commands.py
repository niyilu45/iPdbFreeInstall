"""GDB-style command adapters for IPython's Python debugger."""
import re


COMMANDS = (
    'backtrace', 'break', 'checkpoint', 'clear', 'commands', 'condition',
    'continue', 'debug', 'delete', 'disable', 'display', 'enable', 'exit',
    'finish', 'frame', 'help', 'ignore', 'info', 'interact', 'jump', 'list',
    'next', 'print', 'pudb', 'quit', 'restart', 'return', 'run', 'set',
    'source', 'step', 'tbreak', 'undisplay', 'until', 'up', 'where', 'whatis',
)
ALIASES = {
    'bt': 'backtrace', 'b': 'break', 'c': 'continue', 'cont': 'continue',
    'd': 'delete', 'dis': 'disable', 'disp': 'display', 'f': 'frame',
    'fin': 'finish', 'h': 'help', 'i': 'info', 'j': 'jump', 'l': 'list',
    'n': 'next', 'p': 'print', 'q': 'quit', 'r': 'run', 's': 'step',
    'u': 'up', 'unt': 'until', 'w': 'where',
}


def Resolve(word, choices, aliases=None):
    if aliases and word in aliases:
        return aliases[word]
    if word in choices:
        return word
    matches = sorted(value for value in choices if value.startswith(word))
    if len(matches) > 1:
        raise ValueError('Ambiguous command {!r}: {}'.format(word, ', '.join(matches)))
    return matches[0] if matches else word


def SplitArgument(text):
    parts = text.split(None, 1)
    return (parts[0], parts[1].strip() if len(parts) > 1 else '') if parts else ('', '')


def Translate(line, native_commands=()):
    stripped = line.strip()
    if not stripped or stripped.startswith('!'):
        return line
    command, argument = SplitArgument(stripped)
    if command == 'down':
        raise ValueError('down is disabled; use frame N to select a stack frame')
    if command in native_commands and command not in ALIASES:
        resolved = command
    else:
        resolved = Resolve(command, COMMANDS, ALIASES)
    command = resolved
    if command in ('info', 'delete', 'enable', 'disable') and argument:
        subcommand, remainder = SplitArgument(argument)
        choices = ('breakpoints', 'locals', 'args', 'checkpoints', 'display') if command == 'info' else ('breakpoints', 'checkpoint', 'display')
        if command in ('enable', 'disable'):
            choices = ('breakpoints', 'display')
        subcommand = Resolve(subcommand, choices, {'b': 'breakpoints', 'disp': 'display'})
        argument = subcommand + (' ' + remainder if remainder else '')
        if command in ('enable', 'disable') and subcommand == 'breakpoints':
            argument = remainder
        elif command in ('enable', 'disable') and subcommand == 'display':
            command += '_display'
            argument = remainder
        elif command == 'delete':
            if subcommand == 'checkpoint':
                command, argument = 'delete_checkpoint', remainder
            elif subcommand == 'display':
                command, argument = 'undisplay', remainder
            elif subcommand == 'breakpoints':
                command, argument = 'clear', remainder
    if command == 'delete':
        command = 'clear'
    if command == 'set' and argument:
        subcommand, remainder = SplitArgument(argument)
        subcommand = Resolve(subcommand, ('variable',), {'v': 'variable', 'var': 'variable'})
        argument = subcommand + (' ' + remainder if remainder else '')
    if command in ('break', 'tbreak'):
        parts = re.split(r'\s+if\s+', argument, maxsplit=1)
        if len(parts) == 2:
            argument = parts[0] + ', ' + parts[1]
    command = {'finish': 'return', 'print': 'p'}.get(command, command)
    return command + (' ' + argument if argument else '')


def Install():
    from IPython.core.debugger import Pdb
    if getattr(Pdb, '_portable_gdb', False):
        return
    Pdb._portable_gdb = True
    original = Pdb.precmd
    original_restart = Pdb.do_restart
    original_stack_entry = Pdb.print_stack_entry

    def DisplayEntries(self):
        if not hasattr(self, '_portable_displays'):
            self._portable_displays = {}
            self._portable_display_next = 1
        return self._portable_displays

    def RenderDisplay(self, number, entry, frame):
        try:
            value = eval(entry['code'], frame.f_globals, frame.f_locals)
            rendered = repr(value)
        except Exception as error:
            rendered = '<{}: {}>'.format(type(error).__name__, error)
        self.message('{}: {} = {}'.format(number, entry['expression'], rendered))

    def ShowDisplays(self, frame):
        for number, entry in list(DisplayEntries(self).items()):
            if entry['enabled']:
                RenderDisplay(self, number, entry, frame)

    def StackEntry(self, frame_lineno, *args, **kwargs):
        result = original_stack_entry(self, frame_lineno, *args, **kwargs)
        if not getattr(self, '_portable_pudb', False):
            ShowDisplays(self, frame_lineno[0])
        return result

    def Display(self, argument):
        """display EXPR (or disp EXPR): evaluate now and at every stop.

        Bare display re-evaluates all enabled expressions. Use info display
        to list IDs; enable/disable display ID and undisplay ID manage them.
        Expressions use the selected frame and may have side effects.
        """
        expression = argument.strip()
        if not expression:
            ShowDisplays(self, self.curframe)
            return
        try:
            code = compile(expression, '<display>', 'eval')
        except SyntaxError as error:
            self.error(str(error))
            return
        entries = DisplayEntries(self)
        number = self._portable_display_next
        self._portable_display_next += 1
        entries[number] = {'expression': expression, 'code': code, 'enabled': True}
        RenderDisplay(self, number, entries[number], self.curframe)

    def ChangeDisplays(self, argument, action):
        entries = DisplayEntries(self)
        try:
            numbers = list(dict.fromkeys(int(value) for value in argument.split())) if argument.strip() else list(entries)
            unknown = [number for number in numbers if number not in entries]
            if unknown:
                raise ValueError('Unknown display IDs: {}'.format(', '.join(map(str, unknown))))
        except ValueError as error:
            self.error(str(error))
            return
        for number in numbers:
            if action == 'delete':
                del entries[number]
            else:
                entries[number]['enabled'] = action == 'enable'

    def Undisplay(self, argument):
        """undisplay [ID ...]: remove selected expressions, or all if omitted."""
        ChangeDisplays(self, argument, 'delete')

    def EnableDisplay(self, argument):
        ChangeDisplays(self, argument, 'enable')

    def DisableDisplay(self, argument):
        ChangeDisplays(self, argument, 'disable')

    native_commands = {name[3:] for name in dir(Pdb) if name.startswith('do_')}

    def Noop(self, argument):
        pass

    def Down(self, argument):
        self.error('down is disabled; use frame N to select a stack frame')

    def Precmd(self, line):
        try:
            return original(self, Translate(line, native_commands))
        except ValueError as error:
            self.error(str(error))
            return '_portable_noop'

    def Info(self, argument):
        """info breakpoints|locals|args|checkpoints|display"""
        if argument == 'display':
            entries = DisplayEntries(self)
            if not entries:
                self.message('No display expressions')
            for number, entry in entries.items():
                self.message('{}: [{}] {}'.format(number, 'enabled' if entry['enabled'] else 'disabled', entry['expression']))
            return
        if argument in ('break', 'breakpoints', 'b'):
            return self.do_break('')
        if argument == 'args':
            return self.do_args('')
        if argument == 'locals':
            for name, value in sorted(self.curframe.f_locals.items()):
                try:
                    self.message('{} = {!r}'.format(name, value))
                except Exception as error:
                    self.message('{} = <repr failed: {}>'.format(name, error))
            return
        if argument == 'checkpoints':
            from checkpoints import Manager
            if not Manager.entries:
                self.message('No checkpoints')
            for number, entry in sorted(Manager.entries.items()):
                self.message('{}  {}:{}'.format(number, entry['file'], entry['line']))
            return
        self.error('Usage: info breakpoints|locals|args|checkpoints|display')

    def Frame(self, argument):
        """Select a frame; frame 0 is the innermost frame."""
        try:
            number = int(argument or '0')
            if number < 0 or number >= len(self.stack):
                raise ValueError()
        except ValueError:
            self.error('Frame number is out of range')
            return
        self.curindex = len(self.stack) - 1 - number
        self.curframe = self.stack[self.curindex][0]
        self.curframe_locals = self.curframe.f_locals
        self.print_stack_entry(self.stack[self.curindex])

    def Backtrace(self, argument):
        """bt [COUNT]: show frames numbered from the innermost frame (#0)."""
        frames = list(enumerate(reversed(self.stack)))
        if argument:
            try:
                count = int(argument)
                frames = frames[:count] if count >= 0 else frames[count:]
            except ValueError:
                self.error('Usage: bt [COUNT]')
                return
        for number, (frame, line) in frames:
            marker = '*' if len(self.stack) - 1 - number == self.curindex else ' '
            self.message('{} #{} {} at {}:{}'.format(marker, number,
                         frame.f_code.co_name, frame.f_code.co_filename, line))

    def Set(self, argument):
        """set var NAME = PYTHON_EXPRESSION"""
        match = re.fullmatch(r'(?:var|variable)\s+([A-Za-z_]\w*)\s*=\s*(.+)', argument)
        if not match:
            self.error('Usage: set var NAME = PYTHON_EXPRESSION')
            return
        name, expression = match.groups()
        try:
            value = eval(expression, self.curframe.f_globals, self.curframe.f_locals)
            if name not in (self.curframe.f_code.co_varnames + self.curframe.f_code.co_cellvars + self.curframe.f_code.co_freevars):
                self.curframe.f_globals[name] = value
            else:
                import ctypes
                self.curframe.f_locals[name] = value
                sync = ctypes.pythonapi.PyFrame_LocalsToFast
                sync.argtypes = [ctypes.py_object, ctypes.c_int]
                sync.restype = None
                sync(self.curframe, 0)
            self.curframe_locals = self.curframe.f_locals
        except Exception as error:
            self.error(str(error))

    def Checkpoint(self, argument):
        """Save a single-use Linux fork checkpoint of this paused session."""
        from checkpoints import Manager
        if argument:
            self.error('Usage: checkpoint')
            return
        try:
            Manager.Create(self)
        except (ValueError, OSError) as error:
            self.error(str(error))

    def Restart(self, argument):
        """restart ID restores a checkpoint; bare restart restarts the script."""
        if not argument.strip():
            return original_restart(self, argument)
        from checkpoints import Manager
        try:
            Manager.Restart(int(argument))
        except (ValueError, OSError) as error:
            self.error(str(error))

    def DeleteCheckpoint(self, argument):
        from checkpoints import Manager
        try:
            Manager.Delete(int(argument))
        except (ValueError, OSError) as error:
            self.error(str(error))

    Pdb.precmd = Precmd
    Pdb.do__portable_noop = Noop
    Pdb.do_down = Down
    Pdb.do_d = Pdb.do_clear
    Pdb.print_stack_entry = StackEntry
    Pdb._portable_show_displays = ShowDisplays
    Pdb.do_display = Display
    Pdb.do_undisplay = Undisplay
    Pdb.do_enable_display = EnableDisplay
    Pdb.do_disable_display = DisableDisplay
    Pdb.do_info = Info
    Pdb.do_backtrace = Backtrace
    Pdb.do_frame = Frame
    Pdb.do_set = Set
    Pdb.do_checkpoint = Checkpoint
    Pdb.do_restart = Restart
    Pdb.do_delete_checkpoint = DeleteCheckpoint
