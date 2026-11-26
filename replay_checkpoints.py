"""Opt-in deterministic checkpoints for standalone scripts on native Windows."""
import ast
import bdb
import builtins
import hashlib
import json
import os
from pathlib import Path
import pdb
import sys
import time
import uuid

from replay_session import HANDOFF_EXIT, POLL_INTERVAL, READY_TIMEOUT, ReadJson, WriteJson
from replay_state import Fingerprint, ValidateSource


READ_ONLY_COMMANDS = frozenset('p pp whatis display condition print_format break tbreak'.split())
COMMANDS = READ_ONLY_COMMANDS | frozenset('''
    EOF _portable_noop _pdbcmd_print_frame_status help info args retval backtrace where w bt up frame list longlist l ll
    n next s step c cont continue until unt return finish run restart q quit exit
    enable disable clear ignore undisplay enable_display disable_display checkpoint delete_checkpoint
'''.split())


def CheckExpression(expression):
    tree = ast.parse(expression, mode='eval')
    for node in ast.walk(tree):
        if isinstance(node, (ast.Call, ast.Attribute, ast.Lambda, ast.comprehension)):
            raise ValueError('Replay mode requires read-only expressions without calls or attribute access.')
        if type(node).__name__ == 'NamedExpr' or isinstance(node, ast.Name) and node.id.startswith('__'):
            raise ValueError('Replay mode requires read-only expressions.')


def LoadSource(filename):
    data = Path(filename).read_bytes()
    digest = ValidateSource(filename, data)
    return digest, compile(data, filename, 'exec')


class ReplayCheckpoints:
    def __init__(self, directory, token, target, filename):
        self.root = Path(directory)
        self.token = token
        self.filename = filename
        self.target = target
        self.expected = None
        self.source_digest = None
        self.current_frame = None
        self.ordinal = 0
        self.events = hashlib.sha256()

    def Registry(self):
        return ReadJson(self.root / 'registry.json')

    @property
    def entries(self):
        return {int(number): entry for number, entry in self.Registry()['entries'].items()}

    def Reset(self):
        WriteJson(self.root / 'registry.json', {'entries': {}, 'next_id': 1})

    def Delete(self, number):
        registry = self.Registry()
        if registry['entries'].pop(str(number), None) is None:
            raise ValueError('Unknown checkpoint {}'.format(number))
        WriteJson(self.root / 'registry.json', registry)

    def Ready(self, error):
        WriteJson(self.root / ('ready-' + self.token + '.json'), {'error': error})

    def Create(self, debugger):
        from replay_debugger_state import SaveDebugger

        try:
            (self.root / 'replay-notice').touch(exist_ok=False)
        except FileExistsError:
            pass
        else:
            debugger.message('Restoring a replay checkpoint reruns the script from the start in a separate process.')
        frame = self.current_frame
        if frame is None or frame is not debugger.curframe:
            raise ValueError('Replay checkpoints require a live line stop in the innermost script frame.')
        registry = self.Registry()
        if len(registry['entries']) >= 8:
            raise ValueError('At most 8 replay checkpoints; delete one first.')
        number = registry['next_id']
        registry['entries'][str(number)] = {
            'file': self.filename, 'line': frame.f_lineno, 'source': self.source_digest,
            'event': self.ordinal, 'trace': self.events.hexdigest(),
            'state': Fingerprint(frame, self.filename), 'debugger': SaveDebugger(debugger),
        }
        registry['next_id'] += 1
        WriteJson(self.root / 'registry.json', registry)
        debugger.message('Replay checkpoint {} created'.format(number))

    def Restart(self, number):
        if number not in self.entries:
            raise ValueError('Unknown checkpoint {}'.format(number))
        response = self.root / ('response-' + self.token + '.json')
        try:
            response.unlink()
        except FileNotFoundError:
            pass
        candidate = uuid.uuid4().hex
        print('[ipdb] Restoring checkpoint {}...'.format(number), flush=True)
        WriteJson(self.root / ('request-' + self.token + '.json'), {'number': number, 'token': candidate})
        deadline = time.monotonic() + READY_TIMEOUT + 5
        while time.monotonic() < deadline:
            try:
                if not response.exists():
                    time.sleep(POLL_INTERVAL)
                    continue
                result = ReadJson(response)
                if result['error']:
                    raise ValueError(result['error'])
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(HANDOFF_EXIT)
            except KeyboardInterrupt:
                continue  # Resolve the pending handoff before reading another key.
        raise ValueError('Checkpoint replay did not respond; the current debugger is still active.')

    def Prepare(self, digest):
        self.source_digest = digest
        self.ordinal = 0
        self.events = hashlib.sha256()
        if self.target is not None:
            self.expected = self.entries.get(self.target)
            if self.expected is None or self.expected['source'] != digest or self.expected['file'] != self.filename:
                raise ValueError('The saved source changed since this checkpoint was created.')

    def Activate(self, debugger, frame):
        from replay_debugger_state import RestoreDebugger

        expected = self.expected
        if (self.events.hexdigest() != expected['trace'] or
                Fingerprint(frame, self.filename) != expected['state']):
            raise ValueError('Checkpoint replay reached a different execution path or variable state.')
        RestoreDebugger(debugger, expected['debugger'])
        self.Ready(None)
        marker = self.root / ('activate-' + self.token)
        deadline = time.monotonic() + READY_TIMEOUT + 5
        while not marker.exists():
            if time.monotonic() >= deadline:
                raise ValueError('Checkpoint replay was not activated by the Windows launcher.')
            try:
                time.sleep(POLL_INTERVAL)
            except KeyboardInterrupt:
                continue
        number, self.target = self.target, None
        self.expected = None
        debugger._wait_for_mainpyfile = False
        debugger.set_step()
        debugger.message('Restored replay checkpoint {}'.format(number))

    def Install(self, debugger):
        original_trace = debugger.trace_dispatch
        original_precmd = debugger.precmd

        def Continue():
            debugger._set_stopinfo(debugger.botframe, None, -1)

        def Trace(frame, event, argument):
            if debugger.botframe is None:
                return original_trace(frame, event, argument)
            if frame.f_code.co_filename != self.filename:
                return None
            if event == 'line':
                self.ordinal += 1
                stamp = [frame.f_code.co_name, frame.f_code.co_firstlineno, frame.f_lineno, frame.f_lasti]
                self.events.update(json.dumps(stamp, separators=(',', ':')).encode('ascii'))
            if self.target is not None:
                if event != 'line' or self.ordinal != self.expected['event']:
                    return Trace
                self.Activate(debugger, frame)
            self.current_frame = frame if event == 'line' else None
            try:
                result = original_trace(frame, event, argument)
                return result if debugger.quitting else Trace
            finally:
                self.current_frame = None

        def Precmd(line):
            from gdb_commands import SplitArgument

            translated = original_precmd(line)
            command, argument = SplitArgument(translated)
            try:
                if not command:
                    return Precmd(debugger.lastcmd) if debugger.lastcmd else translated
                if command not in COMMANDS or command == 'run' and argument:
                    raise ValueError('That debugger command is unavailable in replay mode.')
                if command == 'run' or command == 'restart' and not argument:
                    debugger._portable_replay_next_source = LoadSource(self.filename)
                if command in READ_ONLY_COMMANDS and argument:
                    expression = argument
                    if command in ('condition', 'print_format'):
                        expression = SplitArgument(argument)[1]
                    elif command in ('break', 'tbreak'):
                        expression = argument.partition(',')[2].strip()
                    if expression:
                        CheckExpression(expression)
            except (OSError, ValueError, SyntaxError) as error:
                debugger.error(str(error))
                return '_portable_noop'
            return translated

        debugger.set_continue = Continue
        debugger.trace_dispatch = Trace
        debugger.precmd = Precmd

    def Namespace(self):
        def Emit(*values, **options):
            if self.target is None:
                builtins.print(*values, **options)

        namespace = dict(vars(builtins), print=Emit)
        return {'__name__': '__main__', '__file__': self.filename, '__builtins__': namespace}


