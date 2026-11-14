"""Counted Ctrl-W resize keys for the private debugger tmux session."""
import subprocess
import sys


def Handle(tmux, socket, pane, client, key):
    command = [tmux, '-L', socket]
    def Call(*arguments):
        return subprocess.check_output(command + list(arguments), universal_newlines=True).strip()
    def Continue():
        Call('switch-client', '-c', client, '-T', 'ipdb-nav')
    if key == 'reset':
        Call('set-option', '-p', '-t', pane, '@ipdb-resize-count', '')
        Continue()
        return
    count = Call('show-options', '-p', '-qv', '-t', pane, '@ipdb-resize-count')
    count = count if count.isdigit() else ''
    if key in '0123456789' and len(key) == 1:
        Call('set-option', '-p', '-t', pane, '@ipdb-resize-count', str(min(9999, int((count + key) or '0'))))
        Continue()
        return
    Call('set-option', '-p', '-t', pane, '@ipdb-resize-count', '')
    amount = max(1, int(count or '1'))
    if key == '=':
        Call('select-layout', '-t', pane, 'even-vertical')
    elif key in ('+', '-'):
        height = int(Call('display-message', '-p', '-t', pane, '#{pane_height}'))
        Call('resize-pane', '-t', pane, '-y', str(max(3, height + (amount if key == '+' else -amount))))


if __name__ == '__main__':
    if len(sys.argv) == 6:
        Handle(*sys.argv[1:])
