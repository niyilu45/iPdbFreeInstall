"""Supervise native Windows checkpoint replay without transferring console input early."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid


HANDOFF_EXIT = 197
READY_TIMEOUT = 15
POLL_INTERVAL = 0.025


def ReadJson(path):
    with Path(path).open(encoding='utf-8') as stream:
        return json.load(stream)


def WriteJson(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def StartWorker(launcher, arguments, root, token, number=None):
    environment = dict(os.environ, IPDB_REPLAY_SESSION=str(root), IPDB_REPLAY_WORKER=token,
                       PYTHONHASHSEED='0', IPYTHONDIR=str(root / ('ipython-' + token)))
    environment.pop('IPDB_REPLAY_TARGET', None)
    if number is not None:
        environment['IPDB_REPLAY_TARGET'] = str(number)
    return subprocess.Popen([sys.executable, str(launcher), '--plain'] + list(arguments), env=environment)


def StopWorker(process):
    if process is not None and process.poll() is None:
        process.kill()
        process.wait()


def PrepareCandidate(launcher, arguments, root, active, request, used_tokens):
    number = request.get('number') if isinstance(request, dict) else None
    token = request.get('token') if isinstance(request, dict) else None
    if type(number) is not int or number < 1:
        return None, None, 'Invalid checkpoint number.'
    if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token) or token in used_tokens:
        return None, None, 'Invalid checkpoint replay request token.'
    used_tokens.add(token)

    candidate = None
    try:
        if str(number) not in ReadJson(root / 'registry.json')['entries']:
            return None, None, 'Unknown checkpoint {}'.format(number)
        candidate = StartWorker(launcher, arguments, root, token, number)
        ready = root / ('ready-' + token + '.json')
        deadline = time.monotonic() + READY_TIMEOUT
        while active.poll() is None:
            status = candidate.poll()
            if status is not None:
                return None, None, 'Checkpoint replay exited with status {} before activation.'.format(status)
            try:
                result = ReadJson(ready)
            except FileNotFoundError:
                if time.monotonic() >= deadline:
                    return None, None, 'Checkpoint replay timed out after {} seconds.'.format(READY_TIMEOUT)
                time.sleep(POLL_INTERVAL)
                continue
            if not isinstance(result, dict) or 'error' not in result:
                return None, None, 'Invalid checkpoint replay readiness result.'
            if result['error'] is not None:
                return None, None, str(result['error'])
            prepared, candidate = candidate, None
            return prepared, token, None
        return None, None, 'The original debugger exited before checkpoint replay was ready.'
    except (OSError, ValueError) as error:
        return None, None, 'Could not prepare checkpoint replay: {}'.format(error)
    finally:
        StopWorker(candidate)


def SuperviseReplay(launcher, arguments):
    """Keep the terminal attached while a verified replay worker takes over."""
    arguments = list(arguments)
    # Ctrl+C still goes to the same console and its debugger. A Python handler
    # keeps this supervisor alive without enabling inherited SIGINT ignoring.
    previous = signal.signal(signal.SIGINT, lambda _number, _frame: None)
    try:
        with tempfile.TemporaryDirectory(prefix='ipdb-replay-') as directory:
            root = Path(directory)
            WriteJson(root / 'registry.json', {'entries': {}, 'next_id': 1})
            active_token = uuid.uuid4().hex
            used_tokens = {active_token}
            active = StartWorker(launcher, arguments, root, active_token)
            candidate = None
            candidate_token = None
            try:
                while True:
                    status = active.poll()
                    if status is not None:
                        if status != HANDOFF_EXIT or candidate is None:
                            return status
                        if candidate.poll() is not None:
                            print('[ipdb] Checkpoint replay exited before taking over the terminal.', file=sys.stderr)
                            return 1
                        (root / ('activate-' + candidate_token)).touch(exist_ok=False)
                        active, candidate = candidate, None
                        active_token, candidate_token = candidate_token, None
                    elif candidate is None:
                        request_path = root / ('request-' + active_token + '.json')
                        if request_path.is_file():
                            try:
                                request = ReadJson(request_path)
                            except (OSError, ValueError) as error:
                                failure = 'Could not read checkpoint replay request: {}'.format(error)
                            else:
                                candidate, candidate_token, failure = PrepareCandidate(
                                    launcher, arguments, root, active, request, used_tokens)
                            try:
                                request_path.unlink()
                            except FileNotFoundError:
                                pass
                            if active.poll() is not None:
                                StopWorker(candidate)
                                candidate = None
                                continue
                            if candidate is not None and candidate.poll() is not None:
                                candidate = None
                                failure = 'Checkpoint replay exited before the handoff was confirmed.'
                            try:
                                WriteJson(root / ('response-' + active_token + '.json'), {'error': failure})
                            except OSError as error:
                                StopWorker(candidate)
                                candidate = None
                                print('[ipdb] Could not report checkpoint replay result: {}'.format(error), file=sys.stderr)
                    time.sleep(POLL_INTERVAL)
            finally:
                try:
                    StopWorker(candidate)
                finally:
                    StopWorker(active)
    finally:
        signal.signal(signal.SIGINT, previous)
