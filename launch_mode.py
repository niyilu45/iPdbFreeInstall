"""Choose a launcher mode without importing debugger dependencies."""
import os
import sys


MODES = ('--tui', '--plain', '--pudb', '--source-vim', '--source-gvim')


def CheckpointOption(arguments, option='--checkpoint'):
    arguments = list(arguments)
    if arguments[:1] == [option]:
        return True, arguments[1:]
    if len(arguments) > 1 and arguments[0] in MODES and arguments[1] == option:
        return True, arguments[:1] + arguments[2:]
    return False, arguments


def SelectMode(arguments, platform=None, interactive=None, terminal=None):
    arguments = list(arguments)
    if arguments and arguments[0] in MODES:
        return arguments[0][2:], arguments[1:]
    platform = sys.platform if platform is None else platform
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    terminal = os.environ.get('TERM', '') if terminal is None else terminal
    if arguments and arguments[0] not in ('-h', '--help') and platform.startswith('linux') and interactive and terminal not in ('', 'dumb'):
        return 'keys', arguments
    return 'plain', arguments
