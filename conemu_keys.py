"""Route pane navigation and resizing while a ConEmu debuggee runs."""
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
SHIFT_KEYS = {0x10, 0xa0, 0xa1}
CONTROL_KEYS = {0x11, 0xa2, 0xa3}
BLOCKING_KEYS = {0x12, 0x5b, 0x5c, 0xa4, 0xa5}
MODIFIERS = SHIFT_KEYS | CONTROL_KEYS | BLOCKING_KEYS
RESIZE_KEYS = {(0xbb, True): '+', (0xbb, False): '=', (0xbd, False): '-',
               (0x6b, False): '+', (0x6d, False): '-'}
CHORD_TIMEOUT = 2
PROMPT_EVENT = 'IPDB_CONEMU_PROMPT_EVENT'
WM_QUIT = 0x0012


class RunningPaneChord:
    def __init__(self):
        self.deadline = 0
        self.consumed = set()
        self.count = None

    def Feed(self, key, down, control, blocked, active, prompt, now, shift=False):
        if not active:
            self.deadline = 0
            self.count = None
        if key in self.consumed:
            if not down:
                self.consumed.remove(key)
            return True, None
        if not active or not down or key in MODIFIERS:
            return False, None
        if key == ord('W') and control and not blocked and not shift and not prompt:
            self.deadline = now + CHORD_TIMEOUT
            self.count = None
            self.consumed.add(key)
            return True, None
        if now < self.deadline and not blocked:
            if not shift and (ord('0') <= key <= ord('9') or 0x60 <= key <= 0x69):
                self.count = min(9999, (self.count or 0) * 10 + (key & 0xf))
                self.deadline = now + CHORD_TIMEOUT
                self.consumed.add(key)
                return True, None
            height = RESIZE_KEYS.get((key, shift))
            direction = DIRECTIONS.get(key) if self.count is None and not shift else None
        else:
            height = direction = None
        count, self.count = self.count, None
        self.deadline = 0
        if height is not None:
            self.consumed.add(key)
            return True, ('resize', height, count or 1)
        if direction is not None:
            self.consumed.add(key)
            return True, ('move',) + direction
        return False, None


def WinApi(dll, name, result, *arguments):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = arguments
    return function


def ConsoleWindow(kernel):
    # ConEmuHk redirects GetConsoleWindow to its canvas. Its loaded DLL
    # exports the actual console handle used by OnActiveConWndStore.
    name = 'ConEmuHk64.dll' if ctypes.sizeof(ctypes.c_void_p) == 8 else 'ConEmuHk.dll'
    hook = WinApi(kernel, 'GetModuleHandleW', wintypes.HMODULE, wintypes.LPCWSTR)(name)
    if hook:
        return WinApi(ctypes.WinDLL(name, handle=hook), 'GetRealConsoleWindow', wintypes.HWND)()
    return WinApi(kernel, 'GetConsoleWindow', wintypes.HWND)()


class ActivePane:
    def __init__(self, user, kernel, window, draw=0, back=0):
        class GuiInfo(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('flags', wintypes.DWORD),
                        ('active', wintypes.HWND), ('focus', wintypes.HWND),
                        ('capture', wintypes.HWND), ('menu', wintypes.HWND),
                        ('move', wintypes.HWND), ('caret', wintypes.HWND), ('rect', wintypes.RECT)]

        self.window = window
        get_ancestor = WinApi(user, 'GetAncestor', wintypes.HWND, wintypes.HWND, wintypes.UINT)
        self.root = get_ancestor(window, 2) or window  # GA_ROOT for embedded ConEmu windows.
        self.pane_windows = tuple(handle for handle in (draw, back) if handle)
        self.foreground = WinApi(user, 'GetForegroundWindow', wintypes.HWND)
        self.is_child = WinApi(user, 'IsChild', wintypes.BOOL, wintypes.HWND, wintypes.HWND)
        get_long = 'GetWindowLongPtrW' if ctypes.sizeof(ctypes.c_void_p) == 8 else 'GetWindowLongW'
        self.window_data = WinApi(user, get_long, ctypes.c_void_p, wintypes.HWND, ctypes.c_int)
        # ConEmu stores the real console HWND in the canvas's WindowLongDCWnd_ConWnd field.
        self.console = (self.window_data(draw, 0) if draw else None) or ConsoleWindow(kernel)
        self.info_type = GuiInfo
        self.gui_info = WinApi(user, 'GetGUIThreadInfo', wintypes.BOOL, wintypes.DWORD, ctypes.POINTER(GuiInfo))
        if not self.console or not WinApi(user, 'IsWindow', wintypes.BOOL, wintypes.HWND)(window):
            raise RuntimeError('ConEmu did not identify the debugger console')

    def __call__(self):
        # ConEmu's OnActiveConWndStore publishes the active console in GWLP_USERDATA.
        if self.foreground() != self.root or self.window_data(self.window, -21) != self.console:
            return False
        info = self.info_type(size=ctypes.sizeof(self.info_type))
        if not self.gui_info(0, ctypes.byref(info)) or not info.focus or info.flags & 0x1e:
            return False
        return info.focus == self.window or any(info.focus == pane or self.is_child(pane, info.focus)
                                                for pane in self.pane_windows)


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
def RuntimeNavigation(environment, switch, resize):
    # This runs in the pane launcher, independently of the debuggee and its stdin.
    environment.pop(PROMPT_EVENT, None)
    try:
        # Windows os.environ uppercases names. The child's plain dict is case-sensitive.
        conemu = {name.upper(): value for name, value in environment.items()}
        window, draw, back = (int(conemu.get(name) or '0', 16) for name in
                             ('CONEMUHWND', 'CONEMUDRAWHWND', 'CONEMUBACKHWND'))
        if not window:
            raise RuntimeError('ConEmu main window is unavailable')
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        active = ActivePane(user, kernel, window, draw, back)
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
    # Low-level callbacks run before asynchronous keyboard state is updated.
    pressed = {key for key in MODIFIERS if key >= 0x5b and key_state(key) < 0}
    chord = RunningPaneChord()

    def ApplyAction(action):
        if active():
            try:
                operation, *arguments = action
                if operation == 'move':
                    switch('Split(2,{},{})'.format(*arguments))
                else:
                    resize(*arguments)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                print('[ipdb] {}'.format(error), file=sys.stderr, flush=True)

    try:
        with ThreadPoolExecutor(1) as actions, ExitStack() as hook:
            def Feed(key, down):
                if key in MODIFIERS:
                    if down:
                        pressed.add(key)
                    else:
                        pressed.discard(key)
                    return False
                if key not in chord.consumed and not chord.deadline and key != ord('W'):
                    return False
                control = bool(pressed & CONTROL_KEYS)
                blocked = bool(pressed & BLOCKING_KEYS)
                shift = bool(pressed & SHIFT_KEYS)
                consume, action = chord.Feed(key, down, control, blocked, active(), is_set(event, 0) == 0,
                                             time.monotonic(), shift)
                if action is not None:
                    actions.submit(ApplyAction, action)
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
