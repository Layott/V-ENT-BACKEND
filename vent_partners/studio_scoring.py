"""Turning a studio's raw result into a score, with an expression the studio writes.

Design section 14, item 4, and section 18: results are "scored by the
expression with a safe evaluator (never eval, R74)", "a small arithmetic
parser". Challengermode's model (research file, section 7.4): the studio sends
raw numbers and says how they become a score, for example

    team.score
    competitors.sum(it.kills)
    team.roundsWon + competitors.sum(it.kills)
    global.double ? team.roundsWon * 2 : team.roundsWon
    settings.mode == 'KillRace' ? competitors.sum(it.kills) : (team.won ? 1 : 0)

The expression is written by somebody outside V-ENT and runs on our server, so
this module is a language of its own rather than a door into Python's:

  * it is tokenised and parsed here into a small tree, and only that tree is
    walked; nothing is handed to Python to run;
  * a name is one of five fixed names (team, opponent, competitors, global,
    settings, and `it` inside a list method), checked when the expression is
    saved, so a typo is a refusal at registration rather than a surprise at
    the final;
  * `a.b` reads a key from a JSON object and nothing else. A Python attribute
    is never touched, so `team.__class__` is simply a missing field;
  * functions and list methods come from two fixed tables;
  * size, depth, the number of parts, the number of steps, the length of any
    list walked and every number are all bounded, so no expression can run for
    long or produce something huge.

Every refusal is an `ExpressionError` with a code and the position it is about.
"""
import math

MAX_LENGTH = 500
MAX_NODES = 200
MAX_DEPTH = 32
MAX_LIST = 500
MAX_STEPS = 100000
MAX_MAGNITUDE = 1e9

NAMES = ('team', 'opponent', 'competitors', 'global', 'settings')
FUNCTIONS = {'min', 'max', 'abs', 'round', 'floor', 'ceil'}
LIST_METHODS = {'sum', 'avg', 'max', 'min', 'count', 'any', 'all'}
KEYWORDS = {'and', 'or', 'not', 'true', 'false', 'null'}

#: The score a V-ENT match can hold (`vent_tournament.results`).
SCORE_MIN, SCORE_MAX = 0, 999


class ExpressionError(ValueError):
    def __init__(self, code, position=None):
        super().__init__(code)
        self.code = code
        self.position = position


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

_TWO = ('==', '!=', '<=', '>=', '&&', '||')
_ONE = '+-*/%<>!?:(),.'


def _tokens(text):
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c.isdigit() or (c == '.' and i + 1 < n and text[i + 1].isdigit()):
            start = i
            while i < n and text[i].isdigit():
                i += 1
            if i < n and text[i] == '.' and i + 1 < n and text[i + 1].isdigit():
                i += 1
                while i < n and text[i].isdigit():
                    i += 1
            if i < n and text[i] in 'eE':
                j = i + 1
                if j < n and text[j] in '+-':
                    j += 1
                if j < n and text[j].isdigit():
                    i = j
                    while i < n and text[i].isdigit():
                        i += 1
            try:
                value = float(text[start:i])
            except ValueError:
                raise ExpressionError('EXPRESSION_SYNTAX', position=start) from None
            out.append(('num', value, start))
            continue
        if c.isalpha() or c == '_':
            start = i
            while i < n and (text[i].isalnum() or text[i] == '_'):
                i += 1
            word = text[start:i]
            out.append(('word' if word in KEYWORDS else 'name', word, start))
            continue
        if c in '\'"':
            start, quote, i = i, c, i + 1
            chars = []
            while i < n and text[i] != quote:
                if text[i] == '\\' and i + 1 < n:
                    i += 1
                chars.append(text[i])
                i += 1
            if i >= n:
                raise ExpressionError('EXPRESSION_SYNTAX', position=start)
            i += 1
            out.append(('str', ''.join(chars), start))
            continue
        pair = text[i:i + 2]
        if pair in _TWO:
            out.append(('op', pair, i))
            i += 2
            continue
        if c in _ONE:
            out.append(('op', c, i))
            i += 1
            continue
        raise ExpressionError('EXPRESSION_SYNTAX', position=i)
    out.append(('end', None, n))
    return out


