"""Key routing while the Windows ConEmu debuggee is running."""
import sys
import unittest

from conemu_keys import RunningPaneChord


NO_ACTION = (False, None)
CONSUMED = (True, None)
CTRL = 0x11
ARROW_KEYS = ((0x25, -1, 0), (0x28, 0, 1), (0x26, 0, -1), (0x27, 1, 0))


class RunningPaneChordTests(unittest.TestCase):
    def setUp(self):
        self.chord = RunningPaneChord()

    def Key(self, key, *, down=True, control=False, blocked=False, active=True, prompt=False, now=1.0):
        key = ord(key) if isinstance(key, str) else key
        return self.chord.Feed(key, down, control, blocked, active, prompt, now)

    def test_directions_with_letters_and_arrow_keys(self):
        letters = (('H', -1, 0), ('J', 0, 1), ('K', 0, -1), ('L', 1, 0))
        for key, dx, dy in letters + ARROW_KEYS:
            for held in (False, True):
                with self.subTest(key=key, held=held):
                    self.assertEqual(self.Key('W', control=True), CONSUMED)
                    self.assertEqual(self.Key('W', down=False), CONSUMED)
                    if not held:
                        self.assertEqual(self.Key(CTRL, down=False), NO_ACTION)
                    self.assertEqual(self.Key(key, control=held), (True, (dx, dy)))
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
        self.assertEqual(self.Key('K', prompt=True), (True, (0, -1)))
        self.assertEqual(self.Key('K', down=False, prompt=True), CONSUMED)
        self.assertEqual(self.Key('W', control=True, prompt=True), NO_ACTION)

    def test_auto_repeat_dispatches_only_one_move(self):
        self.assertEqual(self.Key('W', control=True), CONSUMED)
        self.assertEqual(self.Key('W', control=True), CONSUMED)
        self.assertEqual(self.Key('W', down=False), CONSUMED)
        self.assertEqual(self.Key('K'), (True, (0, -1)))
        self.assertEqual(self.Key('K'), CONSUMED)
        self.assertEqual(self.Key('K', down=False), CONSUMED)
        self.assertEqual(self.Key('K'), NO_ACTION)


@unittest.skipUnless(sys.platform == 'win32', 'Native Windows events required')
class PromptTrackingTests(unittest.TestCase):
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
