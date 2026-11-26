"""Single-threaded Linux fork checkpoints with a stable supervisor."""
import json
import os
import signal
import socket
import tempfile
import sys
import threading


class Checkpoints:
    def __init__(self):
        self.directory = None
        self.supervised = False

    def Read(self):
        if self.directory is None:
            return {'entries': {}, 'next_id': 1}
        with open(os.path.join(self.directory, 'registry.json')) as stream:
            return json.load(stream)

    def Write(self, registry):
        path = os.path.join(self.directory, 'registry.json')
        with open(path + '.tmp', 'w') as stream:
            json.dump(registry, stream)
        os.replace(path + '.tmp', path)

    @property
    def entries(self):
        return {int(key): value for key, value in self.Read()['entries'].items()}

    @property
    def next_id(self):
        return self.Read()['next_id']

    def Send(self, entry, message):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(entry['socket'])
            connection.sendall((json.dumps(message) + '\n').encode('ascii'))
            reply = connection.makefile('rb').readline()
            if reply != b'OK\n':
                raise OSError('Checkpoint request failed: ' + reply.decode('ascii', 'replace').strip())

    def Delete(self, number):
        registry = self.Read()
        entry = registry['entries'].pop(str(number), None)
        if entry is None:
            raise ValueError('Unknown checkpoint {}'.format(number))
        try:
            self.Send(entry, {'action': 'delete'})
        except (BrokenPipeError, ConnectionRefusedError, FileNotFoundError):
            pass
        self.Write(registry)

    def Cleanup(self):
        for number in list(self.entries):
            self.Delete(number)

    def Reset(self):
        self.Cleanup()
        if self.directory is not None:
            self.Write({'entries': {}, 'next_id': 1})

    def Create(self, debugger):
        if sys.platform == 'win32':
            raise ValueError('Checkpoints require Linux process snapshots. In WSL, run ./ipdbVim.sh with Linux Python.')
        if not sys.platform.startswith('linux'):
            raise ValueError('Checkpoints require Linux process snapshots. Run the debugger on Linux.')
        if not self.supervised:
            raise ValueError('This session has no checkpoint supervisor. Launch the script with ./ipdb.sh or ./ipdbVim.sh.')
        if len(self.entries) >= 8:
            raise ValueError('At most 8 live checkpoints; delete one first')
        if threading.current_thread() is not threading.main_thread():
            raise ValueError('Checkpoints require the main thread')
        from IPython import get_ipython
        shell = get_ipython()
        history = getattr(shell, 'history_manager', None)
        worker = getattr(history, 'save_thread', None)
        if worker is not None and worker.is_alive():
            worker.stop()
        if history is not None:
            history.enabled = False
        if threading.active_count() != 1 or len(os.listdir('/proc/self/task')) != 1:
            raise ValueError('Checkpoint refused: other Python or native threads are active')
        sys.stdout.flush()
        sys.stderr.flush()
        registry = self.Read()
        number = registry['next_id']
        address = os.path.join(self.directory, str(number) + '.sock')
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(address)
        listener.listen(1)
        registry['next_id'] += 1
        registry['entries'][str(number)] = {
            'socket': address, 'pid': os.getpid(),
            'file': debugger.curframe.f_code.co_filename,
            'line': debugger.curframe.f_lineno}
        self.Write(registry)
        try:
            child = os.fork()
        except BaseException:
            listener.close()
            os.unlink(address)
            del registry['entries'][str(number)]
            registry['next_id'] = number
            self.Write(registry)
            raise
        if child == 0:
            listener.close()
            debugger.message('Checkpoint {} created'.format(number))
            return
        # Keep this memory image frozen. Every activation forks a fresh copy.
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        child_signal = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
        while True:
            connection, _ = listener.accept()
            with connection:
                message = json.loads(connection.makefile('rb').readline().decode('ascii'))
                if message['action'] == 'delete':
                    listener.close()
                    os.unlink(address)
                    connection.sendall(b'OK\n')
                    os._exit(0)
                try:
                    restored = os.fork()
                except OSError as error:
                    connection.sendall(('ERROR: ' + str(error) + '\n').encode('ascii', 'replace'))
                    continue
                if restored:
                    connection.sendall(b'OK\n')
                    continue
                # Wait for the old active process to release the connection
                # before returning to the prompt and reading terminal input.
                while connection.recv(1):
                    pass
                connection.close()
                listener.close()
                signal.signal(signal.SIGINT, previous)
                signal.signal(signal.SIGCHLD, child_signal)
                debugger.message('Restored checkpoint {}'.format(number))
                timing = getattr(debugger, '_portable_timing', None)
                if timing is not None:
                    from function_timing import UpdateTimingReport
                    # Restored counters may precede the saved report on disk.
                    # Publish the restored branch without overwriting its source branch.
                    timing.report = None
                    timing.report_error = None
                    UpdateTimingReport(debugger, timing, allow_empty=True, announce=True)
                debugger.print_stack_entry(debugger.stack[debugger.curindex])
                return

    def Restart(self, number):
        target = self.entries.get(number)
        if target is None:
            raise ValueError('Unknown checkpoint {}'.format(number))
        self.Send(target, {'action': 'restart'})
        os._exit(0)


Manager = Checkpoints()


def Supervise(callback):
    """Keep the terminal session leader alive across snapshot activation."""
    if not sys.platform.startswith('linux'):
        return callback()
    directory = tempfile.TemporaryDirectory(prefix='ipdb-check-')
    Manager.directory = directory.name
    Manager.Write({'entries': {}, 'next_id': 1})
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        Manager.supervised = True
        code = 0
        try:
            callback()
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else (0 if error.code is None else 1)
        except BaseException:
            import traceback
            traceback.print_exc()
            code = 1
        finally:
            Manager.Cleanup()
            # os._exit is required for fork workers, but skips Python shutdown.
            import atexit
            atexit._run_exitfuncs()
            os.write(write_fd, str(code).encode('ascii'))
            os.close(write_fd)
            sys.stdout.flush()
            sys.stderr.flush()
        os._exit(code)
    os.close(write_fd)
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    data = b''
    try:
        while True:
            chunk = os.read(read_fd, 128)
            if not chunk:
                break
            data += chunk
        _, status = os.waitpid(child, 0)
    finally:
        os.close(read_fd)
        signal.signal(signal.SIGINT, previous)
        directory.cleanup()
        Manager.directory = None
    if data:
        return int(data)
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)
