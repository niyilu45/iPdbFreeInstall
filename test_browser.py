"""Source cursor and search/mark state regression tests."""
import unittest
from source_browser import SourceBrowser
from source_theme import PaintLine


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.browser = SourceBrowser()
        self.view = {'file': 'one.py', 'line': 1, 'offset': 1,
                     'lines': ['alpha target\n', 'second\n', 'target end\n']}

    def test_cursor_search_wrap_and_reverse(self):
        self.assertEqual(self.browser.Feed(b'/target\n', self.view, 2, 80), b'')
        self.assertEqual((self.view['_cursor_row'], self.view['_cursor_col']), (0, 6))
        self.browser.Feed(b'n', self.view, 2, 80)
        self.assertEqual(self.view['_cursor_row'], 2)
        self.browser.Feed(b'n', self.view, 2, 80)
        self.assertEqual(self.view['_cursor_row'], 0)
        self.browser.Feed(b'N', self.view, 2, 80)
        self.assertEqual(self.view['_cursor_row'], 2)
        self.browser.Feed(b'/missing\n', self.view, 2, 80)
        self.assertEqual(self.view['_status'], 'Not found: missing')
        self.assertEqual(self.view['_cursor_row'], 2)

    def test_marks_survive_new_snapshot_and_are_file_local(self):
        self.browser.Feed(b'/target\nma', self.view, 2, 80)
        new = dict(self.view, _cursor_row=2, _cursor_col=0)
        self.browser.Feed(b'`a', new, 2, 80)
        self.assertEqual((new['_cursor_row'], new['_cursor_col']), (0, 6))
        self.browser.Feed(b"'a", new, 2, 80)
        self.assertEqual(new['_cursor_col'], 0)
        new['file'] = 'two.py'
        self.browser.Feed(b'`a', new, 2, 80)
        self.assertEqual(new['_status'], 'Mark a not set')

    def test_cursor_movement_and_empty_file(self):
        self.browser.Move(self.view, 'down', 2, 80)
        self.browser.Move(self.view, 'right', 2, 80)
        self.assertEqual((self.view['_cursor_row'], self.view['_cursor_col']), (1, 1))
        self.browser.Move(self.view, 'last', 2, 80)
        self.assertEqual(self.view['_top'], 1)
        self.browser.Move({}, 'line_end', 2, 80)

    def test_search_edit_cancel_and_cursor_render(self):
        self.browser.Feed(b'/targeX\x7ft\n', self.view, 2, 80)
        self.assertEqual(self.browser.query, 'target')
        self.browser.Feed(b'/discard\x1b', self.view, 2, 80)
        self.assertEqual(self.browser.mode, '')
        self.assertEqual(self.browser.query, 'target')
        rendered = PaintLine([('abc', 244, False)], 1, 1, 30, cursor=1)
        self.assertIn('\x1b[48;5;250;38;5;16mb', rendered)
        self.assertIn('>', rendered)


if __name__ == '__main__':
    unittest.main()
