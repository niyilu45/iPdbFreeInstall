"""Context-aware completion for portable debugger commands."""
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
            candidate = path
            if not os.path.isabs(text):
                try:
                    candidate = os.path.relpath(path)
                except ValueError:
                    pass  # Windows cannot make a path relative across drives.
            if os.altsep and os.altsep in text and os.sep not in text:
                candidate = candidate.replace(os.sep, os.altsep)
            if candidate.startswith(text) or (not os.path.dirname(text) and os.path.basename(path).startswith(text)):
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


def StringMatches(debugger, line, begin, end):
    quote = None
    start = 0
    escaped = False
    for index, character in enumerate(line[:end]):
        if escaped:
            escaped = False
        elif character == chr(92) and quote:
            escaped = True
        elif quote:
            if character == quote:
                quote = None
        elif character in ("'", '"'):
            quote, start = character, index + 1
    if quote is None:
        return None
    prefix = line[start:end]
    frame = getattr(debugger, 'curframe', None)
    namespace = {}
    if frame is not None:
        namespace.update(frame.f_globals)
        namespace.update(frame.f_locals)
    values = set()
    # Dictionary indexing should complete that dictionary's keys only.
    lookup = re.search(r'([A-Za-z_]\w*)\[\s*$', line[:start - 1])
    target = namespace.get(lookup.group(1)) if lookup else None
    if type(target) is dict:
        values.update(key for key in target if isinstance(key, str))
    else:
        for value in namespace.values():
            if isinstance(value, str):
                values.add(value)
            elif type(value) is dict:
                values.update(key for key in value if isinstance(key, str))
        values.update(BufferMatches(prefix))
        directory, name = os.path.split(os.path.expanduser(prefix))
        try:
            with os.scandir(directory or '.') as entries:
                for entry in entries:
                    if entry.name.startswith(name):
                        head = prefix[:len(prefix) - len(name)] if name else prefix
                        values.add(head + entry.name + ('/' if entry.is_dir() else ''))
        except OSError:
            pass
    candidates = []
    for value in values:
        encoded = value.replace(chr(92), chr(92) * 2).replace(quote, chr(92) + quote)
        encoded = encoded.replace(chr(10), chr(92) + 'n').replace(chr(13), chr(92) + 'r').replace(chr(9), chr(92) + 't')
        if encoded.startswith(prefix):
            full = line[:start] + encoded
            candidates.append(full[begin:])
    return sorted(set(candidates))


def Matches(debugger, line, begin, end):
    from gdb_commands import COMMANDS, ALIASES, Resolve
    strings = StringMatches(debugger, line, begin, end)
    if strings is not None:
        return strings
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
        'info': ('breakpoints', 'locals', 'args', 'checkpoints', 'display', 'timing'),
        'delete': ('breakpoints', 'checkpoint', 'display'),
        'enable': ('breakpoints', 'display'),
        'disable': ('breakpoints', 'display'),
        'set': ('variable',),
        'help': COMMANDS,
    }
    if command in choices and not argument_before_word:
        return [name for name in choices[command] if name.startswith(text)]
    if command in ('break', 'tbreak', 'clear', 'source') and not re.search(r'\sif\s', before):
        path = before[command_end:].lstrip()
        path_start = end - len(path)
        files = BufferMatches(path)
        if files:
            if begin < path_start:
                return [line[begin:path_start] + candidate for candidate in files]
            fixed = line[path_start:begin]
            return [candidate[len(fixed):] for candidate in files if candidate.startswith(fixed)]
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


def CommonMatch(text, candidates):
    common = os.path.commonprefix(candidates)
    if common != text and (common.startswith(text) or
                           len(candidates) == 1 and common in BufferMatches(text)):
        return common
    return None


def InstallPromptCompletion():
    from IPython.terminal.debugger import TerminalPdb
    from prompt_toolkit.completion import Completer, Completion

    original_init = TerminalPdb.pt_init

    class FrameCompleter(Completer):
        def __init__(self, debugger, native):
            self.debugger = debugger
            self.native = native

        def get_completions(self, document, complete_event):
            if not complete_event.completion_requested:
                return
            end = document.cursor_position
            text = document.get_word_before_cursor(WORD=True)
            begin = end - len(text)
            candidates = Matches(self.debugger, document.text, begin, end)
            if candidates is None:
                if self.native is not None:
                    yield from self.native.get_completions(document, complete_event)
                return
            common = CommonMatch(text, candidates)
            if common is not None:
                yield Completion(common, start_position=-len(text))

    def Init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        session = getattr(self, 'pt_app', None)
        if session is not None:
            # IPython still updates its original completer with the selected frame.
            session.completer = FrameCompleter(self, session.completer)
            session.complete_while_typing = False

    TerminalPdb.pt_init = Init


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
                    common = CommonMatch(text, candidates)
                    self._portable_matches = [common] if common is not None else []
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
    InstallPromptCompletion()
