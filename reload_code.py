"""Reload project Python imports on an explicit debugger restart."""
import importlib
import importlib.abc
import importlib.machinery
from contextlib import contextmanager, ExitStack
import linecache
import os
from pathlib import Path
import sys
import ast
import bdb
import tokenize
import pdb
import atexit
import signal
import threading
from traceback import format_exception_only


class ExitCallbacks:
    """Keep each debuggee run's exit callbacks separate from the launcher."""
    def __init__(self):
        self.entries = []
        self.register = atexit.register
        self.unregister = atexit.unregister

    def Register(self, function, *args, **kwargs):
        self.entries.append((function, args, kwargs))
        return function

    def Unregister(self, function):
        self.entries[:] = [entry for entry in self.entries if entry[0] != function]
        self.unregister(function)

    def Start(self):
        atexit.register = self.Register
        atexit.unregister = self.Unregister

    def Finish(self, execute):
        try:
            if execute:
                while self.entries:
                    function, args, kwargs = self.entries.pop()
                    try:
                        function(*args, **kwargs)
                    except BaseException:
                        import traceback
                        traceback.print_exc()
        finally:
            self.entries.clear()
            atexit.register = self.register
            atexit.unregister = self.unregister


def ReadSource(filename):
    with tokenize.open(filename) as stream:
        return stream.read().splitlines(True)


def DisplayPath(filename):
    """Recover filesystem casing for Windows paths in debugger output."""
    if sys.platform != 'win32' or filename.startswith('<'):
        return filename
    try:
        resolved = str(Path(filename).resolve(strict=True))
        if os.path.normcase(resolved) == os.path.normcase(os.path.abspath(filename)):
            return resolved if os.path.isabs(filename) else os.path.relpath(resolved)
    except (OSError, RuntimeError, ValueError):
        pass
    return filename


def SyntaxLocation(error):
    filename = error.filename
    if isinstance(filename, str) and Path(filename).is_file():
        return filename, max(1, error.lineno or 1)


def Relocate(old, new, line):
    """Only retain a location when the original statement can be identified."""
    if not 1 <= line <= len(old):
        return None
    if old == new:
        return line
    def Scopes(lines):
        result = {}
        tree = ast.parse(''.join(lines))
        def Visit(node, scope):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope = scope + (node.name,)
            if hasattr(node, 'lineno'):
                result[node.lineno] = scope
            for child in ast.iter_child_nodes(node):
                Visit(child, scope)
        Visit(tree, ())
        return result
    old_scopes, new_scopes = Scopes(old), Scopes(new)
    target = old[line - 1].strip()
    candidates = [index + 1 for index, text in enumerate(new)
                  if text.strip() == target and new_scopes.get(index + 1) == old_scopes.get(line)]
    originals = [index + 1 for index, text in enumerate(old)
                 if text.strip() == target and old_scopes.get(index + 1) == old_scopes.get(line)]
    if len(candidates) == 1 and len(originals) > 1:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Repeated statements require matching neighboring source as evidence.
    matches = []
    for candidate in candidates:
        score = sum(1 for offset in (-2, -1, 1, 2)
                    if 0 <= line - 1 + offset < len(old)
                    and 0 <= candidate - 1 + offset < len(new)
                    and old[line - 1 + offset].strip()
                    and old[line - 1 + offset].strip() == new[candidate - 1 + offset].strip())
        matches.append((score, candidate))
    matches.sort(reverse=True)
    if matches and matches[0][0] >= 2 and (len(matches) == 1 or matches[0][0] > matches[1][0]):
        return matches[0][1]
    return None


