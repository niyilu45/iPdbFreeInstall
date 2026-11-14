"""Counted tmux resize behavior without requiring a tmux installation."""
import unittest
from unittest import mock
from tmux_resize import Handle


class ResizeTests(unittest.TestCase):
    def test_count_then_grow_and_clear(self):
        with mock.patch('tmux_resize.subprocess.check_output', return_value='') as call:
            Handle('tmux', 'private', '%1', '/dev/pts/2', 'reset')
            self.assertIn('switch-client', call.call_args[0][0])
        with mock.patch('tmux_resize.subprocess.check_output', side_effect=['2', '', '']) as call:
            Handle('tmux', 'private', '%1', '/dev/pts/2', '5')
            self.assertIn('25', call.call_args_list[1][0][0])
        with mock.patch('tmux_resize.subprocess.check_output', side_effect=['5', '', '10', '']) as call:
            Handle('tmux', 'private', '%1', '/dev/pts/2', '+')
            self.assertEqual(call.call_args[0][0][-2:], ['-y', '15'])
            self.assertEqual(call.call_args_list[1][0][0][-1], '')

    def test_shrink_clamps_and_equalize(self):
        with mock.patch('tmux_resize.subprocess.check_output', side_effect=['99', '', '10', '']) as call:
            Handle('tmux', 'private', '%1', '/dev/pts/2', '-')
            self.assertEqual(call.call_args[0][0][-1], '3')
        with mock.patch('tmux_resize.subprocess.check_output', side_effect=['', '', '']) as call:
            Handle('tmux', 'private', '%1', '/dev/pts/2', '=')
            self.assertEqual(call.call_args[0][0][-1], 'even-vertical')


if __name__ == '__main__':
    unittest.main()
