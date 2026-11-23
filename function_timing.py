"""Function timing with a clock that stops while the debugger is paused."""
import cProfile
import os
from pathlib import Path
import tempfile
from time import perf_counter
from uuid import uuid4


def Counts(entries):
    counts = {}
    for entry in entries:
        counts[None, entry.code] = entry.callcount
        for call in entry.calls or ():
            counts[entry.code, call.code] = call.callcount
    return counts


class FunctionTiming:
    def __init__(self, root):
        self.root = root
        self.paused_at = None
        self.paused_seconds = 0.0
        self.paused_calls = None
        self.excluded_calls = {}
        self.profiler = cProfile.Profile(self.Clock)
        self.report = None
        self.report_error = None
        self.seen_prompt = False

    def Clock(self):
        now = perf_counter() if self.paused_at is None else self.paused_at
        return now - self.paused_seconds

    def Pause(self):
        # Leave the profiler installed so calls spanning a stop keep their timing.
        self.paused_at = perf_counter()
        self.paused_calls = Counts(self.profiler.getstats())

    def Resume(self):
        # Calls made while evaluating debugger commands belong to the prompt.
        for key, count in Counts(self.profiler.getstats()).items():
            added = count - self.paused_calls.get(key, 0)
            if added > 0:
                self.excluded_calls[key] = self.excluded_calls.get(key, 0) + added
        self.paused_calls = None
        self.paused_seconds += perf_counter() - self.paused_at
        self.paused_at = None

    def Count(self, caller, callee, count):
        key = (caller, callee)
        if self.paused_calls is not None:
            count = self.paused_calls.get(key, 0)
        return count - self.excluded_calls.get(key, 0)

    def Snapshot(self):
        entries = {}
        project = {}
        rows = []
        for entry in self.profiler.getstats():
            code = entry.code
            count = self.Count(None, code, entry.callcount)
            if count <= 0:
                continue
            entries[code] = entry
            if isinstance(code, str) or code.co_filename.startswith('<'):
                continue
            try:
                path = Path(code.co_filename).resolve().relative_to(self.root)
            except (OSError, RuntimeError, ValueError):
                continue
            project[code] = path
            location = '{}:{}({})'.format(path, code.co_firstlineno, code.co_name)
            rows.append((count, entry.inlinetime, entry.totaltime, code.co_name, location))
        rows.sort(key=lambda row: (-row[2], row[4]))
        return rows, entries, project

    def SaveReport(self, directory, entries, project):
        edges = {}
        pending = list(project)
        while pending:
            code = pending.pop()
            if code in edges:
                continue
            outgoing = []
            for call in entries[code].calls or ():
                count = self.Count(code, call.code, call.callcount)
                if count > 0 and call.code in entries:
                    outgoing.append((call.code, count, Microseconds(call.totaltime)))
                    pending.append(call.code)
            edges[code] = outgoing

        locations = {code: Location(code, self.root, project) for code in edges}
        costs = {code: Microseconds(entries[code].inlinetime) for code in edges}
        directory = directory / 'ipdCache'
        directory.mkdir(parents=True, exist_ok=True)
        report_path = self.report or directory / 'callgrind.out.{}.{}'.format(os.getpid(), uuid4().hex)
        staging = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                    dir=str(directory), prefix='.callgrind-', delete=False) as stream:
                staging = Path(stream.name)
                stream.write('# callgrind format\nversion: 1\ncreator: Portable ipdb (cProfile)\n')
                stream.write('positions: line\nevent: Time : Active wall time (microseconds)\nevents: Time\n')
                stream.write('summary: {}\n'.format(sum(costs.values())))
                if not edges:
                    stream.write('\nfl=[Python]\nfn=[No completed calls]\n0 0\n')
                for code in sorted(edges, key=locations.get):
                    source, line, name = locations[code]
                    stream.write('\nfl={}\nfn={}\n{} {}\n'.format(source, name, line, costs[code]))
                    for target, count, cost in sorted(edges[code], key=lambda edge: locations[edge[0]]):
                        callee_source, callee_line, callee_name = locations[target]
                        stream.write('cfl={}\ncfn={}\ncalls={} {}\n{} {}\n'.format(
                            callee_source, callee_name, count, callee_line, line, cost))
            os.replace(str(staging), str(report_path))
            self.report = report_path
        except BaseException:
            if staging is not None:
                try:
                    staging.unlink()
                except OSError:
                    pass
            raise


def UpdateTimingReport(debugger, timing, allow_empty=False, announce=False):
    rows, entries, project = timing.Snapshot()
    if rows or allow_empty:
        first_report = timing.report is None
        try:
            timing.SaveReport(debugger._portable_launch_directory, entries, project)
        except OSError as error:
            message = 'Could not write Callgrind report: {}'.format(error)
            if message != timing.report_error:
                debugger.error(message)
                timing.report_error = message
        else:
            timing.report_error = None
            if announce and first_report:
                debugger.message('Callgrind report: {}'.format(timing.report))
    return rows


def Microseconds(seconds):
    return max(0, round(seconds * 1000000))


def Location(code, root, project):
    if isinstance(code, str):
        return '[Python builtins]', 0, OneLine(code)
    source = code.co_filename
    if code in project:
        source = str(root / project[code])
    line = max(0, code.co_firstlineno)
    # The profiler gives function definitions and call counts, not call-site lines.
    return OneLine(source), line, '{}:{}'.format(OneLine(code.co_name), line)


def OneLine(value):
    return value.replace('\r', ' ').replace('\n', ' ')


def ShowTiming(debugger, argument='', rows=None):
    report = None
    if rows is None:
        timing = getattr(debugger, '_portable_timing', None)
        rows = timing.Snapshot()[0] if timing is not None else []
        report = timing.report if timing is not None else None
        if not rows and report is None:
            rows, report = getattr(debugger, '_portable_last_timing', ([], None))
            if rows:
                status = 'timing enabled' if getattr(debugger, '_portable_timing_enabled', False) else 'timing off'
                debugger.message('Showing the last completed timing run ({}).'.format(status))
    else:
        _, report = debugger._portable_last_timing
    if not rows:
        if getattr(debugger, '_portable_timing_enabled', False):
            debugger.message('No completed project calls measured yet.')
        else:
            debugger.message('No function timing data. Use rtime (rt) to start timing.')
        if report is not None:
            debugger.message('Callgrind report: {}'.format(report))
        return

    if argument and argument != 'all':
        query = argument.casefold()
        rows = [row for row in rows if query in row[3].casefold()]
    limit = None if argument else 20
    if rows:
        debugger.message('Function timing (seconds; sorted by cumulative time)')
        debugger.message('{:>8} {:>12} {:>12}  {}'.format('Calls', 'Self (s)', 'Total (s)', 'Function'))
        for count, own, total, _, location in rows[:limit]:
            debugger.message('{:8d} {:12.6f} {:12.6f}  {}'.format(count, own, total, location))
        if limit is not None and len(rows) > limit:
            debugger.message('Use info timing all to see all {} rows.'.format(len(rows)))
    else:
        debugger.message('No functions match {!r}.'.format(argument))
    if report is not None:
        debugger.message('Callgrind report: {}'.format(report))
