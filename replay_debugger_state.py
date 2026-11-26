"""Serializable debugger configuration for Windows checkpoint replay."""

import bdb


BREAKPOINT_FIELDS = (
    'number', 'file', 'line', 'funcname', 'func_first_executable_line',
    'temporary', 'cond', 'enabled', 'ignore', 'hits',
)


def SaveDebugger(debugger):
    breakpoints = []
    for entries in bdb.Breakpoint.bplist.values():
        for breakpoint in entries:
            saved = {name: getattr(breakpoint, name) for name in BREAKPOINT_FIELDS}
            if hasattr(breakpoint, '_portable_source'):
                saved['_portable_source'] = list(breakpoint._portable_source)
            breakpoints.append(saved)

    displays = [
        {'number': number, 'expression': entry['expression'], 'enabled': entry['enabled']}
        for number, entry in getattr(debugger, '_portable_displays', {}).items()
    ]
    return {
        'breakpoints': breakpoints,
        'breakpoint_next': bdb.Breakpoint.next,
        'breaks': {filename: list(lines) for filename, lines in debugger.breaks.items()},
        'displays': displays,
        'display_next': getattr(debugger, '_portable_display_next', 1),
    }


def RestoreDebugger(debugger, saved):
    breakpoint_type = bdb.Breakpoint
    by_number = [None] * saved['breakpoint_next']
    by_location = {}
    for entry in saved['breakpoints']:
        breakpoint = breakpoint_type.__new__(breakpoint_type)
        for name in BREAKPOINT_FIELDS:
            setattr(breakpoint, name, entry[name])
        if '_portable_source' in entry:
            breakpoint._portable_source = list(entry['_portable_source'])
        by_number[breakpoint.number] = breakpoint
        by_location.setdefault((breakpoint.file, breakpoint.line), []).append(breakpoint)

    displays = {}
    for entry in saved['displays']:
        expression = entry['expression']
        displays[entry['number']] = {
            'expression': expression,
            'code': compile(expression, '<display>', 'eval'),
            'enabled': entry['enabled'],
        }
    breaks = {filename: list(lines) for filename, lines in saved['breaks'].items()}

    breakpoint_type.next = saved['breakpoint_next']
    breakpoint_type.bplist = by_location
    breakpoint_type.bpbynumber = by_number
    debugger.breaks = breaks
    debugger._portable_displays = displays
    debugger._portable_display_next = saved['display_next']
