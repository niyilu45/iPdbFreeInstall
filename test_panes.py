"""Source window layout, independent state and close behavior."""
import unittest
from source_panes import SourcePanes


class PaneTests(unittest.TestCase):
    def test_only_main_follows_debugger(self):
        panes = SourcePanes()
        panes.Update({'file': 'a.py', 'line': 2, 'lines': ['a'] * 40}, 20, 100)
        panes.Command('vsplit', 20, 100)
        second = panes.active
        panes.views[second]['_cursor_row'] = 10
        panes.Command('split', 20, 100)
        third = panes.active
        panes.Update({'file': 'b.py', 'line': 5, 'lines': ['b'] * 40}, 20, 100)
        self.assertEqual(panes.views[0]['file'], 'b.py')
        self.assertEqual(panes.views[second]['file'], 'a.py')
        self.assertEqual(panes.views[second]['_cursor_row'], 10)
        self.assertEqual(panes.views[third]['file'], 'a.py')
        self.assertEqual(panes.Layout(20, 100)[0][:2], (0, 0))

    def test_navigation_close_and_main_protection(self):
        panes = SourcePanes()
        panes.Command('vs', 20, 100)
        second = panes.active
        self.assertTrue(panes.Focus('h', True, 20, 100))
        self.assertEqual(panes.active, 0)
        self.assertTrue(panes.Focus('l', True, 20, 100))
        self.assertEqual(panes.active, second)
        self.assertFalse(panes.Focus('j', True, 20, 100))
        self.assertTrue(panes.Focus('k', False, 20, 100))
        panes.active = second
        panes.Command('q', 20, 100)
        self.assertEqual(panes.Layout(20, 100), {0: (0, 0, 20, 100)})
        panes.Command('q', 20, 100)
        self.assertIn('cannot be closed', panes.views[0]['_status'])

    def test_colon_command_does_not_reach_debugger(self):
        panes = SourcePanes()
        browser, view = panes.browsers[0], panes.views[0]
        self.assertEqual(browser.Feed(b':vsplit\n', view, 18, 100), b'')
        self.assertEqual(view.pop('_command'), 'vsplit')
        panes.Command('split', 6, 30)
        self.assertEqual(len(panes.views), 1)


if __name__ == '__main__':
    unittest.main()
