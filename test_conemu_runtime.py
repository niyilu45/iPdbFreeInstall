"""Key routing while the Windows ConEmu debuggee is running."""
import sys
import unittest
from unittest import mock

from conemu_keys import RunningPaneChord


NO_ACTION = (False, None)
CONSUMED = (True, None)
CTRL = 0x11
ARROW_KEYS = ((0x25, -1, 0), (0x28, 0, 1), (0x26, 0, -1), (0x27, 1, 0))


class RunningPaneChordTests(unittest.TestCase):
    def setUp(self):
        self.chord = RunningPaneChord()

    def Key(self, key, *, down=True, control=False, blocked=False, active=True, prompt=False, now=1.0, shift=False):
        key = ord(key) if isinstance(key, str) else key
        return self.chord.Feed(key, down, control, blocked, active, prompt, now, shift)

    def test_directions_with_letters_and_arrow_keys(self):
        letters = (('H', -1, 0), ('J', 0, 1), ('K', 0, -1), ('L', 1, 0))
        for key, dx, dy in letters + ARROW_KEYS:
            for held in (False, True):
                with self.subTest(key=key, held=held):
                    self.assertEqual(self.Key('W', control=True), CONSUMED)
                    self.assertEqual(self.Key('W', down=False), CONSUMED)
                    if not held:
                        self.assertEqual(self.Key(CTRL, down=False), NO_ACTION)
                    self.assertEqual(self.Key(key, control=held), (True, ('move', dx, dy)))
                    self.assertEqual(self.Key(key, down=False, active=False), CONSUMED)

    def test_plain_input_and_ctrl_c_are_never_consumed(self):
        self.assertEqual(self.Key('W'), NO_ACTION)
        self.assertEqual(self.Key('K'), NO_ACTION)
        self.assertEqual(self.Key('C', control=True), NO_ACTION)
        for key, control in (('C', True), ('X', False), (0x0D, False), (0x09, False)):
            with self.subTest(key=key):
                self.assertEqual(self.Key('W', control=True), CONSUMED)
                self.assertEqual(self.Key('W', down=False), CONSUMED)
                self.assertEqual(self.Key(key, control=control), NO_ACTION)
                self.assertEqual(self.Key(key, down=False), NO_ACTION)
                self.assertEqual(self.Key('K'), NO_ACTION)

    def test_disables_chords_in_the_prompt_other_panes_and_modifier_combinations(self):
        for state in ({'prompt': True}, {'active': False}, {'blocked': True}):
            with self.subTest(state=state):
                self.assertEqual(self.Key('W', control=True, **state), NO_ACTION)
                self.assertEqual(self.Key('W', down=False, **state), NO_ACTION)
                self.assertEqual(self.Key('K', **state), NO_ACTION)

    def test_pending_prefix_expires_or_is_cancelled_on_state_change(self):
        for state in ({'now': 10.0}, {'active': False}, {'blocked': True}):
            with self.subTest(state=state):
                self.assertEqual(self.Key('W', control=True), CONSUMED)
                self.assertEqual(self.Key('W', down=False), CONSUMED)
                self.assertEqual(self.Key('K', **state), NO_ACTION)
                self.assertEqual(self.Key('K', now=10.1), NO_ACTION)

    def test_chord_started_while_running_finishes_when_prompt_opens(self):
        self.assertEqual(self.Key('W', control=True), CONSUMED)
        self.assertEqual(self.Key('W', down=False, prompt=True), CONSUMED)
        self.assertEqual(self.Key('K', prompt=True), (True, ('move', 0, -1)))
        self.assertEqual(self.Key('K', down=False, prompt=True), CONSUMED)
        self.assertEqual(self.Key('W', control=True, prompt=True), NO_ACTION)

    def test_auto_repeat_dispatches_only_one_move(self):
        self.assertEqual(self.Key('W', control=True), CONSUMED)
        self.assertEqual(self.Key('W', control=True), CONSUMED)
        self.assertEqual(self.Key('W', down=False), CONSUMED)
        self.assertEqual(self.Key('K'), (True, ('move', 0, -1)))
        self.assertEqual(self.Key('K'), CONSUMED)
        self.assertEqual(self.Key('K', down=False), CONSUMED)
        self.assertEqual(self.Key('K'), NO_ACTION)

    def test_running_resize_supports_counts_and_both_keyboards(self):
        cases = ((('5',), 0xbb, True, '+', 5), (('1', 0x62), 0x6d, False, '-', 12),
                 ((), 0xbb, False, '=', 1), (('9', '9', '9', '9', '9'), 0x6b, False, '+', 9999),
                 ((), 0xbd, False, '-', 1))
        for digits, key, shift, operation, count in cases:
            with self.subTest(digits=digits, key=key, shift=shift):
                self.assertEqual(self.Key('W', control=True), CONSUMED)
                self.assertEqual(self.Key('W', down=False), CONSUMED)
                for digit in digits:
                    self.assertEqual(self.Key(digit), CONSUMED)
                    self.assertEqual(self.Key(digit, down=False), CONSUMED)
                self.assertEqual(self.Key(key, shift=shift), (True, ('resize', operation, count)))
                self.assertEqual(self.Key(key, down=False), CONSUMED)

    def test_counted_prefix_preserves_ctrl_c_and_next_plain_key(self):
        self.assertEqual(self.Key('W', control=True), CONSUMED)
        self.assertEqual(self.Key('W', down=False), CONSUMED)
        self.assertEqual(self.Key('5'), CONSUMED)
        self.assertEqual(self.Key('5', down=False), CONSUMED)
        self.assertEqual(self.Key('C', control=True), NO_ACTION)
        self.assertEqual(self.Key(0xbb, shift=True), NO_ACTION)