# ---------------------------------------------------------------------------
# Parsing into a tree of tuples
# ---------------------------------------------------------------------------

class _Parser:
    def __init__(self, text):
        self.tokens = _tokens(text)
        self.at = 0
        self.depth = 0
        self.nodes = 0
        self.scopes = 0       # how many list methods we are inside: `it` is known there
        # An unknown name or function is held until the whole expression has
        # parsed, so `lambda: 1` reads as the syntax error it is rather than
        # as an unknown name.
        self.unknown = None

    def refuse_later(self, code, position):
        if self.unknown is None:
            self.unknown = ExpressionError(code, position)

    def peek(self):
        return self.tokens[self.at]

    def take(self):
        token = self.tokens[self.at]
        self.at += 1
        return token

    def is_op(self, *ops):
        kind, value, _ = self.peek()
        return kind == 'op' and value in ops

    def is_word(self, *words):
        kind, value, _ = self.peek()
        return kind == 'word' and value in words

    def expect_op(self, op):
        kind, value, position = self.take()
        if kind != 'op' or value != op:
            raise ExpressionError('EXPRESSION_SYNTAX', position=position)

    def node(self, *parts):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ExpressionError('EXPRESSION_TOO_COMPLEX', position=self.peek()[2])
        return parts

    def enter(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExpressionError('EXPRESSION_TOO_COMPLEX', position=self.peek()[2])

    def leave(self):
        self.depth -= 1

    def parse(self):
        tree = self.ternary()
        kind, _, position = self.peek()
        if kind != 'end':
            raise ExpressionError('EXPRESSION_SYNTAX', position=position)
        if self.unknown is not None:
            raise self.unknown
        return tree

    def ternary(self):
        self.enter()
        try:
            condition = self.either()
            if self.is_op('?'):
                self.take()
                yes = self.ternary()
                self.expect_op(':')
                no = self.ternary()
                return self.node('if', condition, yes, no)
            return condition
        finally:
            self.leave()

    def either(self):
        left = self.both()
        while self.is_op('||') or self.is_word('or'):
            self.take()
            left = self.node('or', left, self.both())
        return left

    def both(self):
        left = self.negation()
        while self.is_op('&&') or self.is_word('and'):
            self.take()
            left = self.node('and', left, self.negation())
        return left

    def negation(self):
        if self.is_op('!') or self.is_word('not'):
            self.take()
            self.enter()
            try:
                return self.node('not', self.negation())
            finally:
                self.leave()
        return self.comparison()

    def comparison(self):
        left = self.sum()
        if self.is_op('==', '!=', '<', '<=', '>', '>='):
            op = self.take()[1]
            left = self.node('cmp', op, left, self.sum())
            if self.is_op('==', '!=', '<', '<=', '>', '>='):
                raise ExpressionError('EXPRESSION_SYNTAX', position=self.peek()[2])
        return left

    def sum(self):
        left = self.product()
        while self.is_op('+', '-'):
            op = self.take()[1]
            left = self.node('math', op, left, self.product())
        return left

    def product(self):
        left = self.unary()
        while self.is_op('*', '/', '%'):
            op = self.take()[1]
            left = self.node('math', op, left, self.unary())
        return left

    def unary(self):
        if self.is_op('-', '+'):
            op = self.take()[1]
            self.enter()
            try:
                inner = self.unary()
            finally:
                self.leave()
            return self.node('neg', inner) if op == '-' else inner
        return self.postfix()

    def postfix(self):
        tree = self.primary()
        while self.is_op('.'):
            self.take()
            kind, name, position = self.take()
            if kind != 'name':
                raise ExpressionError('EXPRESSION_SYNTAX', position=position)
            if self.is_op('('):
                if name not in LIST_METHODS:
                    self.refuse_later('EXPRESSION_UNKNOWN_FUNCTION', position)
                self.take()
                argument = None
                if not self.is_op(')'):
                    self.scopes += 1
                    try:
                        argument = self.ternary()
                    finally:
                        self.scopes -= 1
                self.expect_op(')')
                if argument is None and name in LIST_METHODS and name != 'count':
                    raise ExpressionError('EXPRESSION_SYNTAX', position=position)
                tree = self.node('method', name, tree, argument)
            else:
                tree = self.node('field', tree, name)
        return tree

    def primary(self):
        kind, value, position = self.take()
        if kind == 'num':
            if not math.isfinite(value) or abs(value) > MAX_MAGNITUDE:
                raise ExpressionError('EXPRESSION_OUT_OF_RANGE', position=position)
            return self.node('const', value)
        if kind == 'str':
            return self.node('const', value)
        if kind == 'word' and value in ('true', 'false', 'null'):
            return self.node('const', {'true': True, 'false': False, 'null': None}[value])
        if kind == 'op' and value == '(':
            tree = self.ternary()
            self.expect_op(')')
            return tree
        if kind == 'name':
            if self.is_op('('):
                if value not in FUNCTIONS:
                    self.refuse_later('EXPRESSION_UNKNOWN_FUNCTION', position)
                self.take()
                arguments = []
                if not self.is_op(')'):
                    arguments.append(self.ternary())
                    while self.is_op(','):
                        self.take()
                        arguments.append(self.ternary())
                self.expect_op(')')
                wanted = (1, 50) if value in ('min', 'max') else (1, 1)
                if value in FUNCTIONS and not wanted[0] <= len(arguments) <= wanted[1]:
                    raise ExpressionError('EXPRESSION_SYNTAX', position=position)
                return self.node('call', value, tuple(arguments))
            if not (value in NAMES or (value == 'it' and self.scopes > 0)):
                self.refuse_later('EXPRESSION_UNKNOWN_NAME', position)
            return self.node('name', value)
        raise ExpressionError('EXPRESSION_SYNTAX', position=position)


# ---------------------------------------------------------------------------
# Walking the tree
# ---------------------------------------------------------------------------

def _number(value):
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ExpressionError('EXPRESSION_OUT_OF_RANGE')
        return float(value)
    raise ExpressionError('EXPRESSION_NOT_A_NUMBER')


def _bounded(value):
    if not math.isfinite(value) or abs(value) > MAX_MAGNITUDE:
        raise ExpressionError('EXPRESSION_OUT_OF_RANGE')
    return value


def _truthy(value):
    if value is None:
        return False
    if isinstance(value, (bool, int, float)):
        return bool(value)
    if isinstance(value, (str, list, dict)):
        return len(value) > 0
    return False


def _equal(a, b):
    if isinstance(a, (bool, int, float)) and isinstance(b, (bool, int, float)) \
            and not isinstance(a, str) and not isinstance(b, str):
        return _number(a) == _number(b)
    return a == b


def _half_up(value):
    return math.floor(value + 0.5)


class Expression:
    def __init__(self, tree, text):
        self.tree = tree
        self.text = text

    def evaluate(self, env):
        self.steps = 0
        return self._walk(self.tree, env)

    def _walk(self, tree, env):
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise ExpressionError('EXPRESSION_TOO_COMPLEX')
        kind = tree[0]
        if kind == 'const':
            return tree[1]
        if kind == 'name':
            return env.get(tree[1])
        if kind == 'field':
            holder = self._walk(tree[1], env)
            return holder.get(tree[2]) if isinstance(holder, dict) else None
        if kind == 'neg':
            return _bounded(-_number(self._walk(tree[1], env)))
        if kind == 'not':
            return not _truthy(self._walk(tree[1], env))
        if kind == 'and':
            return _truthy(self._walk(tree[1], env)) and _truthy(self._walk(tree[2], env))
        if kind == 'or':
            return _truthy(self._walk(tree[1], env)) or _truthy(self._walk(tree[2], env))
        if kind == 'if':
            branch = tree[2] if _truthy(self._walk(tree[1], env)) else tree[3]
            return self._walk(branch, env)
        if kind == 'cmp':
            return self._compare(tree[1], self._walk(tree[2], env), self._walk(tree[3], env))
        if kind == 'math':
            return self._arithmetic(tree[1], self._walk(tree[2], env), self._walk(tree[3], env))
        if kind == 'call':
            return self._call(tree[1], [self._walk(a, env) for a in tree[2]])
        if kind == 'method':
            return self._method(tree[1], self._walk(tree[2], env), tree[3], env)
        raise ExpressionError('EXPRESSION_SYNTAX', position=None)

    @staticmethod
    def _compare(op, a, b):
        if op == '==':
            return _equal(a, b)
        if op == '!=':
            return not _equal(a, b)
        x, y = _number(a), _number(b)
        return {'<': x < y, '<=': x <= y, '>': x > y, '>=': x >= y}[op]

    @staticmethod
    def _arithmetic(op, a, b):
        x, y = _number(a), _number(b)
        if op == '+':
            return _bounded(x + y)
        if op == '-':
            return _bounded(x - y)
        if op == '*':
            return _bounded(x * y)
        if y == 0:
            raise ExpressionError('EXPRESSION_DIVIDE_BY_ZERO')
        if op == '/':
            return _bounded(x / y)
        return _bounded(math.fmod(x, y))

    @staticmethod
    def _call(name, values):
        numbers = [_number(v) for v in values]
        if name == 'min':
            return min(numbers)
        if name == 'max':
            return max(numbers)
        if name == 'abs':
            return abs(numbers[0])
        if name == 'round':
            return float(_half_up(numbers[0]))
        if name == 'floor':
            return float(math.floor(numbers[0]))
        return float(math.ceil(numbers[0]))

    def _method(self, name, items, argument, env):
        if not isinstance(items, list):
            raise ExpressionError('EXPRESSION_NOT_A_LIST')
        if len(items) > MAX_LIST:
            raise ExpressionError('EXPRESSION_TOO_COMPLEX')
        if name == 'count' and argument is None:
            return float(len(items))
        values = [self._walk(argument, dict(env, it=item)) for item in items]
        if name == 'count':
            return float(sum(1 for v in values if _truthy(v)))
        if name == 'any':
            return any(_truthy(v) for v in values)
        if name == 'all':
            return all(_truthy(v) for v in values)
        numbers = [_number(v) for v in values]
        if name == 'sum':
            return _bounded(sum(numbers))
        if not numbers:
            return None
        if name == 'avg':
            return _bounded(sum(numbers) / len(numbers))
        return max(numbers) if name == 'max' else min(numbers)


def compile_expression(text):
    """Parse a scoring expression once, refusing anything outside the language."""
    raw = str(text or '')
    if len(raw) > MAX_LENGTH:
        raise ExpressionError('EXPRESSION_TOO_LONG', position=MAX_LENGTH)
    if not raw.strip():
        raise ExpressionError('EXPRESSION_EMPTY', position=0)
    return Expression(_Parser(raw).parse(), raw)


def side_env(result, side, settings):
    """The names an expression sees when scoring one side of one game."""
    lineups = [l for l in (result or {}).get('lineups') or [] if isinstance(l, dict)]
    mine = next((l for l in lineups if l.get('side') == side), {})
    theirs = next((l for l in lineups if l.get('side') != side), {})
    people = [p for p in (result or {}).get('competitors') or []
              if isinstance(p, dict) and p.get('side') == side]
    general = (result or {}).get('general')
    return {
        'team': mine,
        'opponent': theirs,
        'competitors': people,
        'global': general if isinstance(general, dict) else {},
        'settings': settings if isinstance(settings, dict) else {},
    }


def to_score(value):
    """An evaluated answer as a whole score a match can hold."""
    number = _number(value) if value is not None else None
    if number is None:
        raise ExpressionError('EXPRESSION_NOT_A_NUMBER')
    whole = _half_up(number)
    if not SCORE_MIN <= whole <= SCORE_MAX:
        raise ExpressionError('SCORE_OUT_OF_RANGE')
    return int(whole)


def score_sides(expression_text, result, settings):
    """(side 1 score, side 2 score) for one game."""
    expression = compile_expression(expression_text)
    return tuple(to_score(expression.evaluate(side_env(result, side, settings)))
                 for side in (1, 2))
