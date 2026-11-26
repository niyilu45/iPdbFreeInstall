"""Validate and compare the program state supported by native replay."""
import ast
import builtins
import hashlib
import json
import marshal
import os
from pathlib import Path
import struct
import types


_BUILTINS = {name: getattr(builtins, name) for name in (
    'abs', 'all', 'any', 'ascii', 'bin', 'bool', 'bytearray', 'bytes', 'chr',
    'complex',
    'dict', 'divmod', 'enumerate', 'filter', 'float', 'format', 'frozenset',
    'hex', 'int', 'isinstance', 'iter', 'len', 'list', 'map', 'max', 'min',
    'next', 'oct', 'ord', 'pow', 'print', 'range', 'repr', 'reversed',
    'round', 'set', 'slice', 'sorted', 'str', 'sum', 'tuple', 'zip',
)}
_BUILTIN_IDS = {id(value): name for name, value in _BUILTINS.items()}
_RESERVED = frozenset(vars(builtins)) - frozenset(_BUILTINS)
_MODULE_METADATA = frozenset((
    '__name__', '__doc__', '__package__', '__loader__', '__spec__',
    '__annotations__', '__builtins__', '__file__', '__cached__', '__pdb_convenience_variables',
))
_READ_ONLY_NAMES = frozenset(('__name__', '__file__', '__doc__', '__debug__'))
_ATTRIBUTES = frozenset((
    'add', 'append', 'capitalize', 'casefold', 'center', 'clear', 'conjugate',
    'copy', 'count', 'difference', 'difference_update', 'discard', 'endswith',
    'extend', 'find', 'from_bytes', 'fromhex', 'fromkeys', 'get', 'hex',
    'imag', 'index', 'insert', 'intersection', 'intersection_update',
    'isalnum', 'isalpha', 'isascii', 'isdecimal', 'isdigit', 'isdisjoint',
    'islower', 'isnumeric', 'isspace', 'issubset', 'issuperset', 'isupper',
    'items', 'join', 'keys', 'ljust', 'lower', 'lstrip', 'partition', 'pop',
    'popitem', 'real', 'remove', 'replace', 'reverse', 'rfind', 'rindex',
    'rjust', 'rpartition', 'rsplit', 'rstrip', 'setdefault', 'sort', 'split',
    'splitlines', 'start', 'startswith', 'step', 'stop', 'strip', 'swapcase',
    'symmetric_difference', 'symmetric_difference_update', 'title',
    'to_bytes', 'union', 'update', 'upper', 'values', 'zfill',
))
_NODES = frozenset(getattr(ast, name) for name in (
    'Module', 'FunctionDef', 'arguments', 'arg', 'Lambda', 'Return', 'Delete',
    'Assign', 'AugAssign', 'AnnAssign', 'NamedExpr', 'For', 'While', 'If',
    'Assert', 'Global', 'Nonlocal', 'Expr', 'Pass', 'Break', 'Continue',
    'BoolOp', 'BinOp', 'UnaryOp', 'IfExp', 'Dict', 'Set', 'ListComp',
    'SetComp', 'DictComp', 'comprehension', 'Compare', 'Call', 'keyword',
    'FormattedValue', 'JoinedStr', 'Constant', 'Num', 'Str', 'Bytes',
    'NameConstant', 'Ellipsis', 'Attribute', 'Subscript', 'Starred', 'Name',
    'List', 'Tuple', 'Slice', 'ExtSlice', 'Index', 'Load', 'Store', 'Del',
) if hasattr(ast, name))
_BUILTIN_METHOD_OWNERS = frozenset(id(kind) for kind in (
    bool, int, float, complex, str, bytes, bytearray, list, tuple, dict, set,
    frozenset, range, slice,
))
_CELL = type((lambda value: lambda: value)(None).__closure__[0])


def _SourceError(node, reason):
    raise ValueError('Replay checkpoints do not support {} at line {}.'.format(
        reason, getattr(node, 'lineno', 1)))


def _IsUserName(name):
    return not name.startswith('__') and name not in _RESERVED


def ValidateSource(filename, data=None):
    """Return the digest of accepted deterministic, standalone script bytes."""
    if data is None:
        data = Path(filename).read_bytes()
    if type(data) is not bytes:
        raise TypeError('Replay checkpoint source must be exact bytes.')
    tree = ast.parse(data, filename=os.fspath(filename))
    bindings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bindings.add(node.id)
        elif isinstance(node, ast.FunctionDef):
            bindings.add(node.name)
        elif isinstance(node, ast.arg):
            bindings.add(node.arg)

    readable = bindings | set(_BUILTINS) | _READ_ONLY_NAMES
    for node in ast.walk(tree):
        if type(node) not in _NODES and not isinstance(node, (ast.operator, ast.unaryop, ast.boolop, ast.cmpop)):
            _SourceError(node, type(node).__name__)
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load) and node.id in _READ_ONLY_NAMES:
                continue
            if not _IsUserName(node.id) or isinstance(node.ctx, ast.Load) and node.id not in readable:
                _SourceError(node, 'access to {!r}'.format(node.id))
        elif isinstance(node, (ast.FunctionDef, ast.arg, ast.Global, ast.Nonlocal)):
            names = (node.name,) if isinstance(node, ast.FunctionDef) else (
                (node.arg,) if isinstance(node, ast.arg) else node.names)
            for name in names:
                if not _IsUserName(name):
                    _SourceError(node, 'binding {!r}'.format(name))
        elif isinstance(node, ast.Attribute):
            if not isinstance(node.ctx, ast.Load) or node.attr not in _ATTRIBUTES:
                _SourceError(node, 'attribute {!r}'.format(node.attr))
        elif isinstance(node, ast.keyword) and (node.arg is None or node.arg == 'file'):
            _SourceError(node, 'keyword unpacking or file= arguments')
    compile(tree, os.fspath(filename), 'exec')
    return hashlib.sha256(data).hexdigest()


