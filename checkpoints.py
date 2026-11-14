"""Single-threaded Linux fork checkpoints with a stable supervisor."""
import json
import os
import signal
import sys
import threading


class Checkpoints:
    def __init__(self):
        self.entries = {}
        self.next_id = 1
        self.supervised = False

    def Send(self, entry, message):
        data = (json.dumps(message) + '\n').encode('ascii')
        while data:
            data = data[os.write(entry['fd'], data):]

    def Delete(self, number):
        if number not in self.entries:
            raise ValueError('Unknown checkpoint {}'.format(number))
        entry = self.entries.pop(number)
        try:
            self.Send(entry, {'action': 'delete'})
        except BrokenPipeError:
            pass
        finally:
            os.close(entry['fd'])

    def Cleanup(self):
        for number in list(self.entries):
            self.Delete(number)

    def Create(self, debugger):
        if not sys.platform.startswith('linux') or not self.supervised:
            raise ValueError('Checkpoints require the Linux CLI launcher')
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
        number = self.next_id
        self.next_id += 1
        read_fd, write_fd = os.pipe()
        try:
            child = os.fork()
        except BaseException:
            os.close(read_fd)
            os.close(write_fd)
            raise
        if child:
            # This process is the frozen snapshot. The child keeps debugging.
            os.close(write_fd)
            previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
            data = b''
            while b'\n' not in data:
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    os._exit(0)
                data += chunk
            message = json.loads(data.decode('ascii'))
            os.close(read_fd)
            if message['action'] == 'delete':
                os._exit(0)
            # Restart abandons the continuation descended from this snapshot.
            # Reap its direct child before returning to the saved prompt.
            os.waitpid(child, 0)
            signal.signal(signal.SIGINT, previous)
            keep = message['keep']
            for old in list(self.entries):
                if old not in keep:
                    os.close(self.entries.pop(old)['fd'])
            self.next_id = message['next_id']
            debugger.message('Restored checkpoint {} (consumed)'.format(number))
            debugger.print_stack_entry(debugger.stack[debugger.curindex])
            return
        os.close(read_fd)
        self.entries[number] = {'fd': write_fd, 'pid': os.getppid(),
                                'file': debugger.curframe.f_code.co_filename,
                                'line': debugger.curframe.f_lineno}
        debugger.message('Checkpoint {} created'.format(number))

    def Restart(self, number):
        if number not in self.entries:
            raise ValueError('Unknown checkpoint {}'.format(number))
        for later in sorted(list(self.entries), reverse=True):
            if later > number:
                self.Delete(later)
        target = self.entries[number]
        self.Send(target, {'action': 'restart', 'next_id': self.next_id,
                           'keep': [key for key in self.entries if key < number]})
        os._exit(0)


Manager = Checkpoints()


def Supervise(callback):
    """Keep the terminal session leader alive across snapshot activation."""
    if not sys.platform.startswith('linux'):
        return callback()
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
    if data:
        return int(data)
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)
