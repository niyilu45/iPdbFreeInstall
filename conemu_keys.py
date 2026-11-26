"""Route pane movement while a script is running in a ConEmu debugger pane."""
import ctypes
from ctypes import wintypes
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
import os
import subprocess
import sys
import threading
import time
import uuid


DIRECTIONS = {
    ord('H'): (-1, 0), 0x25: (-1, 0),
    ord('J'): (0, 1), 0x28: (0, 1),
    ord('K'): (0, -1), 0x26: (0, -1),
    ord('L'): (1, 0), 0x27: (1, 0),
}
MODIFIERS = {0x10, 0x11, 0x12, 0x5b, 0x5c, *range(0xa0, 0xa6)}
PROMPT_EVENT = 'IPDB_CONEMU_PROMPT_EVENT'
WM_QUIT = 0x0012


class RunningPaneChord:
    def __init__(self):
        self.deadline = 0
        self.consumed = set()

    def Feed(self, key, down, control, blocked, active, prompt, now):
        if not active:
            self.deadline = 0
        if key in self.consumed:
            if not down:
                self.consumed.remove(key)
            return True, None
        if not active or not down or key in MODIFIERS:
            return False, None
        if key == ord('W') and control and not blocked and not prompt:
            self.deadline = now + 1
            self.consumed.add(key)
            return True, None
        direction = DIRECTIONS.get(key) if now < self.deadline and not blocked else None
        self.deadline = 0
        if direction is None:
            return False, None
        self.consumed.add(key)
        return True, direction


def WinApi(dll, name, result, *arguments):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = arguments
    return function


def ConsoleWindow(kernel):
    # ConEmuHk redirects GetConsoleWindow to its canvas. Its loaded DLL
    # exports the actual console handle used by OnActiveConWndStore.
    bitness = '64' if ctypes.sizeof(ctypes.c_void_p) == 8 else ''
    hook = WinApi(kernel, 'GetModuleHandleW', wintypes.HMODULE, wintypes.LPCWSTR)(
        'ConEmuHk' + bitness + '.dll')
    if hook:
        return WinApi(ctypes.WinDLL(None, handle=hook), 'GetRealConsoleWindow', wintypes.HWND)()
    return WinApi(kernel, 'GetConsoleWindow', wintypes.HWND)()


class ActivePane:
    def __init__(self, user, kernel, window):
        class GuiInfo(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('flags', wintypes.DWORD),
                        ('active', wintypes.HWND), ('focus', wintypes.HWND),
                        ('capture', wintypes.HWND), ('menu', wintypes.HWND),
                        ('move', wintypes.HWND), ('caret', wintypes.HWND), ('rect', wintypes.RECT)]

        self.window = window
        self.console = ConsoleWindow(kernel)
        self.foreground = WinApi(user, 'GetForegroundWindow', wintypes.HWND)
        get_long = 'GetWindowLongPtrW' if ctypes.sizeof(ctypes.c_void_p) == 8 else 'GetWindowLongW'
        self.window_data = WinApi(user, get_long, ctypes.c_void_p, wintypes.HWND, ctypes.c_int)
        self.info_type = GuiInfo
        self.gui_info = WinApi(user, 'GetGUIThreadInfo', wintypes.BOOL, wintypes.DWORD, ctypes.POINTER(GuiInfo))
        if not self.console or not WinApi(user, 'IsWindow', wintypes.BOOL, wintypes.HWND)(window):
            raise RuntimeError('ConEmu did not identify the debugger console')

    def __call__(self):
        # ConEmu's OnActiveConWndStore publishes the active console in GWLP_USERDATA.
        if self.foreground() != self.window or self.window_data(self.window, -21) != self.console:
            return False
        info = self.info_type(size=ctypes.sizeof(self.info_type))
        return self.gui_info(0, ctypes.byref(info)) and info.focus == self.window and not (info.flags & 0x1e)


