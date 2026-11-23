"""Reload project Python imports on an explicit debugger restart."""
import importlib
import importlib.abc
import importlib.machinery
import linecache
from pathlib import Path
import sys
import ast
import bdb
import tokenize
import pdb
import atexit
import signal
import threading


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
            debugger.message('Breakpoint {} disabled: original source cannot be uniquely located in {}. Set it again after reviewing the edit.'.format(breakpoint.number, breakpoint.file))
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
            debugger.message('Breakpoint {} moved: {}:{} -> {}'.format(breakpoint.number, breakpoint.file, previous, line))
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


def Install():
    from IPython.core.debugger import Pdb
    if getattr(Pdb, '_portable_reload', False):
        return
    Pdb._portable_reload = True
    original_run = Pdb.do_run
    original_set_break = Pdb.set_break
    original_interaction = Pdb.interaction

    def Interaction(self, *args, **kwargs):
        timing = getattr(self, '_portable_timing', None)
        if timing is not None:
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
            if timing is not None:
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

    def Wrap(original, module_mode):
        def Execute(self, filename):
            if not hasattr(self, '_portable_initial_modules'):
                self._portable_initial_modules = set(sys.modules)
                self._portable_launch_directory = Path.cwd()
                if module_mode:
                    spec = importlib.util.find_spec(filename)
                    root = Path(spec.origin).resolve().parent if spec and spec.origin else Path.cwd()
                else:
                    root = Path(filename).resolve().parent
                self._portable_project_root = root
            if getattr(self, '_portable_reload_pending', False):
                Refresh(self)
                self._portable_reload_pending = False
                self.message('Reloading saved project source from {}'.format(self._portable_project_root))
            self._portable_execution_active = True
            callbacks = ExitCallbacks()
            callbacks.Start()
            completed = False
            previous_sigint = None
            previous_nosigint = self.nosigint
            timing = None

            def Pause(_signum, frame):
                if self.allow_kbdint or getattr(self, 'quitting', True) or self.botframe is None:
                    raise KeyboardInterrupt
                frames = []
                current = frame
                while current is not None and current is not self.botframe:
                    frames.append(current)
                    current = current.f_back
                if current is None or not frames:
                    raise KeyboardInterrupt
                # Continue can remove tracing altogether. Restore it on the
                # live stack without resetting the debugger's run boundary.
                self.set_step()
                for current in frames:
                    current.f_trace = self.trace_dispatch
                sys.settrace(self.trace_dispatch)

            try:
                if not previous_nosigint and threading.current_thread() is threading.main_thread():
                    previous_sigint = signal.getsignal(signal.SIGINT)
                    if previous_sigint is not None:
                        signal.signal(signal.SIGINT, Pause)
                        # Keep the same handler across continue and step commands.
                        self.nosigint = True
                if getattr(self, '_portable_timing_enabled', False):
                    if sys.getprofile() is None:
                        from function_timing import FunctionTiming
                        timing = FunctionTiming(self._portable_project_root)
                        self._portable_timing = timing
                        timing.profiler.enable()
                    else:
                        self.error('Function timing skipped because another profiler is active.')
                result = original(self, filename)
                completed = True
                return result
            except SystemExit as error:
                # Keep the traceback frames alive while inspecting program exit.
                traceback = error.__traceback__
                self.message('Program requested exit: {!r}. Inspect the saved stack, r to rerun, or q to quit.'.format(error.code))
                self.interaction(None, traceback)
                completed = True
                if getattr(self, '_user_requested_quit', False):
                    return
                raise
            except KeyboardInterrupt as error:
                traceback = error.__traceback__
                self.message('Program interrupted. Inspect the saved stack; use r to restart or q to quit.')
                return self.interaction(None, traceback)
            finally:
                if previous_sigint is not None and signal.getsignal(signal.SIGINT) is Pause:
                    signal.signal(signal.SIGINT, previous_sigint)
                self.nosigint = previous_nosigint
                try:
                    callbacks.Finish(completed)
                finally:
                    self._portable_execution_active = False
                    if timing is not None:
                        if sys.getprofile() is timing.profiler:
                            timing.profiler.disable()
                        self._portable_timing = None
                        if not (getattr(self, '_portable_reload_pending', False)
                                or getattr(self, '_user_requested_quit', False)):
                            rows, entries, project = timing.Snapshot()
                            if rows:
                                report = None
                                try:
                                    report = timing.SaveReport(self._portable_launch_directory, entries, project)
                                except OSError as error:
                                    self.error('Could not write Callgrind report: {}'.format(error))
                                self._portable_last_timing = (rows, report)
                                from function_timing import ShowTiming
                                ShowTiming(self, rows=rows)
        return Execute

    Pdb.do_run = Run
    Pdb.do_rtime = Pdb.do_rt = RunTimed
    Pdb.set_break = SetBreak
    Pdb.interaction = Interaction
    Pdb.do_restart = Run
    Pdb._runscript = Wrap(Pdb._runscript, False)
    if hasattr(Pdb, '_runmodule'):
        Pdb._runmodule = Wrap(Pdb._runmodule, True)
