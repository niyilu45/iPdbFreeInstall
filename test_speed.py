"""Check that continue optimization preserves debugger stop semantics."""
import inspect
import io
import sys
import unittest
import portable_ipdb
from IPython.core.debugger import Pdb


def Workload():
    total = 0
    for number in range(10):
        total += number
    return total


class Runner(Pdb):
    def __init__(self, step=False):
        super().__init__(stdout=io.StringIO())
        self.stops = []
        self.step_once = step
        self.traces = []

    def user_line(self, frame):
        self.stops.append((frame.f_lineno, frame.f_locals.get('number')))
        if self.step_once:
            self.step_once = False
            self.set_next(frame)
        else:
            self.set_continue()
            self.traces.append(sys.gettrace())


class SpeedTests(unittest.TestCase):
    def test_disabled_breakpoints_do_not_keep_tracing(self):
        debugger = Runner()
        self.addCleanup(debugger.clear_all_breaks)
        line = inspect.getsourcelines(Workload)[1] + 3
        debugger.set_break(__file__, line)
        entry = debugger.get_breaks(__file__, line)[0]
        entry.disable()
        debugger._portable_displays = {1: {'enabled': True, 'expression': 'total'}}
        self.assertEqual(debugger.runcall(Workload), 45)
        self.assertEqual(debugger.traces, [None])
        self.assertIn(entry, debugger.get_breaks(__file__, line))
        entry.enable()
        debugger.stops = []
        self.assertEqual(debugger.runcall(Workload), 45)
        self.assertGreater(len(debugger.stops), 1)

    def test_deleted_breakpoints_do_not_keep_tracing(self):
        debugger = Runner()
        line = inspect.getsourcelines(Workload)[1] + 3
        debugger.set_break(__file__, line)
        debugger.clear_all_breaks()
        self.assertEqual(debugger.runcall(Workload), 45)
        self.assertEqual(debugger.traces, [None])

    def test_conditional_breakpoint(self):
        debugger = Runner()
        self.addCleanup(debugger.clear_all_breaks)
        line = inspect.getsourcelines(Workload)[1] + 3
        self.assertIsNone(debugger.set_break(__file__, line, cond='number == 4'))
        self.assertEqual(debugger.runcall(Workload), 45)
        self.assertEqual(debugger.stops[1:], [(line, 4)])

    def test_temporary_breakpoint_and_ignore_count(self):
        debugger = Runner()
        self.addCleanup(debugger.clear_all_breaks)
        line = inspect.getsourcelines(Workload)[1] + 3
        debugger.set_break(__file__, line, temporary=True)
        debugger.get_breaks(__file__, line)[0].ignore = 2
        self.assertEqual(debugger.runcall(Workload), 45)
        self.assertEqual(debugger.stops[1:], [(line, 2)])
        self.assertFalse(debugger.get_breaks(__file__, line))

    def test_next_and_continue_without_breakpoints(self):
        debugger = Runner(step=True)
        self.assertEqual(debugger.runcall(Workload), 45)
        self.assertEqual(len(debugger.stops), 2)
        self.assertEqual(debugger.stops[1][0], debugger.stops[0][0] + 1)
        self.assertEqual(debugger.traces, [None])


if __name__ == '__main__':
    unittest.main()
