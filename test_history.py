"""Terminal history restoration without requiring a native PTY."""
import unittest
import portable_ipdb
import pyte
from output_history import OutputHistory


class HistoryTests(unittest.TestCase):
    def test_restore_scrolled_output_and_input(self):
        history = OutputHistory(40, 4)
        history.Feed(b'first\r\nsecond\r\nthird\r\nfourth\r\nipdb> p value')
        for unused in range(3):
            history.Resize(40, 8)
            self.assertEqual([line.rstrip() for line in history.screen.display[:5]],
                             ['first', 'second', 'third', 'fourth', 'ipdb> p value'])
            self.assertEqual((history.screen.cursor.x, history.screen.cursor.y), (13, 4))
            history.Resize(40, 4)

    def test_render_preserves_edits_color_and_cursor(self):
        history = OutputHistory(30, 4)
        history.Feed(b'\x1b[31mERROR\x1b[0m\r\nipdb> p valX\x08\x1b[Ke')
        history.Feed(b'\x1b[2D')
        terminal = pyte.Screen(30, 10)
        pyte.Stream(terminal).feed(history.Render(3))
        self.assertEqual(terminal.display[2].strip(), 'ERROR')
        self.assertEqual(terminal.display[3].strip(), 'ipdb> p vale')
        self.assertEqual(terminal.buffer[2][0].fg, 'red')
        self.assertEqual(terminal.cursor.x, history.screen.cursor.x)
        self.assertEqual(terminal.cursor.y, history.screen.cursor.y + 2)

    def test_render_overwrites_old_rows_and_clips_wide_edge(self):
        history = OutputHistory(6, 2)
        history.Feed('one \u754c'.encode('utf-8'))
        terminal = pyte.Screen(6, 4)
        stream = pyte.Stream(terminal)
        stream.feed('stays\r\n111111\r\n222222')
        stream.feed(history.Render(2))
        self.assertEqual(terminal.display[:3], ['stays ', 'one \u754c', '      '])
        self.assertEqual(terminal.cursor.x, 6)
        history.Resize(5, 2)
        terminal.resize(columns=5)
        stream.feed(history.Render(2))
        self.assertEqual(terminal.display[:3], ['stays', 'one  ', '     '])

    def test_exit_tail_keeps_error_without_terminal_controls(self):
        history = OutputHistory(80, 3)
        history.Feed(b'first\r\nsecond\r\nTraceback:\r\n\x1b[31mRuntimeError: failed\x1b[0m\r\n')
        tail = history.Tail()
        self.assertIn('Traceback:\nRuntimeError: failed', tail)
        self.assertNotIn('\x1b', tail)
        self.assertEqual(history.Tail(1), 'RuntimeError: failed')

    def test_partial_utf8_and_bounded_history(self):
        history = OutputHistory(20, 2, limit=3)
        history.Feed(b'caf\xc3')
        history.Feed(b'\xa9\r\n')
        self.assertEqual(history.screen.display[0].strip(), 'caf\u00e9')
        for number in range(10):
            history.Feed(('line{}\r\n'.format(number)).encode())
        self.assertLessEqual(len(history.screen.history.top), 3)
        history.Resize(20, 6)
        self.assertIn('line9', '\n'.join(history.screen.display))


if __name__ == '__main__':
    unittest.main()
