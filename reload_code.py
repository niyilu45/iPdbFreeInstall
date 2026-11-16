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
    Manager.Cleanup()


def Install():
    from IPython.core.debugger import Pdb
    if getattr(Pdb, '_portable_reload', False):
        return
    Pdb._portable_reload = True
    original_run = Pdb.do_run
    original_set_break = Pdb.set_break
    original_interaction = Pdb.interaction

    def Interaction(self, *args, **kwargs):
        try:
            return original_interaction(self, *args, **kwargs)
        except pdb.Restart:
            # ipdb's post-mortem interaction is inside its exception handler,
            # outside the Restart handler used for normal traced execution.
            if getattr(self, '_portable_execution_active', False) or not hasattr(self, '_portable_project_root'):
                raise
            self.message('Reload requested; leaving post-mortem debugging.')

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

    def Run(self, argument):
        # Keep the live prompt available if a saved edit is temporarily invalid.
        filename = getattr(self, 'mainpyfile', None)
        if filename:
            try:
                compile(''.join(ReadSource(filename)), filename, 'exec')
            except (OSError, SyntaxError, UnicodeError) as error:
                self.error('Cannot reload saved source: {}'.format(error))
                return
        self._portable_reload_pending = True
        return original_run(self, argument)

    def Wrap(original, module_mode):
        def Execute(self, filename):
            if not hasattr(self, '_portable_initial_modules'):
                self._portable_initial_modules = set(sys.modules)
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
            try:
                return original(self, filename)
            except SystemExit as error:
                # Keep the traceback frames alive while inspecting program exit.
                traceback = error.__traceback__
                self.message('Program requested exit: {!r}. Inspect the saved stack, r to rerun, or q to quit.'.format(error.code))
                self.interaction(None, traceback)
                if getattr(self, '_user_requested_quit', False):
                    return
                raise
            finally:
                self._portable_execution_active = False
        return Execute

    Pdb.do_run = Run
    Pdb.set_break = SetBreak
    Pdb.interaction = Interaction
    Pdb.do_restart = Run
    Pdb._runscript = Wrap(Pdb._runscript, False)
    if hasattr(Pdb, '_runmodule'):
        Pdb._runmodule = Wrap(Pdb._runmodule, True)
