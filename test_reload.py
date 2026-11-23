"""Real debugger restart after same-size, same-timestamp source edits."""
import os
import py_compile
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import re
from reload_code import Relocate


class ReloadTests(unittest.TestCase):
    def test_rtime_reports_live_calls_and_r_retains_the_completed_report(self):
        launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
        for command in ('rtime', 'rt'):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                launch_dir = root / 'launch'
                launch_dir.mkdir()
                runtime_dir = root / 'runtime'
                runtime_dir.mkdir()
                script = root / 'timed.py'
                script.write_text('raise RuntimeError("STALE_TIMING_SOURCE")\n')
                extras = '\n\n'.join('def Extra{:02d}():\n    return 1'.format(i) for i in range(23))
                calls = '\n'.join('    Extra{:02d}()'.format(i) for i in range(23))
                source = ('import os, time\nos.chdir({!r})\n\ndef TimedLeaf():\n    return 42\n\n'.format(str(runtime_dir))
                          + extras
                          + '\n\ndef TimedOuter():\n' + calls
                          + '\n    TimedLeaf()\n    value = TimedLeaf()\n    time.sleep(0.06)\n    return value\n\n'
                          + 'print("TIMED_WORKLOAD", TimedOuter())\n')
                breakpoint_line = source.splitlines().index('    time.sleep(0.06)') + 1
                breakpoint_report = root / 'breakpoint.callgrind'
                breakpoint_path = root / 'breakpoint-report-path.txt'
                commands = [
                    "!open({!r}, 'w').write({!r})".format(str(script), source),
                    command, 'tbreak {}'.format(breakpoint_line), 'c',
                    "!snapshot = next(__import__('pathlib').Path({!r}).glob('callgrind.out.*'))".format(str(launch_dir / 'ipdCache')),
                    "!open({!r}, 'w').write(str(snapshot))".format(str(breakpoint_path)),
                    "!open({!r}, 'wb').write(snapshot.read_bytes())".format(str(breakpoint_report)),
                    "!print('TIMING_DEFAULT')", 'i time',
                    "!print('TIMING_ALL')", 'i time all',
                    "!TimedLeaf()", "!print('TIMING_FILTERED')", 'i time Leaf',
                    "!print('TIMING_COMPLETE')", 'c',
                    "!print('TIMING_OFF')", 'r', 'c',
                    "!print('TIMING_RETAINED')", 'i time TimedLeaf', 'q', '',
                ]
                result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(script)],
                    input='\n'.join(commands), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    universal_newlines=True, timeout=30, cwd=str(launch_dir),
                    env=dict(os.environ, IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
                output = re.sub(r'\x1b\[[0-9;]*m', '', result.stdout)
                self.assertEqual(result.returncode, 0, output)
                self.assertNotIn('Traceback', output)
                self.assertNotIn('RuntimeError: STALE_TIMING_SOURCE', output)

                def Section(start, end=None):
                    self.assertIn('TIMING_' + start, output)
                    content = output.split('TIMING_' + start, 1)[1]
                    if end is not None:
                        self.assertIn('TIMING_' + end, content)
                        content = content.split('TIMING_' + end, 1)[0]
                    return content

                default = Section('DEFAULT', 'ALL')
                all_functions = Section('ALL', 'FILTERED')
                self.assertIn('Function timing (', default)
                table_row = r'(?m)^[ \t]*\d+[ \t]+[0-9.]+[ \t]+[0-9.]+[ \t]+.*\([^()\n]+\)$'
                self.assertEqual(len(re.findall(table_row, default)), 20, default)
                self.assertEqual(len(set(re.findall(r'Extra\d\d', all_functions))), 23, all_functions)
                leaf_row = r'(?m)^\s*2\s+[0-9.]+\s+[0-9.]+\s+.*\(TimedLeaf\)\s*$'
                filtered = Section('FILTERED', 'COMPLETE')
                self.assertRegex(filtered, leaf_row)
                self.assertNotRegex(filtered, r'Extra\d\d')
                completed = Section('COMPLETE', 'OFF')
                self.assertIn('TIMED_WORKLOAD 42', completed)
                outer_row = re.search(r'(?m)^\s*1\s+[0-9.]+\s+([0-9.]+)\s+.*\(TimedOuter\)\s*$', completed)
                self.assertIsNotNone(outer_row, completed)
                self.assertGreaterEqual(float(outer_row.group(1)), 0.05, completed)
                untimed = Section('OFF', 'RETAINED')
                self.assertIn('TIMED_WORKLOAD 42', untimed)
                self.assertNotIn('Function timing (', untimed)
                retained = Section('RETAINED')
                self.assertIn('timing off', retained)
                self.assertRegex(retained, leaf_row)

                reports = re.findall(r'Callgrind report: ([^\r\n]+)', output)
                self.assertTrue(reports, output)
                self.assertEqual(len(set(reports)), 1, output)
                report_path = (launch_dir / reports[0].strip()).resolve()
                self.assertEqual(report_path.parent, (launch_dir / 'ipdCache').resolve())
                self.assertEqual(list(report_path.parent.glob('callgrind.out.*')), [report_path])
                self.assertEqual(Path(breakpoint_path.read_text()).resolve(), report_path)
                paused_report = breakpoint_report.read_text(encoding='utf-8')
                self.assertRegex(paused_report, r'(?m)^events:\s+Time\s*$')
                self.assertRegex(paused_report, r'(?m)^fn=TimedLeaf:\d+$')
                report = report_path.read_text(encoding='utf-8')
                self.assertNotEqual(paused_report, report)
                self.assertRegex(report, r'(?m)^events:\s+Time\s*$')
                self.assertRegex(report, r'(?m)^\d+ \d+$')
                outer = [section for section in re.split(r'(?m)^fn=', report)
                         if 'TimedOuter' in section.partition('\n')[0]]
                self.assertEqual(len(outer), 1, report)
                self.assertRegex(outer[0], r'(?m)^cfn=.*TimedLeaf(?::\d+)?\n(?:[^\n]+\n)*?calls=2\s+\d+')

    def test_exit_callbacks_run_once_in_reverse_order(self):
        for ending, commands in [('pass', 'c\nq\n'), ('sys.exit(7)', 'c\nc\nq\n')]:
            with self.subTest(ending=ending), tempfile.TemporaryDirectory() as directory:
                script = Path(directory) / 'callbacks.py'
                script.write_text('import atexit, sys\natexit.register(print, "FIRST_CALLBACK")\natexit.register(print, "LAST_CALLBACK")\n' + ending + '\n')
                launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
                result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(script)],
                    input=commands, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    universal_newlines=True, timeout=30,
                    env=dict(os.environ, IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(len(re.findall(r'(?m)^(?:ipdb> )?LAST_CALLBACK\s*$', result.stdout)), 1, result.stdout)
                self.assertEqual(len(re.findall(r'(?m)^FIRST_CALLBACK\s*$', result.stdout)), 1, result.stdout)
                self.assertLess(result.stdout.index('LAST_CALLBACK\nFIRST_CALLBACK'), result.stdout.index('FIRST_CALLBACK', result.stdout.index('LAST_CALLBACK\nFIRST_CALLBACK')))

    def test_exit_preserves_locals_and_allows_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'exit_sample.py'
            script.write_text('import sys\ndef work():\n    saved = 42\n    sys.exit(7)\nwork()\n')
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(script)],
                input='c\np saved\nbt\nr\nc\np saved\nq\n', stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, universal_newlines=True, timeout=30,
                env=dict(os.environ, IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(result.stdout.count('Program requested exit: 7'), 2, result.stdout)
            self.assertGreaterEqual(len(re.findall(r'(?m).*42\s*$', result.stdout)), 2, result.stdout)
            self.assertIn('sys.exit(7)', re.sub(r'\x1b\[[0-9;]*m', '', result.stdout))


    def test_restart_from_post_mortem_keeps_session(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'broken.py'
            script.write_text('raise ValueError("original failure")\n')
            commands = ["!print('SESSION_PID', __import__('os').getpid())", 'c',
                        "!open({!r}, 'w').write('print(\"RECOVERED\")\\n')".format(str(script)),
                        'r', "!print('SESSION_PID', __import__('os').getpid())", 'c', 'q', '']
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(script)],
                input='\n'.join(commands), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, timeout=30, cwd=directory,
                env=dict(os.environ, IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('RECOVERED', result.stdout)
            self.assertNotIn('pdb.Restart', result.stdout)
            pids = re.findall(r'SESSION_PID (\d+)', result.stdout)
            self.assertEqual(len(pids), 2, result.stdout)
            self.assertEqual(pids[0], pids[1], result.stdout)

    def test_relocation_requires_unambiguous_source(self):
        old = 'def first():\n    value = 1\n    return value\ndef second():\n    return value\n'.splitlines(True)
        new = '# inserted\n\n' + ''.join(old)
        self.assertEqual(Relocate(old, new.splitlines(True), 3), 5)
        deleted = 'def first():\n    value = 1\ndef second():\n    return value\n'.splitlines(True)
        self.assertIsNone(Relocate(old, deleted, 3))
        ambiguous = 'value = 1\nvalue = 1\n'.splitlines(True)
        self.assertIsNone(Relocate(['value = 1\n'], ambiguous, 1))
        self.assertIsNone(Relocate(ambiguous, ['value = 1\n'], 1))

    def test_saved_script_and_import_reload_preserving_breakpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / 'helper.py'
            helper.write_text('VALUE = 10\n')
            script = root / 'sample.py'
            script.write_text('import helper\nprint("OLD", helper.VALUE)\nsentinel = 0\n')
            py_compile.compile(str(helper), doraise=True)
            helper_stamp = helper.stat().st_mtime
            commands = [
                "!print('SESSION_PID', __import__('os').getpid())",
                'b 3', 'c',
                "!open({!r}, 'w').write('VALUE = 20\\n')".format(str(helper)),
                "!__import__('os').utime({!r}, ({!r}, {!r}))".format(str(helper), helper_stamp, helper_stamp),
                "!open({!r}, 'w').write('this is invalid python !!!\\n')".format(str(script)),
                'r',
                "!open({!r}, 'w').write('# inserted\\n\\nimport helper\\nprint(\"NEW\", helper.VALUE)\\nsentinel = 0\\n')".format(str(script)),
                'r', 'c', 'p helper.VALUE',
                "!print('SESSION_PID', __import__('os').getpid())",
                "!print('BREAKPOINT_LINE', __import__('bdb').Breakpoint.bpbynumber[1].line)",
                'q', '']
            launcher = Path(__file__).with_name('portable_ipdb.py').resolve()
            result = subprocess.run([sys.executable, '-B', str(launcher), '--plain', str(script)],
                input='\n'.join(commands), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, timeout=30, cwd=directory,
                env=dict(os.environ, IPY_TEST_SIMPLE_PROMPT='1', IPYTHONDIR=directory))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('OLD 10', result.stdout)
            self.assertIn('NEW 20', result.stdout)
            self.assertIn('Reloading saved project source', result.stdout)
            self.assertIn('Cannot reload saved source', result.stdout)
            self.assertIn('BREAKPOINT_LINE 5', result.stdout)
            pids = re.findall(r'SESSION_PID (\d+)', result.stdout)
            self.assertEqual(len(pids), 2, result.stdout)
            self.assertEqual(pids[0], pids[1], result.stdout)
            self.assertNotIn('Traceback', result.stdout)


if __name__ == '__main__':
    unittest.main()