class ActivePaneTests(unittest.TestCase):
    def test_console_fallback_resolves_real_handle_from_the_loaded_hook(self):
        from conemu_keys import ConsoleWindow

        kernel = mock.Mock()
        kernel.GetModuleHandleW.return_value = 11
        with mock.patch('conemu_keys.ctypes.WinDLL', create=True) as dll:
            dll.return_value.GetRealConsoleWindow.return_value = 31
            self.assertEqual(ConsoleWindow(kernel), 31)
            name = kernel.GetModuleHandleW.call_args[0][0]
            self.assertIn(name, ('ConEmuHk64.dll', 'ConEmuHk.dll'))
            dll.assert_called_once_with(name, handle=11)
        kernel.GetConsoleWindow.assert_not_called()

    def test_uses_the_active_console_and_accepts_focus_inside_the_command_pane(self):
        from conemu_keys import ActivePane

        user = mock.Mock()
        window, draw, back, console = (4, 5, 6, 7)
        user.GetAncestor.return_value = 10
        user.GetForegroundWindow.return_value = 10
        data = {(draw, 0): console, (window, -21): console}
        for getter in (user.GetWindowLongPtrW, user.GetWindowLongW):
            getter.side_effect = lambda hwnd, offset: data.get((hwnd, offset))
        user.IsChild.side_effect = lambda parent, child: parent == draw and child == 8
        focus, flags = 8, 0

        def GuiInfo(_thread, info):
            info._obj.focus = focus
            info._obj.flags = flags
            return True

        user.GetGUIThreadInfo.side_effect = GuiInfo
        with mock.patch('conemu_keys.ConsoleWindow') as fallback:
            active = ActivePane(user, mock.Mock(), window, draw, back)
            fallback.assert_not_called()
        self.assertTrue(active())
        focus = back
        self.assertTrue(active())
        focus = 9
        self.assertFalse(active())
        focus, flags = window, 0x04
        self.assertFalse(active())
        flags, data[(window, -21)] = 0, 99
        self.assertFalse(active())


