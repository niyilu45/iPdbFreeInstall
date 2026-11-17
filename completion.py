"""Context-aware readline completion for portable debugger commands."""
import builtins
import inspect
import re
import os
import json


def BufferMatches(text):
    state = os.environ.get('IPDB_VIM_STATE')
    if not state:
        return []
    try:
        with open(state + '.buffers', encoding='utf-8') as stream:
            files = json.load(stream)
        matches = []
        for path in files:
            if not isinstance(path, str) or not path.endswith('.py'):
                continue
            relative = os.path.relpath(path)
            candidate = path if os.path.isabs(text) else relative
            if candidate.startswith(text) or os.path.basename(path).startswith(text):
                matches.append(candidate)
        return sorted(set(matches))
    except (OSError, ValueError, TypeError):
        return []


def ExpressionMatches(debugger, prefix):
    frame = getattr(debugger, 'curframe', None)
    namespace = dict(vars(builtins))
    if frame is not None:
        namespace.update(frame.f_globals)
        namespace.update(frame.f_locals)
    if '.' not in prefix:
        return sorted(name for name in namespace if isinstance(name, str) and name.startswith(prefix))
    parts = prefix.split('.')
    if parts[0] not in namespace:
        return []
    value = namespace[parts[0]]
    try:
        for name in parts[1:-1]:
            value = inspect.getattr_static(value, name)
            if isinstance(value, property):
                return []
        head = '.'.join(parts[:-1]) + '.'
        return sorted(head + name for name in dir(value) if name.startswith(parts[-1]))
    except Exception:
        return []


def Matches(debugger, line, begin, end):
    from gdb_commands import COMMANDS, ALIASES, Resolve
    before = line[:end]
    stripped = before.lstrip()
    start = len(before) - len(stripped)
    parts = stripped.split(None, 1)
    text = line[begin:end]
    if not parts or (len(parts) == 1 and not stripped.endswith((' ', '\t'))):
        if stripped.startswith('!'):
            pass
        else:
            if text in ALIASES:
                return [ALIASES[text]]
            native = {name[3:] for name in dir(debugger) if name.startswith('do_') and not name[3:].startswith('_')}
            native -= {'down', 'enable_display', 'disable_display', 'delete_checkpoint'}
            return sorted({name for name in set(COMMANDS) | native if name.startswith(text)} | set(ExpressionMatches(debugger, text)))
    command = parts[0] if parts else ''
    try:
        command = Resolve(command, COMMANDS, ALIASES)
    except ValueError:
        return []
    command_end = start + len(parts[0]) if parts else start
    argument_before_word = line[command_end:begin].strip()
    choices = {
        'info': ('breakpoints', 'locals', 'args', 'checkpoints', 'display'),
        'delete': ('breakpoints', 'checkpoint', 'display'),
        'enable': ('breakpoints', 'display'),
        'disable': ('breakpoints', 'display'),
        'set': ('variable',),
        'help': COMMANDS,
    }
    if command in choices and not argument_before_word:
        return [name for name in choices[command] if name.startswith(text)]
    if command in ('break', 'tbreak', 'clear', 'source') and not re.search(r'\sif\s', before):
        files = BufferMatches(text)
        if files:
            return files
        return None  # Retain pdb's filename/line completion where available.
    match = re.search(r'[\w.]+$', before, re.UNICODE)
    prefix = match.group() if match else ''
    prefix_start = match.start() if match else end
    if begin < prefix_start:
        # Preserve ! or another delimiter that readline includes in its token.
        lead = line[begin:prefix_start]
        return [lead + candidate for candidate in ExpressionMatches(debugger, prefix)]
    trim = begin - prefix_start
    return [candidate[trim:] for candidate in ExpressionMatches(debugger, prefix)]


def InstallCompletion(debugger_class):
    original = debugger_class.complete
    original_cmdloop = debugger_class.cmdloop

    def Complete(self, text, state):
        if state == 0:
            try:
                import readline
                candidates = Matches(self, readline.get_line_buffer(), readline.get_begidx(), readline.get_endidx())
                if candidates is None:
                    self._portable_matches = None
                else:
                    # Expose only the unambiguous extension. Readline must never
                    # receive the underlying variable list, even on repeated Tab.
                    common = os.path.commonprefix(candidates)
                    self._portable_matches = [common] if common.startswith(text) and len(common) > len(text) else []
                    if len(candidates) == 1 and candidates[0] in BufferMatches(text) and common != text:
                        self._portable_matches = [common]
            except Exception:
                self._portable_matches = []
        candidates = getattr(self, '_portable_matches', None)
        if candidates is None:
            return original(self, text, state)
        return candidates[state] if state < len(candidates) else None

    def CommandLoop(self, intro=None):
        # IPython core Pdb defaults completekey to None. Binding Tab alone
        # does not install self.complete in cmd.Cmd's readline input loop.
        if self.completekey is None:
            self.completekey = 'tab'
        return original_cmdloop(self, intro)

    debugger_class.complete = Complete
    debugger_class.cmdloop = CommandLoop
