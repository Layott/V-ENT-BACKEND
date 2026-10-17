"""Point tables: how many circuit points a final place is worth (P5).

Design section 13: "a point table per tier in the form `1: 300, 2-9: 100,
default 10`". That line is start.gg's league format (research S7), and it is
what an organiser types. It is stored as

    {"rows": [[1, 1, "300"], [2, 9, "100"]], "default": "10"}

with the points as strings, so a half point is kept exactly rather than as a
float. `parse` reads the typed line, `text` writes it back the same way, and
`clean` checks a stored one again, because a table that did not come through
`parse` is not trusted.

Shared places split equally (`shared`): two entrants level on 3rd took places 3
and 4 between them, so each gets the mean of the two. It is the rule the prize
split follows (design section 15, decision 5), so a shared place is worth the
same share of points as of money.
"""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

#: Places run from 1 to this. A circuit event with more entrants than this
#: still counts; everybody below gets the default.
MAX_PLACE = 1024
#: The most points a place may be worth.
MAX_POINTS = Decimal('1000000')
#: The most lines a table may have.
MAX_ROWS = 64

_RANGE = re.compile(r'^(\d+)\s*(?:-\s*(\d+))?\s*:\s*(\S+)$')
_DEFAULT = re.compile(r'^default\s*:?\s*(\S+)$', re.IGNORECASE)
_NUMBER = re.compile(r'^-?\d+(?:\.\d{1,2})?$')
TWO_PLACES = Decimal('0.01')


class TableError(ValueError):
    """A table that cannot be used. `code` is translated by the screen and
    `part` is the piece of the line it is about, shown beside it."""

    def __init__(self, code, part=''):
        super().__init__('%s: %s' % (code, part))
        self.code = code
        self.part = part


def _points(raw, part):
    raw = str(raw).strip()
    if not _NUMBER.match(raw):
        raise TableError('POINT_TABLE_INVALID', part)
    value = Decimal(raw)
    if value < 0 or value > MAX_POINTS:
        raise TableError('POINT_TABLE_RANGE', part)
    return value


def plain(value):
    """A number as the shortest string that says it: 300, 12.5, -0.25. Every
    number the API writes goes through here: str(Decimal) can say 1E+2."""
    return format(Decimal(str(value)).normalize(), 'f')


_plain = plain


def parse(text):
    """The table a typed line describes, or {} for an empty line."""
    parts = [p.strip() for p in re.split(r'[,;\n]+', str(text or '')) if p.strip()]
    if not parts:
        return {}
    if len(parts) > MAX_ROWS:
        raise TableError('POINT_TABLE_TOO_LONG', '')
    rows, default, taken = [], None, []
    for part in parts:
        found = _DEFAULT.match(part)
        if found:
            if default is not None:
                raise TableError('POINT_TABLE_OVERLAP', part)
            default = _points(found.group(1), part)
            continue
        found = _RANGE.match(part)
        if not found:
            raise TableError('POINT_TABLE_INVALID', part)
        first = int(found.group(1))
        last = int(found.group(2)) if found.group(2) else first
        if first < 1 or last < 1 or first > MAX_PLACE or last > MAX_PLACE:
            raise TableError('POINT_TABLE_RANGE', part)
        if last < first:
            raise TableError('POINT_TABLE_ORDER', part)
        if any(first <= b and a <= last for a, b in taken):
            raise TableError('POINT_TABLE_OVERLAP', part)
        taken.append((first, last))
        rows.append([first, last, _plain(_points(found.group(3), part))])
    rows.sort(key=lambda r: r[0])
    table = {'rows': rows}
    if default is not None:
        table['default'] = _plain(default)
    return table


def clean(raw):
    """A stored table checked again: the same table back, or TableError."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise TableError('POINT_TABLE_INVALID', '')
    rows = raw.get('rows', [])
    if not isinstance(rows, list):
        raise TableError('POINT_TABLE_INVALID', '')
    lines = []
    for row in rows:
        if not (isinstance(row, (list, tuple)) and len(row) == 3):
            raise TableError('POINT_TABLE_INVALID', '')
        first, last, points = row
        try:
            first, last = int(first), int(last)
        except (TypeError, ValueError):
            raise TableError('POINT_TABLE_INVALID', '')
        lines.append(('%d-%d: %s' % (first, last, points)) if last != first
                     else '%d: %s' % (first, points))
    if 'default' in raw:
        lines.append('default %s' % raw['default'])
    return parse(', '.join(lines))


def text(table):
    """The line an organiser typed for `table`: "1: 300, 2-9: 100, default 10"."""
    if not table:
        return ''
    out = []
    for first, last, points in table.get('rows', []):
        out.append(('%d-%d: %s' % (first, last, points)) if last != first else '%d: %s' % (first, points))
    if table.get('default') is not None:
        out.append('default %s' % table['default'])
    return ', '.join(out)


def points(table, place):
    """What `place` is worth in `table`: its line, else the default, else 0."""
    for first, last, value in (table or {}).get('rows', []):
        if first <= place <= last:
            return Decimal(str(value))
    default = (table or {}).get('default')
    return Decimal(str(default)) if default is not None else Decimal('0')


def shared(table, place, count):
    """What each of `count` entrants sharing `place` gets: the mean of the
    places they took between them, to two decimal places."""
    count = max(int(count or 1), 1)
    total = sum((points(table, place + i) for i in range(count)), Decimal('0'))
    try:
        each = (total / count).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    except InvalidOperation:          # pragma: no cover - the numbers are bounded
        each = Decimal('0')
    return Decimal(plain(each))