@unittest.skipUnless(sys.platform == 'win32', 'Native Windows events required')
class PromptTrackingTests(unittest.TestCase):
    def test_runtime_navigation_uses_windows_environment_and_dispatches_both_actions(self):
        from contextlib import contextmanager
        import os
        import conemu_keys

        with mock.patch.dict(os.environ, {'ConEmuHWND': '0x11', 'ConEmuDrawHWND': '0x12', 'ConEmuBackHWND': '0x13'}):
            environment = dict(os.environ)
        self.assertIn('CONEMUHWND', environment)
        self.assertNotIn('ConEmuHWND', environment)

        native_api = conemu_keys.WinApi

        def Api(dll, name, result, *parameters):
            if name == 'GetAsyncKeyState':
                return lambda key: 0
            return native_api(dll, name, result, *parameters)

        listeners = []

        @contextmanager
        def Listen(feed):
            listeners.append(feed)
            yield

        move, resize = mock.Mock(), mock.Mock()
        with mock.patch.object(conemu_keys, 'WinApi', side_effect=Api), \
                mock.patch.object(conemu_keys, 'ActivePane') as pane, \
                mock.patch.object(conemu_keys, 'KeyboardHook') as keyboard:
            pane.return_value.return_value = True
            keyboard.return_value.Listen.side_effect = Listen
            with conemu_keys.RuntimeNavigation(environment, move, resize):
                self.assertEqual(pane.call_args[0][2:], (0x11, 0x12, 0x13))
                feed, = listeners

                def Key(key, consumed):
                    self.assertEqual(feed(key, True), consumed)
                    self.assertEqual(feed(key, False), consumed)

                def Prefix():
                    self.assertFalse(feed(0xa2, True))  # Left Ctrl
                    Key(ord('W'), True)
                    self.assertFalse(feed(0xa2, False))

                Prefix()
                Key(ord('K'), True)
                Prefix()
                Key(ord('5'), True)
                self.assertFalse(feed(0xa0, True))  # Left Shift
                Key(0xbb, True)
                self.assertFalse(feed(0xa0, False))
                Prefix()
                self.assertFalse(feed(0xa2, True))
                Key(ord('C'), False)
                self.assertFalse(feed(0xa2, False))
                Key(ord('K'), False)

        move.assert_called_once_with('Split(2,0,-1)')
        resize.assert_called_once_with('+', 5)
        self.assertNotIn(conemu_keys.PROMPT_EVENT, environment)

    def test_runtime_chord_gate_opens_again_after_prompt_interrupt(self):
        import ctypes
        from ctypes import wintypes
        import uuid

        from conemu_keys import InstallPromptTracking, WinApi

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = WinApi(kernel, 'CreateEventW', wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL,
                        wintypes.BOOL, wintypes.LPCWSTR)
        wait = WinApi(kernel, 'WaitForSingleObject', wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
        close = WinApi(kernel, 'CloseHandle', wintypes.BOOL, wintypes.HANDLE)
        name = 'Local\\ipdb-test-conemu-{}'.format(uuid.uuid4().hex)
        event = create(None, True, False, name)
        self.assertTrue(event)
        self.addCleanup(close, event)
        case = self

        class TerminalPdb:
            def _prompt(self, interrupt=False):
                case.assertEqual(wait(event, 0), 0)
                if interrupt:
                    raise KeyboardInterrupt
                return 'continue'

        InstallPromptTracking(TerminalPdb, name)
        debugger = TerminalPdb()
        for _ in range(2):
            self.assertEqual(wait(event, 0), 258)
            self.assertEqual(debugger._prompt(), 'continue')
            self.assertEqual(wait(event, 0), 258)
            with self.assertRaises(KeyboardInterrupt):
                debugger._prompt(interrupt=True)
            self.assertEqual(wait(event, 0), 258)


if __name__ == '__main__':
    unittest.main()
