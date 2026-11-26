"""Choose a launcher mode without importing debugger dependencies."""
import os
import sys


MODES = ('--tui', '--plain', '--pudb', '--source-vim', '--source-gvim')


def CheckpointOption(arguments):
    arguments = list(arguments)
    if arguments[:1] == ['--checkpoint']:
        return True, arguments[1:]
    if len(arguments) > 1 and arguments[0] in MODES and arguments[1] == '--checkpoint':
        return True, arguments[:1] + arguments[2:]
    return False, arguments


def WslCheckpoint(arguments, launcher):
    import getopt
    import ntpath
    import shutil
    import subprocess

    wsl = shutil.which('wsl.exe')
    if not wsl:
        raise RuntimeError('The Windows checkpoint option requires WSL with a Linux distribution.')
    index = 1 if arguments and arguments[0] in MODES else 0
    if len(arguments) <= index:
        raise RuntimeError('Usage: ipdbVim.cmd --checkpoint script.py [arguments]')
    try:
        options, script_args = getopt.getopt(arguments[index:], 'mhc:', ['help', 'command='])
    except getopt.GetoptError:
        options, script_args = [], []  # Let the Linux launcher report invalid options.
    convert = 0
    if script_args and not any(option == '-m' for option, _ in options):
        target = script_args[0]
        if ntpath.splitdrive(target)[0] or '\\' in target:
            convert = len(arguments) - len(script_args) + 1
    # Keep the Linux program fixed and pass paths and Python arguments as argv.
    bridge = r'''set -eu
launcher=$(wslpath -a -u "$1")
position=$2
shift 2
if [ "$position" -ne 0 ]; then
    for argument do
        shift
        if [ "$position" -eq 1 ]; then argument=$(wslpath -a -u "$argument"); fi
        set -- "$@" "$argument"
        position=$((position - 1))
    done
fi
unset IPDB_PYTHON
exec sh "$launcher" "$@"
'''
    command = [wsl, '--cd', os.getcwd(), '--exec', 'sh', '-c', bridge, 'ipdb-checkpoint',
               str(launcher), str(convert)] + arguments
    print('[ipdb] Starting a checkpoint session with Linux Python in WSL...', file=sys.stderr, flush=True)
    process = subprocess.Popen(command)
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            # The debugger receives Ctrl+C through the same terminal.
            continue


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
