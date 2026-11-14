"""Reload project Python imports on an explicit debugger restart."""
import importlib
import importlib.abc
import importlib.machinery
import linecache
from pathlib import Path
import sys


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
    linecache.clearcache()
    from checkpoints import Manager
    Manager.Cleanup()


def Install():
    from IPython.core.debugger import Pdb
    if getattr(Pdb, '_portable_reload', False):
        return
    Pdb._portable_reload = True
    original_run = Pdb.do_run

    def Run(self, argument):
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
            return original(self, filename)
        return Execute

    Pdb.do_run = Run
    Pdb.do_restart = Run
    Pdb._runscript = Wrap(Pdb._runscript, False)
    if hasattr(Pdb, '_runmodule'):
        Pdb._runmodule = Wrap(Pdb._runmodule, True)