def _Path(filename):
    return os.path.normcase(os.path.realpath(filename))


class _Graph:
    def __init__(self, filename):
        self.filename = filename
        self.nodes = []
        self.references = {}
        self.codes = {}

    def Code(self, code):
        identity = id(code)
        if identity not in self.codes:
            self.codes[identity] = hashlib.sha256(marshal.dumps(code)).hexdigest()
        return self.codes[identity]

    def Namespace(self, namespace, global_scope=False):
        if type(namespace) is not dict:
            raise ValueError('Replay checkpoints require ordinary Python namespaces.')
        names = []
        for name, value in namespace.items():
            if type(name) is not str:
                raise ValueError('Replay checkpoints require string variable names.')
            if not global_scope or name not in _MODULE_METADATA:
                names.append((name, value))
        return [[name, self.Reference(value)] for name, value in sorted(names, key=lambda item: item[0])]

    def Reference(self, value):
        identity = id(value)
        if identity in self.references:
            return self.references[identity]
        index = len(self.nodes)
        self.references[identity] = index
        self.nodes.append(None)
        self.nodes[index] = self.Value(value)
        return index

    def Value(self, value):
        kind = type(value)
        if value is None or value is Ellipsis or value is NotImplemented:
            return ['singleton', str(value)]
        if kind is bool or kind is str:
            return [kind.__name__, value]
        if kind is int:
            return ['int', hex(value)]
        if kind is float:
            return ['float', struct.pack('>d', value).hex()]
        if kind is complex:
            return ['complex', struct.pack('>dd', value.real, value.imag).hex()]
        if kind is bytes or kind is bytearray:
            return [kind.__name__, value.hex()]
        if kind is dict:
            return ['dict', [[self.Reference(key), self.Reference(item)] for key, item in value.items()]]
        if kind is tuple or kind is list or kind is set or kind is frozenset:
            # Preserve the order that the program can observe when iterating.
            return [kind.__name__, [self.Reference(item) for item in value]]
        if kind is range or kind is slice:
            return [kind.__name__, self.Reference(value.start), self.Reference(value.stop), self.Reference(value.step)]
        builtin = _BUILTIN_IDS.get(id(value))
        if builtin is not None:
            return ['builtin', builtin]
        if kind is types.FunctionType and _Path(value.__code__.co_filename) == self.filename:
            return ['function', self.Code(value.__code__), self.Reference(value.__defaults__),
                    self.Reference(value.__kwdefaults__), self.Reference(value.__closure__),
                    self.Reference(value.__annotations__), self.Reference(value.__dict__)]
        if kind is _CELL:
            try:
                contents = value.cell_contents
            except ValueError:
                return ['empty-cell']
            return ['cell', self.Reference(contents)]
        if kind is types.BuiltinMethodType:
            owner = value.__self__
            if (id(type(owner)) in _BUILTIN_METHOD_OWNERS or id(owner) in _BUILTIN_IDS) and value.__name__ in _ATTRIBUTES:
                return ['method', value.__name__, self.Reference(owner)]
        raise ValueError('Replay checkpoints cannot compare an active iterator or a non-portable program value.')


def Fingerprint(frame, filename):
    """Return a digest of the main script's active frames and reference graph."""
    if type(frame) is not types.FrameType:
        raise ValueError('Replay checkpoints require a live Python stack frame.')
    filename = _Path(filename)
    stack = []
    while frame is not None:
        if _Path(frame.f_code.co_filename) == filename:
            stack.append(frame)
        frame = frame.f_back
    if not stack:
        raise ValueError('Replay checkpoints require a frame in the main script.')

    graph = _Graph(filename)
    namespaces = {}
    globals_ = []
    frames = []
    try:
        for frame in reversed(stack):
            global_id = id(frame.f_globals)
            if global_id not in namespaces:
                namespaces[global_id] = len(globals_)
                globals_.append(graph.Namespace(frame.f_globals, global_scope=True))
            local = frame.f_locals
            frames.append([graph.Code(frame.f_code), frame.f_lineno, frame.f_lasti, namespaces[global_id],
                           None if local is frame.f_globals else graph.Namespace(local)])
        data = json.dumps([frames, globals_, graph.nodes], separators=(',', ':'), ensure_ascii=True)
    except RecursionError as error:
        raise ValueError('Replay checkpoint state exceeds Python\'s recursion limit.') from error
    return hashlib.sha256(data.encode('ascii')).hexdigest()
