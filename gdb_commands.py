"""GDB-style command adapters for IPython's Python debugger."""
import re


def Translate(line):
    stripped = line.strip()
    if not stripped or stripped.startswith('!'):
        return line
    command, _, argument = stripped.partition(' ')
    argument = argument.strip()
    aliases = {'bt': 'backtrace', 'finish': 'return',
               'fin': 'return', 'print': 'p', 'cont': 'continue', 'r': 'run',
               'f': 'frame', 'i': 'info'}
    command = aliases.get(command, command)
    if command in ('break', 'b', 'tbreak'):
        parts = re.split(r'\s+if\s+', argument, maxsplit=1)
        if len(parts) == 2:
            argument = parts[0] + ', ' + parts[1]
    if command == 'delete':
        if argument.startswith('checkpoint '):
            command, argument = 'delete_checkpoint', argument[len('checkpoint '):]
        else:
            command = 'clear'
    return command + (' ' + argument if argument else '')


def Install():
    from IPython.core.debugger import Pdb
    if getattr(Pdb, '_portable_gdb', False):
        return
    Pdb._portable_gdb = True
    original = Pdb.precmd
    original_restart = Pdb.do_restart

    def Precmd(self, line):
        return original(self, Translate(line))

    def Info(self, argument):
        """info breakpoints|locals|args|checkpoints"""
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
        self.error('Usage: info breakpoints|locals|args|checkpoints')

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
    Pdb.do_info = Info
    Pdb.do_backtrace = Backtrace
    Pdb.do_frame = Frame
    Pdb.do_set = Set
    Pdb.do_checkpoint = Checkpoint
    Pdb.do_restart = Restart
    Pdb.do_delete_checkpoint = DeleteCheckpoint