def RefreshBreakpoints(debugger):
    for breakpoint in bdb.Breakpoint.bpbynumber[1:]:
        if breakpoint is None or not hasattr(breakpoint, '_portable_source'):
            continue
        try:
            new = ReadSource(breakpoint.file)
            line = Relocate(breakpoint._portable_source, new, breakpoint.line)
        except (OSError, SyntaxError, UnicodeError):
            line = None
        if line is None:
            breakpoint.disable()
            debugger.message('Breakpoint {} disabled: original source cannot be uniquely located in {}. Set it again after reviewing the edit.'.format(breakpoint.number, DisplayPath(breakpoint.file)))
            continue
        if line != breakpoint.line:
            previous = breakpoint.line
            key = (breakpoint.file, previous)
            bdb.Breakpoint.bplist[key].remove(breakpoint)
            if not bdb.Breakpoint.bplist[key]:
                del bdb.Breakpoint.bplist[key]
            breakpoint.line = line
            breakpoint.func_first_executable_lineno = None
            bdb.Breakpoint.bplist.setdefault((breakpoint.file, line), []).append(breakpoint)
            debugger.message('Breakpoint {} moved: {}:{} -> {}'.format(breakpoint.number, DisplayPath(breakpoint.file), previous, line))
        breakpoint._portable_source = new
    debugger.breaks = {}
    for filename, line in bdb.Breakpoint.bplist:
        debugger.breaks.setdefault(filename, []).append(line)


def Within(filename, root):
    try:
        Path(filename).resolve().relative_to(root)
        return True
    except (ValueError, TypeError):
        return False