class KeyboardHook:
    def __init__(self, user, kernel):
        self.kernel = kernel
        result = ctypes.c_ssize_t
        self.callback_type = ctypes.WINFUNCTYPE(result, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        self.install = WinApi(user, 'SetWindowsHookExW', wintypes.HANDLE, ctypes.c_int, self.callback_type,
                              wintypes.HINSTANCE, wintypes.DWORD)
        self.uninstall = WinApi(user, 'UnhookWindowsHookEx', wintypes.BOOL, wintypes.HANDLE)
        self.next = WinApi(user, 'CallNextHookEx', result, wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        self.post = WinApi(user, 'PostThreadMessageW', wintypes.BOOL, wintypes.DWORD, wintypes.UINT,
                           wintypes.WPARAM, wintypes.LPARAM)
        self.get = WinApi(user, 'GetMessageW', ctypes.c_int, ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                          wintypes.UINT, wintypes.UINT)
        self.peek = WinApi(user, 'PeekMessageW', wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                           wintypes.UINT, wintypes.UINT, wintypes.UINT)

    @contextmanager
    def Listen(self, feed):
        def OnKey(code, message, data):
            if code == 0 and message in (0x100, 0x101, 0x104, 0x105):
                key = ctypes.cast(data, ctypes.POINTER(wintypes.DWORD)).contents.value
                if feed(key, message in (0x100, 0x104)):
                    return 1
            return self.next(None, code, message, data)

        ready = threading.Event()
        stopping = threading.Event()
        error = None
        thread_id = None

        def Run():
            nonlocal error, thread_id
            callback = self.callback_type(OnKey)
            module = WinApi(self.kernel, 'GetModuleHandleW', wintypes.HMODULE, wintypes.LPCWSTR)(None)
            thread_id = WinApi(self.kernel, 'GetCurrentThreadId', wintypes.DWORD)()
            message = wintypes.MSG()
            self.peek(ctypes.byref(message), None, 0, 0, 0)  # Create the thread message queue.
            handle = self.install(13, callback, module, 0)
            if not handle:
                error = ctypes.WinError(ctypes.get_last_error())
            ready.set()
            if handle:
                try:
                    while not stopping.is_set() and self.get(ctypes.byref(message), None, 0, 0) > 0:
                        pass
                finally:
                    self.uninstall(handle)

        thread = threading.Thread(target=Run, name='ipdb ConEmu keys', daemon=True)
        thread.start()
        try:
            if not ready.wait(2):
                raise RuntimeError('ConEmu keyboard hook did not start')
            if error is not None:
                raise error
            yield
        finally:
            stopping.set()
            if thread_id is not None:
                self.post(thread_id, WM_QUIT, 0, 0)
            thread.join(1)


@contextmanager
def RuntimeNavigation(environment, switch):
    # This runs in the pane launcher, independently of the debuggee and its stdin.
    try:
        window = int(environment.get('ConEmuHWND', ''), 16)
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        active = ActivePane(user, kernel, window)
        name = 'Local\\ipdb-conemu-prompt-' + uuid.uuid4().hex
        event = WinApi(kernel, 'CreateEventW', wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL,
                       wintypes.BOOL, wintypes.LPCWSTR)(None, True, False, name)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
    except (AttributeError, OSError, RuntimeError, ValueError) as error:
        print('[ipdb] Runtime pane shortcuts unavailable: {}'.format(error), file=sys.stderr, flush=True)
        yield
        return

    is_set = WinApi(kernel, 'WaitForSingleObject', wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
    close = WinApi(kernel, 'CloseHandle', wintypes.BOOL, wintypes.HANDLE)
    key_state = WinApi(user, 'GetAsyncKeyState', wintypes.SHORT, ctypes.c_int)
    chord = RunningPaneChord()

    def Move(direction):
        if active():
            try:
                switch('Split(2,{},{})'.format(*direction))
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                print('[ipdb] {}'.format(error), file=sys.stderr, flush=True)

    try:
        with ThreadPoolExecutor(1) as actions, ExitStack() as hook:
            def Feed(key, down):
                if key not in chord.consumed and not chord.deadline and key != ord('W'):
                    return False
                control = key_state(0x11) < 0
                blocked = any(key_state(modifier) < 0 for modifier in (0x10, 0x12, 0x5b, 0x5c))
                consume, direction = chord.Feed(key, down, control, blocked, active(), is_set(event, 0) == 0,
                                                time.monotonic())
                if direction is not None:
                    actions.submit(Move, direction)
                return consume

            try:
                hook.enter_context(KeyboardHook(user, kernel).Listen(Feed))
            except (OSError, RuntimeError) as error:
                print('[ipdb] Runtime pane shortcuts unavailable: {}'.format(error), file=sys.stderr, flush=True)
            else:
                environment[PROMPT_EVENT] = name
            yield
    finally:
        environment.pop(PROMPT_EVENT, None)
        close(event)


def InstallPromptTracking(debugger, name):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    open_event = WinApi(kernel, 'OpenEventW', wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    set_event = WinApi(kernel, 'SetEvent', wintypes.BOOL, wintypes.HANDLE)
    reset_event = WinApi(kernel, 'ResetEvent', wintypes.BOOL, wintypes.HANDLE)
    close = WinApi(kernel, 'CloseHandle', wintypes.BOOL, wintypes.HANDLE)
    prompt_method = '_prompt' if hasattr(debugger, '_prompt') else 'cmdloop'
    original = getattr(debugger, prompt_method)

    def Prompt(self, *args, **kwargs):
        event = open_event(0x0002, False, name)
        if not event:
            return original(self, *args, **kwargs)
        set_event(event)
        try:
            return original(self, *args, **kwargs)
        finally:
            reset_event(event)
            close(event)

    setattr(debugger, prompt_method, Prompt)
