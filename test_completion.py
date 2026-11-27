"""Command and selected-frame completion regression tests."""
import sys
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

    def test_quoted_dictionary_keys_and_string_values(self):
        self.debugger.curframe.f_locals.update(
            config={'sample_rate': 1, 'sample_width': 2}, label='hello world')
        for quote in (chr(39), chr(34)):
            line = 'p config[' + quote + 'sample_'
            self.assertEqual(Matches(self.debugger, line, len(line) - 7, len(line)),
                             ['sample_rate', 'sample_width'])
            self.assertEqual(Matches(self.debugger, line, len('p config['), len(line)),
                             [quote + 'sample_rate', quote + 'sample_width'])
            line = 'p ' + quote + 'hello wo'
            self.assertIn('world', Matches(self.debugger, line, len(line) - 2, len(line)))
        self.assertIn('sample_count', self.Complete('p sample_c'))
        self.debugger.curframe.f_locals['config'] = {"it's done": 1}
        line = "p config['it\\'s d"
        self.assertEqual(Matches(self.debugger, line, len(line) - 1, len(line)), ['done'])

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

    def test_ambiguous_completion_returns_only_common_extension(self):
        import io
        import sys
        import portable_ipdb
        from IPython.core.debugger import Pdb
        from unittest import mock
        debugger = Pdb(stdout=io.StringIO())
        debugger.curframe = SimpleNamespace(f_globals={}, f_locals={
            'signal_count': 1, 'signal_config': 2, 'signal_status': 3})
        for prefix, expected in [('sig', 'signal_'), ('signal_', None),
                                 ('signal_c', 'signal_co'), ('signal_co', None),
                                 ('signal_cou', 'signal_count')]:
            line = 'p ' + prefix
            readline = SimpleNamespace(get_line_buffer=lambda: line,
                                       get_begidx=lambda: 2, get_endidx=lambda: len(line))
            with mock.patch.dict(sys.modules, {'readline': readline}):
                for unused in range(2):
                    self.assertEqual(debugger.complete(prefix, 0), expected)
                    self.assertIsNone(debugger.complete(prefix, 1))

    def test_no_property_evaluation(self):
        class Object:
            @property
            def explosive(self):
                raise AssertionError('Completion must not evaluate this property')
        self.debugger.curframe.f_locals['obj'] = Object()
        self.assertEqual(ExpressionMatches(self.debugger, 'obj.expl'), ['obj.explosive'])
        self.assertEqual(ExpressionMatches(self.debugger, 'obj.explosive.value'), [])

    def test_terminal_prompt_uses_portable_completion(self):
        import io
        import portable_ipdb
        from IPython.terminal.debugger import TerminalPdb
        from prompt_toolkit.completion import CompleteEvent, Completion
        from prompt_toolkit.document import Document
        from unittest import mock

        native = mock.Mock()
        native.get_completions.return_value = [Completion('sample.py', start_position=-3)]
        session = SimpleNamespace(completer=native, complete_while_typing=True)
        with mock.patch('IPython.terminal.debugger.PromptSession', return_value=session), \
                mock.patch('IPython.terminal.debugger._use_simple_prompt', False, create=True):
            debugger = TerminalPdb(stdout=io.StringIO())
        loop = getattr(debugger, 'pt_loop', None)
        if loop is not None:
            self.addCleanup(loop.close)
        debugger.curframe = SimpleNamespace(f_globals={}, f_locals={
            'signal_count': 1, 'signal_config': 2, 'signal_status': 3, 'config': {'signal_name': 4}})
        event = CompleteEvent(completion_requested=True)
        for line, expected in (
                ('p sig', ['p signal_']), ('p signal_', []), ('p signal_c', ['p signal_co']),
                ('p signal_cou', ['p signal_count']), ('i dis', ['i display']),
                ('p config["signal_n', ['p config["signal_name']), ('b sam', ['b sample.py'])):
            with self.subTest(line=line):
                results = session.completer.get_completions(Document(line), event)
                self.assertEqual([line[:len(line) + item.start_position] + item.text for item in results], expected)
        self.assertFalse(session.complete_while_typing)
        self.assertEqual(list(session.completer.get_completions(Document('p sig'), CompleteEvent(text_inserted=True))), [])

    def test_missing_frame_and_native_filename_completion(self):
        self.assertEqual(Matches(SimpleNamespace(), 'chec', 0, 4), ['checkpoint'])
        self.assertIsNone(self.Complete('break sample'))

    @unittest.skipUnless(sys.platform == 'win32', 'Windows prompt typeahead required')
    def test_terminal_ctrl_c_cancels_blank_typeahead_without_eating_explicit_commands(self):
        import asyncio
        import io
        from unittest import mock

        import portable_ipdb
        from IPython import get_ipython
        from IPython.terminal.debugger import TerminalPdb
        from ipdb import __main__ as ipdb_main
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        async def Check():
            ipdb_main._get_debugger_cls()
            with create_pipe_input() as pipe, \
                    mock.patch.object(get_ipython(), 'debugger_history', InMemoryHistory()), \
                    mock.patch('IPython.terminal.debugger._use_simple_prompt', False):
                debugger = TerminalPdb(stdout=io.StringIO(), pt_session_options={'input': pipe, 'output': DummyOutput()})
                loop = asyncio.get_running_loop()

                async def Prompt():
                    try:
                        return await asyncio.wait_for(loop.run_in_executor(None, debugger._prompt), 5)
                    except asyncio.TimeoutError:
                        pipe.close()
                        raise

                try:
                    debugger.lastcmd = 'continue'
                    debugger._portable_resume = ('continue', 99)
                    pipe.send_text('n\r\r\rp before\r\r\x03p after\r\r')
                    self.assertEqual([await Prompt() for _ in range(4)], ['n', 'p before', 'p after', ''])
                    self.assertEqual(debugger.lastcmd, '')
                    self.assertIsNone(debugger._portable_resume)
                    pipe.send_text('c 100\r\r\x03p stopped\r')
                    self.assertEqual(await Prompt(), 'p stopped')
                finally:
                    debugger.pt_loop.close()

        asyncio.run(Check())