class FreshSource(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        # Avoid timestamp/size collisions when a file is saved twice in a second.
        return self.source_to_code(self.get_data(self.path), self.path)


class ProjectFinder(importlib.abc.MetaPathFinder):
    def __init__(self, root, protected):
        self.root, self.protected = root, protected

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.protected:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and isinstance(spec.loader, importlib.machinery.SourceFileLoader) and Within(spec.origin, self.root):
            spec.loader = FreshSource(fullname, spec.origin)
            return spec
        return None


def Refresh(debugger):
    pyplot = sys.modules.get('matplotlib.pyplot')
    if pyplot is not None and getattr(pyplot.show, '_portable_nonblocking', False) is True:
        # Old GUI timers must not call into the reset __main__ namespace.
        pyplot.close('all')
    root = debugger._portable_project_root
    protected = debugger._portable_initial_modules
    removed = []
    for name, module in list(sys.modules.items()):
        if name in protected or module is None:
            continue
        filename = getattr(module, '__file__', None)
        if filename and Within(filename, root) and str(filename).endswith(('.py', '.pyc')):
            removed.append(name)
    for name in sorted(removed, key=lambda name: name.count('.'), reverse=True):
        module = sys.modules.pop(name, None)
        parent, separator, child = name.rpartition('.')
        if separator and getattr(sys.modules.get(parent), child, None) is module:
            delattr(sys.modules[parent], child)
    previous = getattr(debugger, '_portable_project_finder', None)
    if previous in sys.meta_path:
        sys.meta_path.remove(previous)
    finder = ProjectFinder(root, protected)
    debugger._portable_project_finder = finder
    sys.meta_path.insert(0, finder)
    importlib.invalidate_caches()
    RefreshBreakpoints(debugger)
    linecache.clearcache()
    from checkpoints import Manager
    Manager.Reset()


def CancelRepeats(debugger):
    debugger._portable_resume = None
    debugger.lastcmd = ''
    debugger.cmdqueue[:] = [command for command in debugger.cmdqueue if command.strip()]


@contextmanager
def DebuggerInterrupts(debugger):
    previous = None
    nosigint = debugger.nosigint

    def Pause(_signum, frame):
        CancelRepeats(debugger)
        if debugger.allow_kbdint or getattr(debugger, 'quitting', True) or debugger.botframe is None:
            raise KeyboardInterrupt
        frames = []
        current = frame
        while current is not None and current is not debugger.botframe:
            frames.append(current)
            current = current.f_back
        if current is None or not frames:
            raise KeyboardInterrupt
        # Continue can remove tracing. Bring it back on the live stack.
        debugger.set_step()
        for current in frames:
            current.f_trace = debugger.trace_dispatch
        sys.settrace(debugger.trace_dispatch)

    if not nosigint and threading.current_thread() is threading.main_thread():
        previous = signal.getsignal(signal.SIGINT)
        if previous is not None:
            signal.signal(signal.SIGINT, Pause)
            debugger.nosigint = True
    try:
        yield
    finally:
        if previous is not None and signal.getsignal(signal.SIGINT) is Pause:
            signal.signal(signal.SIGINT, previous)
        debugger.nosigint = nosigint


def Install():
    from IPython.core.debugger import Pdb
    if getattr(Pdb, '_portable_reload', False):
        return
    Pdb._portable_reload = True
    original_run = Pdb.do_run
    original_set_break = Pdb.set_break
    original_interaction = Pdb.interaction
    original_cmdloop = Pdb._cmdloop
    original_exception = Pdb.user_exception
    original_sigint = Pdb.sigint_handler

    def Sigint(self, signum, frame):
        CancelRepeats(self)
        return original_sigint(self, signum, frame)

    def UserException(self, frame, exc_info):
        self._portable_resume = None
        return original_exception(self, frame, exc_info)

    def Interaction(self, *args, **kwargs):
        timing = getattr(self, '_portable_timing', None)
        owns_pause = timing is not None and timing.paused_at is None
        if owns_pause:
            timing.Pause()
        try:
            return original_interaction(self, *args, **kwargs)
        except pdb.Restart:
            # ipdb's post-mortem interaction is inside its exception handler,
            # outside the Restart handler used for normal traced execution.
            if getattr(self, '_portable_execution_active', False) or not hasattr(self, '_portable_project_root'):
                raise
            self.message('Reload requested; leaving post-mortem debugging.')
        finally:
            if owns_pause:
                timing.Resume()

    def InteractiveCommands(self):
        prompt = self.cmdloop
        previous = vars(self).get('cmdloop')

        def Read(*args, **kwargs):
            try:
                return prompt(*args, **kwargs)
            except KeyboardInterrupt:
                CancelRepeats(self)
                raise

        self.cmdloop = Read
        try:
            return original_cmdloop(self)
        finally:
            if previous is None:
                del self.cmdloop
            else:
                self.cmdloop = previous

    def CommandLoop(self):
        # Counted resume advances through actual debugger stops without faking keystrokes.
        allow_kbdint = self.allow_kbdint
        self.allow_kbdint = True
        try:
            repeat = getattr(self, '_portable_resume', None)
            if repeat is not None:
                command, remaining = repeat
                self._portable_resume = (command, remaining - 1) if remaining > 1 else None
                command(self, '')
                return
        except KeyboardInterrupt:
            CancelRepeats(self)
            self.message('--KeyboardInterrupt--')
        finally:
            self.allow_kbdint = allow_kbdint
        timing = getattr(self, '_portable_timing', None)
        if timing is None:
            return InteractiveCommands(self)
        from function_timing import UpdateTimingReport
        # Breakpoints with command lists can enter the prompt without interaction().
        owns_pause = timing.paused_at is None
        if owns_pause:
            timing.Pause()
        try:
            UpdateTimingReport(self, timing, allow_empty=timing.seen_prompt, announce=True)
            timing.seen_prompt = True
            return InteractiveCommands(self)
        finally:
            if owns_pause:
                timing.Resume()

    def SetBreak(self, filename, *args, **kwargs):
        before = bdb.Breakpoint.next
        result = original_set_break(self, filename, *args, **kwargs)
        for breakpoint in bdb.Breakpoint.bpbynumber[before:]:
            if breakpoint is not None:
                try:
                    cached = linecache.getlines(breakpoint.file)
                    breakpoint._portable_source = list(cached) if cached else ReadSource(breakpoint.file)
                except (OSError, UnicodeError):
                    pass
        return result

    def Restart(self, argument, timing):
        # Keep the live prompt available if a saved edit is temporarily invalid.
        filename = getattr(self, 'mainpyfile', None)
        if filename:
            try:
                compile(''.join(ReadSource(filename)), filename, 'exec')
            except (OSError, SyntaxError, UnicodeError) as error:
                self.error('Cannot reload saved source: {}'.format(error))
                sync = getattr(self, '_portable_vim_sync', None)
                location = SyntaxLocation(error) if isinstance(error, SyntaxError) else None
                if sync is not None and location is not None:
                    if hasattr(self, '_portable_syntax_location'):
                        self._portable_syntax_location = location
                    sync(*location)
                return
        try:
            return original_run(self, argument)
        except pdb.Restart:
            self._portable_reload_pending = True
            self._portable_timing_enabled = timing
            raise

    def Run(self, argument):
        """run [args...]: restart from saved source with function timing off."""
        return Restart(self, argument, False)

    def RunTimed(self, argument):
        """rtime [args...]: restart with function timing on. Alias: rt."""
        return Restart(self, argument, True)

    def ModuleFilename(name):
        spec = importlib.util.find_spec(name)
        return spec.origin if spec else None

    def Wrap(original, source_filename):
        def Execute(self, target):
            first_run = not hasattr(self, '_portable_initial_modules')
            if first_run:
                self._portable_initial_modules = set(sys.modules)
                self._portable_launch_directory = Path.cwd()
                self._portable_project_root = self._portable_launch_directory
            if getattr(self, '_portable_reload_pending', False):
                # Modern pdb module targets cache their code on first access.
                # Refresh before resolving the new target in the native runner.
                Refresh(self)
                self._portable_reload_pending = False
                self.message('Reloading saved project source from {}'.format(self._portable_project_root))
            self._portable_execution_active = True
            callbacks = ExitCallbacks()
            callbacks.Start()
            completed = False
            self._portable_resume = None
            interrupts = ExitStack()
            timing = None

            try:
                if first_run:
                    filename = source_filename(target)
                    if filename:
                        self._portable_project_root = Path(filename).resolve().parent
                interrupts.enter_context(DebuggerInterrupts(self))
                if getattr(self, '_portable_timing_enabled', False):
                    from function_timing import FunctionTiming
                    candidate = FunctionTiming(self._portable_project_root)
                    if candidate.Start():
                        timing = self._portable_timing = candidate
                    else:
                        self.error('Function timing skipped because another profiler is active.')
                result = original(self, target)
                completed = True
                return result
            except SyntaxError as error:
                self._portable_resume = None
                location = SyntaxLocation(error)
                if location is None:
                    raise
                if not getattr(self, 'mainpyfile', None):
                    self.mainpyfile = self.canonic(location[0])
                    self._portable_project_root = Path(location[0]).resolve().parent
                if not hasattr(self, 'botframe'):
                    self.reset()
                self._user_requested_quit = False
                self._portable_syntax_location = location
                try:
                    self.message(''.join(format_exception_only(type(error), error)).rstrip())
                    self.message('Fix the source and use r to retry, or q to quit.')
                    return self.interaction(None, error.__traceback__)
                finally:
                    del self._portable_syntax_location
            except SystemExit as error:
                self._portable_resume = None
                # Keep the traceback frames alive while inspecting program exit.
                traceback = error.__traceback__
                self.message('Program requested exit: {!r}. Inspect the saved stack, r to rerun, or q to quit.'.format(error.code))
                self.interaction(None, traceback)
                completed = True
                if getattr(self, '_user_requested_quit', False):
                    return
                raise
            except KeyboardInterrupt as error:
                CancelRepeats(self)
                traceback = error.__traceback__
                self.message('Program interrupted. Inspect the saved stack; use r to restart or q to quit.')
                return self.interaction(None, traceback)
            finally:
                interrupts.close()
                try:
                    callbacks.Finish(completed)
                finally:
                    self._portable_execution_active = False
                    if timing is not None:
                        timing.Stop()
                        self._portable_timing = None
                        if not (getattr(self, '_portable_reload_pending', False)
                                or getattr(self, '_user_requested_quit', False)):
                            from function_timing import ShowTiming, UpdateTimingReport
                            rows = UpdateTimingReport(self, timing, allow_empty=timing.report is not None)
                            if rows:
                                self._portable_last_timing = (rows, timing.report)
                                ShowTiming(self, rows=rows)
        return Execute

    Pdb.do_run = Run
    Pdb.do_rtime = Pdb.do_rt = RunTimed
    Pdb.set_break = SetBreak
    Pdb.interaction = Interaction
    Pdb.user_exception = UserException
    Pdb.sigint_handler = Sigint
    Pdb._cmdloop = CommandLoop
    Pdb.do_restart = Run
    if hasattr(Pdb, '_run'):
        Pdb._run = Wrap(Pdb._run, lambda target: target.filename)
    else:
        Pdb._runscript = Wrap(Pdb._runscript, lambda filename: filename)
        if hasattr(Pdb, '_runmodule'):
            Pdb._runmodule = Wrap(Pdb._runmodule, ModuleFilename)
