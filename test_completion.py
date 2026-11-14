"""Command and selected-frame completion regression tests."""
import unittest
from types import SimpleNamespace
from completion import Matches, ExpressionMatches


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.debugger = SimpleNamespace(curframe=SimpleNamespace(
            f_globals={'sample_global': 1, 'sample_values': None},
            f_locals={'sample_values': [1, 2], 'sample_count': 2}))

    def Complete(self, line):
        begin = len(line.rstrip('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789.'))
        return Matches(self.debugger, line, begin, len(line))

    def test_commands_and_subcommands(self):
        self.assertEqual(self.Complete('chec'), ['checkpoint'])
        self.assertEqual(self.Complete('disp'), ['display'])
        self.assertEqual(self.Complete('i dis'), ['display'])
        self.assertEqual(self.Complete('dis disp'), ['display'])
        self.assertEqual(self.Complete('del check'), ['checkpoint'])
        self.assertEqual(self.Complete('set va'), ['variable'])
        self.assertNotIn('down', self.Complete('do'))

    def test_variables_and_attributes(self):
        self.assertIn('sample_count', self.Complete('p sample_c'))
        self.assertEqual(self.Complete('disp sample_values.ap'), ['sample_values.append'])
        self.assertEqual(self.Complete('condition 1 sample_c'), ['sample_count'])
        self.assertEqual(self.Complete('break 20 if sample_c'), ['sample_count'])
        self.assertEqual(self.Complete('set var value = sample_c'), ['sample_count'])
        line = 'p sample_values.ap'
        self.assertEqual(Matches(self.debugger, line, line.rindex('.') + 1, len(line)), ['append'])
        self.assertEqual(self.Complete('   p sample_c'), ['sample_count'])
        self.assertEqual(Matches(self.debugger, '!sample_c', 0, 9), ['!sample_count'])

    def test_command_loop_installs_and_restores_frame_completer(self):
        import io
        import sys
        import portable_ipdb
        from IPython.core.debugger import Pdb
        from unittest import mock
        debugger = Pdb(stdout=io.StringIO())
        debugger.completekey = None
        debugger.use_rawinput = True
        import inspect
        sample_count = 2
        debugger.curframe = inspect.currentframe()
        previous = object()
        readline = SimpleNamespace(
            get_completer=mock.Mock(return_value=previous), set_completer=mock.Mock(),
            parse_and_bind=mock.Mock(), get_line_buffer=lambda: 'p sample_c',
            get_begidx=lambda: 2, get_endidx=lambda: 10)
        def Input(prompt):
            self.assertEqual(readline.set_completer.call_args[0][0], debugger.complete)
            self.assertEqual(debugger.complete('sample_c', 0), 'sample_count')
            self.assertIsNone(debugger.complete('sample_c', 1))
            return 'q'
        with mock.patch.dict(sys.modules, {'readline': readline}), mock.patch('builtins.input', side_effect=Input), mock.patch.object(debugger, 'onecmd', return_value=True):
            debugger.cmdloop()
        readline.parse_and_bind.assert_called_with('tab: complete')
        readline.set_completer.assert_called_with(previous)

    def test_no_property_evaluation(self):
        class Object:
            @property
            def explosive(self):
                raise AssertionError('Completion must not evaluate this property')
        self.debugger.curframe.f_locals['obj'] = Object()
        self.assertEqual(ExpressionMatches(self.debugger, 'obj.expl'), ['obj.explosive'])
        self.assertEqual(ExpressionMatches(self.debugger, 'obj.explosive.value'), [])

    def test_missing_frame_and_native_filename_completion(self):
        self.assertEqual(Matches(SimpleNamespace(), 'chec', 0, 4), ['checkpoint'])
        self.assertIsNone(self.Complete('break sample'))


if __name__ == '__main__':
    unittest.main()