def RunReplayWorker(arguments, module):
    import checkpoints
    from IPython import get_ipython
    from reload_code import RefreshBreakpoints

    session = os.environ['IPDB_REPLAY_SESSION']
    token = os.environ['IPDB_REPLAY_WORKER']
    target = os.environ.get('IPDB_REPLAY_TARGET')
    filename = os.path.normcase(os.path.realpath(arguments[0]))
    manager = ReplayCheckpoints(session, token, int(target) if target else None, filename)
    checkpoints.Manager = manager
    try:
        source = LoadSource(filename)
        manager.Prepare(source[0])
        sys.argv = [filename] + arguments[1:]
        module._get_debugger_cls()  # Initialize the isolated shell before building its prompt.
        shell = get_ipython()
        if shell is not None and hasattr(shell, 'debugger_history_file'):
            shell.debugger_history_file = str(Path(os.environ['IPYTHONDIR']) / 'pdbhistory')
        debugger = module._init_pdb()
        debugger.rcLines.clear()
        debugger.mainpyfile = filename
        manager.Install(debugger)
        if target is None:
            debugger.message('Native Windows replay is active for this deterministic script.')
        while True:
            debugger._wait_for_mainpyfile = True
            debugger._user_requested_quit = False
            try:
                debugger.run(source[1], manager.Namespace())
            except pdb.Restart:
                source = debugger._portable_replay_next_source
                manager.Prepare(source[0])
                manager.Reset()
                RefreshBreakpoints(debugger)
                continue
            if manager.target is not None:
                raise ValueError('The script finished before reaching the replay checkpoint.')
            if debugger._user_requested_quit:
                return 0
            debugger.message('The program finished and will be restarted.')
            manager.Prepare(source[0])
    except (Exception, KeyboardInterrupt) as error:
        if manager.target is not None:
            manager.Ready(str(error) or type(error).__name__)
            time.sleep(READY_TIMEOUT + 1)  # The supervisor reads the error before retiring this worker.
        else:
            print('[ipdb] {}'.format(error), file=sys.stderr, flush=True)
        return 1
