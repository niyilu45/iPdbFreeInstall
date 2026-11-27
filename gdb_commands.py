"""GDB-style command adapters for IPython's Python debugger."""
import os
import re
import sys
from functools import wraps

from reload_code import DisplayPath


COMMANDS = (
    'backtrace', 'break', 'checkpoint', 'clear', 'commands', 'condition',
    'continue', 'debug', 'delete', 'disable', 'display', 'enable', 'exit',
    'finish', 'frame', 'help', 'ignore', 'info', 'interact', 'jump', 'list',
    'next', 'print', 'pudb', 'quit', 'restart', 'return', 'rtime', 'run', 'set',
    'source', 'step', 'tbreak', 'undisplay', 'until', 'up', 'where', 'whatis',
)
ALIASES = {
    'bt': 'backtrace', 'b': 'break', 'c': 'continue', 'cont': 'continue',
    'd': 'delete', 'dis': 'disable', 'disp': 'display', 'f': 'frame',
    'fin': 'finish', 'h': 'help', 'i': 'info', 'j': 'jump', 'l': 'list',
    'n': 'next', 'p': 'print', 'q': 'quit', 'r': 'run', 'rt': 'rtime', 's': 'step',
    'u': 'up', 'unt': 'until', 'w': 'where',
}
# The ConEmu connector can pass focus reports and keyboard sequences to a
# Windows line reader as text. A standalone Escape can also precede a command.
CONEMU_ESCAPE = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]?|O[@-~])?')
BREAKPOINT_LOCATION = re.compile(
    r'^((?:Breakpoint \d+|(?:Deleted|Enabled|Disabled) breakpoint \d+|'
    r'\d+ +breakpoint +(?:keep|del) +(?:yes|no)) +at )([^\r\n]+)(:\d+)(?=\r?\n|$)')


def ShowBreakpointPath(command):
    """Render native breakpoint messages while keeping bdb's lookup keys intact."""
    @wraps(command)
    def Run(self, *args, **kwargs):
        message = self.message
        previous = vars(self).get('message')

        def Render(text):
            match = BREAKPOINT_LOCATION.match(text) if isinstance(text, str) else None
            if match:
                start, end = match.span(2)
                text = text[:start] + DisplayPath(text[start:end]) + text[end:]
            message(text)

        self.message = Render
        try:
            return command(self, *args, **kwargs)
        finally:
            if previous is None:
                del self.message
            else:
                self.message = previous

    return Run


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
    if command.startswith(('p/', 'print/')):
        format_name = command.split('/', 1)[1]
        return 'print_format ' + format_name + (' ' + argument if argument else '')
    if command == 'down':
        raise ValueError('down is disabled; use frame N to select a stack frame')
    if command in native_commands and command not in ALIASES:
        resolved = command
    else:
        resolved = Resolve(command, COMMANDS, ALIASES)
    command = resolved
    if command in ('info', 'delete', 'enable', 'disable') and argument:
        subcommand, remainder = SplitArgument(argument)
        choices = ('breakpoints', 'locals', 'args', 'checkpoints', 'display', 'timing') if command == 'info' else ('breakpoints', 'checkpoint', 'display')
        if command in ('enable', 'disable'):
            choices = ('breakpoints', 'display')
        aliases = {'b': 'breakpoints', 'disp': 'display'}
        if command == 'info':
            aliases['time'] = 'timing'
        subcommand = Resolve(subcommand, choices, aliases)
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
    if command in ('break', 'tbreak') and SplitArgument(argument)[0] != 'if':
        parts = re.split(r'\s+if\s+', argument, maxsplit=1)
        if len(parts) == 2:
            argument = parts[0] + ', ' + parts[1]
    command = {'finish': 'return', 'print': 'p'}.get(command, command)
    return command + (' ' + argument if argument else '')


def ParseIds(argument):
    numbers = []
    for token in argument.split():
        match = re.fullmatch(r'(\d+)(?:-(\d+))?', token)
        if not match:
            raise ValueError('Invalid ID or range: ' + token)
        first = int(match.group(1))
        last = int(match.group(2) or first)
        if first < 1 or last < first:
            raise ValueError('Invalid ID range: ' + token)
        numbers.extend(range(first, last + 1))
    return list(dict.fromkeys(numbers))


