"""Keep Matplotlib GUI events on the main thread while the debugger is idle."""
import ctypes
import cmd
import importlib.abc
import importlib.machinery
import os
import sys
import threading


def Pump():
    if threading.current_thread() is not threading.main_thread():
        return
    helpers = sys.modules.get('matplotlib._pylab_helpers')
    if helpers is None:
        return
    for manager in list(helpers.Gcf.get_all_fig_managers()):
        try:
            canvas = manager.canvas
            if canvas.figure.stale:
                canvas.draw_idle()
            canvas.flush_events()
        except Exception as error:
            if not getattr(manager, '_portable_event_error', False):
                manager._portable_event_error = True
                print('[ipdb] Figure event processing failed: {}'.format(error), file=sys.stderr)


def PatchPyplot(module):
    if getattr(module.show, '_portable_nonblocking', False) is True:
        return
    original = module.show
    def Show(*args, **kwargs):
        if not args and 'block' not in kwargs:
            kwargs['block'] = False
        return original(*args, **kwargs)
    Show._portable_nonblocking = True
    Show.__doc__ = original.__doc__
    module.show = Show


class PlotLoader(importlib.abc.Loader):
    def __init__(self, original):
        self.original = original

    def create_module(self, spec):
        return self.original.create_module(spec) if hasattr(self.original, 'create_module') else None

    def exec_module(self, module):
        self.original.exec_module(module)
        PatchPyplot(module)


class PlotFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'matplotlib.pyplot':
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None and hasattr(spec.loader, 'exec_module'):
            spec.loader = PlotLoader(spec.loader)
        return spec


def GuiPrompt(prompt):
    result = []
    def Read():
        try:
            result.append((True, prompt()))
        except BaseException as error:
            result.append((False, error))
    worker = threading.Thread(target=Read, daemon=True)
    worker.start()
    interrupted = False
    while worker.is_alive():
        try:
            Pump()
            worker.join(0.03)
        except KeyboardInterrupt:
            if not interrupted:
                print('[ipdb] Input interrupted. Press Enter if the prompt is still waiting.', file=sys.stderr)
            interrupted = True
    if interrupted:
        raise KeyboardInterrupt
    success, value = result[0]
    if not success:
        raise value
    return value


def Install():
    if os.environ.get('IPDB_PLOT_EVENTS') == '0':
        return
    from IPython.core.debugger import Pdb
    if getattr(Pdb, '_portable_plot_events', False):
        return
    Pdb._portable_plot_events = True
    sys.meta_path.insert(0, PlotFinder())
    if 'matplotlib.pyplot' in sys.modules:
        PatchPyplot(sys.modules['matplotlib.pyplot'])
    original = Pdb.cmdloop

    @ctypes.CFUNCTYPE(ctypes.c_int)
    def InputHook():
        try:
            Pump()
        except BaseException:
            pass
        return 0

    def CommandLoop(self, intro=None):
        if 'matplotlib.pyplot' not in sys.modules or threading.current_thread() is not threading.main_thread():
            return original(self, intro)
        Pump()
        if sys.platform == 'win32' or not sys.stdin.isatty():
            import builtins
            missing = object()
            previous_input = getattr(cmd, 'input', missing)
            reader = builtins.input if previous_input is missing else previous_input
            cmd.input = lambda prompt='': GuiPrompt(lambda: reader(prompt))
            try:
                return original(self, intro)
            finally:
                if previous_input is missing:
                    del cmd.input
                else:
                    cmd.input = previous_input
        try:
            slot = ctypes.c_void_p.in_dll(ctypes.pythonapi, 'PyOS_InputHook')
        except ValueError:
            return original(self, intro)
        previous = slot.value
        address = ctypes.cast(InputHook, ctypes.c_void_p).value
        slot.value = address
        try:
            return original(self, intro)
        finally:
            if slot.value == address:
                slot.value = previous
    Pdb.cmdloop = CommandLoop

    from IPython.terminal.debugger import TerminalPdb
    original_terminal = TerminalPdb.cmdloop
    def TerminalLoop(self, intro=None):
        if 'matplotlib.pyplot' not in sys.modules or threading.current_thread() is not threading.main_thread():
            return original_terminal(self, intro)
        self.preloop()
        if intro:
            self.stdout.write(str(intro) + '\n')
        stop = False
        while not stop:
            if self.cmdqueue:
                line = self.cmdqueue.pop(0)
            else:
                if getattr(self, '_ptcomp', None) is not None:
                    self._ptcomp.ipy_completer.namespace = self.curframe_locals
                    self._ptcomp.ipy_completer.global_namespace = self.curframe.f_globals
                if getattr(self, 'pt_app', None) is not None:
                    prompt = getattr(self, '_prompt', None) or self.pt_app.prompt
                else:
                    prompt = lambda: input(self.prompt)
                try:
                    line = GuiPrompt(prompt)
                except EOFError:
                    line = 'EOF'
            line = self.precmd(line)
            stop = self.postcmd(self.onecmd(line), line)
        self.postloop()
    TerminalPdb.cmdloop = TerminalLoop