class BufferCompletionTests(unittest.TestCase):
    def test_break_filename_completion_and_tab_ambiguity(self):
        import io
        import os
        from pathlib import Path
        import sys
        from unittest import mock

        import portable_ipdb
        from IPython.core.debugger import Pdb
        from IPython.terminal.debugger import TerminalPdb
        from prompt_toolkit.completion import CompleteEvent
        from prompt_toolkit.document import Document

        files = [str(Path('First Dir') / 'Shared.py'), str(Path('Other Dir') / 'Shared.py'),
                 str(Path('Outside') / 'Module Name.PY')]
        output = io.StringIO()
        session = SimpleNamespace(completer=None, complete_while_typing=True)
        with mock.patch('IPython.terminal.debugger.PromptSession', return_value=session), \
                mock.patch('IPython.terminal.debugger._use_simple_prompt', False, create=True):
            terminal = TerminalPdb(stdout=output)
        loop = getattr(terminal, 'pt_loop', None)
        if loop is not None:
            self.addCleanup(loop.close)
        event = CompleteEvent(completion_requested=True)
        debugger = Pdb(stdout=output)
        with mock.patch('completion.BufferFiles', return_value=files):
            line = 'b Sha'
            redisplay = mock.Mock()
            readline = SimpleNamespace(get_line_buffer=lambda: line, get_begidx=lambda: 2,
                                       get_endidx=lambda: len(line), redisplay=redisplay)
            with mock.patch.dict(sys.modules, {'readline': readline}):
                self.assertIsNone(debugger.complete('Sha', 0))
                self.assertIsNone(debugger.complete('Sha', 1))
            redisplay.assert_called_once_with()
            self.assertEqual(list(session.completer.get_completions(Document(line), event)), [])
            notice = 'Multiple Vim buffers are named \'Shared.py\'. Specify a file path.'
            self.assertEqual(output.getvalue().count(notice), 2)
            for line, expected in (('tbreak Mod', 'tbreak Module Name.PY'),
                                   ('b "Module Na', 'b "Module Name.PY"'),
                                   ('b First Dir' + os.sep + 'Sha', 'b First Dir' + os.sep + 'Shared.py')):
                with self.subTest(line=line):
                    items = session.completer.get_completions(Document(line), event)
                    self.assertEqual([line[:len(line) + item.start_position] + item.text for item in items], [expected])

    def test_loaded_files_can_set_breakpoints_and_duplicates_require_a_path(self):
        import json
        import os
        from pathlib import Path
        import subprocess
        import sys
        import tempfile

        launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'Source' / 'Entry.py'
            first = script.with_name('Shared.py')
            second = root / 'Other Dir' / 'Shared.py'
            unique = root / 'Outside' / 'BufferOnly.PY'
            for path in (first, second, unique):
                path.parent.mkdir()
                path.write_text('value = 1\nvalue += 1\n')
            script.write_text('stop = True\n')
            state = root / 'state'
            state.with_suffix('.buffers').write_text(json.dumps([str(first), str(second), str(unique)]))
            commands = ('b Shared.py:2\ntbreak Shared:2\nb BufferOnly:2 if value == 1\n'
                        'b "Other Dir/Shared.py":2\ni b\nq\n')
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(script)],
                                    input=commands, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    universal_newlines=True, cwd=directory, timeout=20,
                                    env=dict(os.environ, IPDB_VIM_STATE=str(state), IPY_TEST_SIMPLE_PROMPT='1',
                                             IPYTHONDIR=directory))
            output = result.stdout
            self.assertEqual(result.returncode, 0, output)
            self.assertEqual(output.count('Specify a file path.'), 2, output)
            self.assertIn('Breakpoint 1 at {}:2'.format(unique.resolve()), output)
            self.assertIn('Breakpoint 2 at {}:2'.format(second.resolve()), output)
            self.assertIn('stop only if value == 1', output)
            self.assertNotIn('Breakpoint 3 at ', output)

    def test_vim_python_buffers(self):
        import tempfile
        import json
        import os
        from pathlib import Path
        from unittest import mock
        from completion import BufferMatches
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / 'state')
            path = str(Path(directory) / 'helper.py')
            Path(state + '.buffers').write_text(json.dumps([path, path, str(Path(directory) / 'notes.txt')]))
            with mock.patch.dict(os.environ, {'IPDB_VIM_STATE': state}):
                self.assertEqual(BufferMatches('help'), [os.path.relpath(path)])
                self.assertEqual(BufferMatches('notes'), [])
                with mock.patch('completion.os.path.relpath', side_effect=ValueError('different drive')):
                    self.assertEqual(BufferMatches('help'), [path])
                Path(state + '.buffers').write_text('invalid')
                self.assertEqual(BufferMatches('help'), [])

    def test_filename_completion_preserves_existing_directory(self):
        from unittest import mock

        line = 'b pkg/he'
        with mock.patch('completion.BufferMatches', return_value=['pkg/helper.py']) as match:
            self.assertEqual(Matches(SimpleNamespace(), line, 6, len(line)), ['helper.py'])
            match.assert_called_once_with('pkg/he')


if __name__ == '__main__':
    unittest.main()