def Install():
    from IPython.core.debugger import Pdb
    from completion import AmbiguousBuffer, InstallCompletion, ResolveBreakpointFile
    if getattr(Pdb, '_portable_gdb', False):
        return
    Pdb._portable_gdb = True
    original_break = Pdb.do_break
    original_clear = Pdb.do_clear
    original_enable = Pdb.do_enable
    original_disable = Pdb.do_disable
    original_checkline = Pdb.checkline

    def CheckLine(self, filename, lineno):
        import linecache
        linecache.checkcache(filename)
        lines = linecache.getlines(filename)
        if 0 < lineno <= len(lines) and not lines[lineno - 1].strip():
            import dis
            import types
            try:
                code = compile(''.join(lines), filename, 'exec')
            except (SyntaxError, ValueError) as error:
                self.error('Cannot find an executable line: {}'.format(error))
                return 0
            pending = [code]
            executable = set()
            while pending:
                current = pending.pop()
                executable.update(line for _, line in dis.findlinestarts(current))
                pending.extend(value for value in current.co_consts if isinstance(value, types.CodeType))
            candidates = sorted(line for line in executable if line > lineno
                                and line <= len(lines) and lines[line - 1].strip()
                                and not lines[line - 1].lstrip().startswith('#'))
            if not candidates:
                self.error('No executable line below {}:{}'.format(DisplayPath(filename), lineno))
                return 0
            target = candidates[0]
            self.message('Breakpoint moved from line {} to {}'.format(lineno, target))
            lineno = target
        return original_checkline(self, filename, lineno)

    Pdb.checkline = CheckLine
    original = Pdb.precmd
    original_restart = Pdb.do_restart
    original_stack_entry = Pdb.print_stack_entry
    original_break_here = Pdb.break_here

    def BreakHere(self, frame):
        if not self.breaks:
            return original_break_here(self, frame)
        import bdb
        # Temporary breakpoints may be deleted by the native hit handler.
        filename = self.canonic(frame.f_code.co_filename)
        lines = self.breaks.get(filename, ())
        if frame.f_lineno not in lines and frame.f_code.co_firstlineno not in lines:
            return original_break_here(self, frame)
        candidates = list(bdb.Breakpoint.bplist.get((filename, frame.f_lineno), ()))
        if frame.f_lineno != frame.f_code.co_firstlineno:
            candidates += list(bdb.Breakpoint.bplist.get((filename, frame.f_code.co_firstlineno), ()))
        conditions = {entry.number: entry.cond for entry in candidates}
        hit = original_break_here(self, frame)
        if hit:
            number = self.currentbp
            condition = conditions.get(number)
            self.message('Breakpoint {} hit; condition: {}'.format(
                number, condition if condition else '<unconditional>'))
        return hit

    Pdb.break_here = BreakHere

    def UserCall(self, frame, argument_list):
        # Keep bdb tracing this frame. The line event stops before the first statement runs.
        return

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
            rendered = repr(eval(code, self.curframe.f_globals, self.curframe.f_locals))
        except Exception as error:
            self.error('{}: {}'.format(type(error).__name__, error))
            return
        entries = DisplayEntries(self)
        number = self._portable_display_next
        self._portable_display_next += 1
        entries[number] = {'expression': expression, 'code': code, 'enabled': True}
        self.message('{}: {} = {}'.format(number, expression, rendered))

    def ChangeDisplays(self, argument, action):
        entries = DisplayEntries(self)
        try:
            numbers = ParseIds(argument) if argument.strip() else list(entries)
            unknown = [number for number in numbers if number not in entries]
            if unknown:
                self.error('Unknown display IDs: {}'.format(', '.join(map(str, unknown))))
                if action != 'delete':
                    return
        except ValueError as error:
            self.error(str(error))
            return
        for number in numbers:
            if number not in entries:
                continue
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
        if argument:
            self.lastcmd = argument

    def Down(self, argument):
        self.error('down is disabled; use frame N to select a stack frame')

    def Precmd(self, line):
        if '\x1b' in line and sys.platform == 'win32' and os.environ.get('ConEmuPID') and not sys.stdin.isatty():
            line = CONEMU_ESCAPE.sub('', line)
            if not line.strip():
                return ('_portable_noop ' + self.lastcmd).rstrip()
        try:
            translated = Translate(line, native_commands)
            command, argument = SplitArgument(translated)
            word, condition = SplitArgument(argument)
            if command in ('break', 'tbreak') and word == 'if':
                if not condition:
                    raise ValueError('A breakpoint condition is required after if')
                frame = getattr(self, 'curframe', None)
                if frame is None:
                    raise ValueError('No current frame for the breakpoint')
                translated = '{} {}:{}, {}'.format(
                    command, frame.f_code.co_filename, frame.f_lineno, condition)
            return original(self, translated)
        except ValueError as error:
            self.error(str(error))
            return '_portable_noop'

    def Info(self, argument):
        """info breakpoints|locals|args|checkpoints|display|timing [all|NAME]"""
        topic, query = SplitArgument(argument)
        if topic in ('time', 'timing'):
            from function_timing import ShowTiming
            return ShowTiming(self, query)
        if argument == 'display':
            entries = DisplayEntries(self)
            if not entries:
                self.message('No display expressions')
            for number, entry in entries.items():
                self.message('{}: [{}] {}'.format(number, 'enabled' if entry['enabled'] else 'disabled', entry['expression']))
            return
        if argument in ('break', 'breakpoints', 'b'):
            if not self.get_all_breaks():
                self.message('No breakpoints')
                return
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
        self.error('Usage: info breakpoints|locals|args|checkpoints|display|timing [all|NAME]')

    def PrintFormat(self, argument):
        """Print an integer expression using /t, /x, /o, /d or /c."""
        import operator
        format_name, expression = SplitArgument(argument)
        formats = {'t': 'b', 'x': 'x', 'o': 'o', 'd': 'd'}
        if format_name not in formats and format_name != 'c':
            self.error('Supported print formats: /t /x /o /d /c')
            return
        if not expression:
            self.error('An expression is required')
            return
        try:
            value = operator.index(eval(expression, self.curframe.f_globals, self.curframe.f_locals))
            if format_name == 'c':
                rendered = repr(chr(value))
            elif format_name == 'd':
                rendered = str(value)
            else:
                bits = (value if value >= 0 else ~value).bit_length() + 1
                width = max(8, ((bits + 7) // 8) * 8)
                encoded = value & ((1 << width) - 1)
                digits = {'t': width, 'x': width // 4, 'o': (width + 2) // 3}[format_name]
                prefix = {'t': '', 'x': '0x', 'o': '0o'}[format_name]
                rendered = prefix + format(encoded, '0{}{}'.format(digits, formats[format_name]))
            self.message(rendered)
        except Exception as error:
            self.error('{}: {}'.format(type(error).__name__, error))

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
                         frame.f_code.co_name, DisplayPath(frame.f_code.co_filename), line))

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
        """Save a reusable Linux fork checkpoint of this paused session."""
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

    def ApplyIds(self, argument, entries, action, label):
        try:
            numbers = ParseIds(argument)
        except ValueError as error:
            self.error(str(error))
            return
        if not numbers:
            self.error('At least one ID is required')
            return
        missing = []
        for number in numbers:
            if number not in entries:
                missing.append(number)
                continue
            try:
                action(number)
            except (ValueError, OSError) as error:
                self.error(str(error))
        if missing:
            self.error('Unknown {} IDs: {}'.format(label, ', '.join(map(str, missing))))

    @wraps(original_break)
    def Break(self, argument, temporary=0):
        location, comma, condition = argument.partition(',')
        filename, colon, line = location.rpartition(':')
        if colon and filename.strip():
            try:
                int(line)
            except ValueError:
                return original_break(self, argument, temporary)
            try:
                filename = ResolveBreakpointFile(filename.strip())
            except AmbiguousBuffer as error:
                self.error(str(error))
                return
            argument = filename + colon + line + comma + condition
        return original_break(self, argument, temporary)

    def Clear(self, argument):
        if not argument.strip() or ':' in argument:
            return original_clear(self, argument)
        import bdb
        entries = {entry.number for entry in bdb.Breakpoint.bpbynumber if entry}
        return ApplyIds(self, argument, entries,
                         lambda number: original_clear(self, str(number)), 'breakpoint')

    def DeleteCheckpoint(self, argument):
        from checkpoints import Manager
        ApplyIds(self, argument, Manager.entries, Manager.Delete, 'checkpoint')

    def Enable(self, argument):
        import bdb
        entries = {entry.number for entry in bdb.Breakpoint.bpbynumber if entry}
        ApplyIds(self, argument, entries,
                  lambda number: original_enable(self, str(number)), 'breakpoint')

    def Disable(self, argument):
        import bdb
        entries = {entry.number for entry in bdb.Breakpoint.bpbynumber if entry}
        ApplyIds(self, argument, entries,
                  lambda number: original_disable(self, str(number)), 'breakpoint')

    Pdb.precmd = Precmd
    Pdb.do__portable_noop = Noop
    Pdb.do_down = Down
    if sys.platform == 'win32':
        Break = ShowBreakpointPath(Break)
        Pdb.do_cl = ShowBreakpointPath(Pdb.do_cl)
        Clear = ShowBreakpointPath(Clear)
        Enable = ShowBreakpointPath(Enable)
        Disable = ShowBreakpointPath(Disable)
    Pdb.do_break = Pdb.do_b = Break
    Pdb.do_clear = Pdb.do_d = Clear
    Pdb.do_enable = Enable
    Pdb.do_disable = Disable
    Pdb.user_call = UserCall
    Pdb.print_stack_entry = StackEntry
    Pdb._portable_show_displays = ShowDisplays
    Pdb.do_display = Display
    Pdb.do_undisplay = Undisplay
    Pdb.do_enable_display = EnableDisplay
    Pdb.do_disable_display = DisableDisplay
    Pdb.do_info = Info
    Pdb.do_print_format = PrintFormat
    Pdb.do_backtrace = Backtrace
    Pdb.do_frame = Frame
    Pdb.do_set = Set
    Pdb.do_checkpoint = Checkpoint
    Pdb.do_restart = Restart
    Pdb.do_delete_checkpoint = DeleteCheckpoint
    InstallCompletion(Pdb)
