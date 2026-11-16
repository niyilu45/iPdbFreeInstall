"""Choose a launcher mode without importing debugger dependencies."""
import os
import sys


def SelectMode(arguments, platform=None, interactive=None, terminal=None):
    arguments = list(arguments)
    if arguments and arguments[0] in ('--tui', '--plain', '--pudb', '--source-vim', '--source-gvim'):
        return arguments[0][2:], arguments[1:]
    platform = sys.platform if platform is None else platform
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    terminal = os.environ.get('TERM', '') if terminal is None else terminal
    if arguments and arguments[0] not in ('-h', '--help') and platform.startswith('linux') and interactive and terminal not in ('', 'dumb'):
        return 'keys', arguments
    return 'plain', arguments
