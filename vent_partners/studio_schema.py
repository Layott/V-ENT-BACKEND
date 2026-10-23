"""The result a studio sends, checked twice: our shape, then theirs.

Design section 14, item 1: a studio registers "a result schema (JSON Schema)",
and item 4: results are "validated against the schema". Challengermode does the
same (research file, section 7.4): the game posts raw results and the platform
checks them against a schema the studio set.

**Our shape comes first** (`envelope_problems`). Whatever the studio's schema
says, a head to head result has two lineups, one per side, because that is
what a V-ENT match is:

    {
      "lineups":     [{"side": 1, ...}, {"side": 2, ...}],
      "competitors": [{"account_id": "...", "side": 1, ...}],   optional
      "general":     {...}                                      optional
    }

**Their schema comes second** (`validate`). It is a declared subset of JSON
Schema, listed in `KEYWORDS`, and a schema using any other keyword is refused
when it is saved (`check_schema`). The alternative, ignoring keywords we do not
understand, would let a studio believe `pattern` or `$ref` protects them when
nothing reads it: a validator that silently skips half a schema is worse than
one that says what it does.

Written here rather than taken from the `jsonschema` package: the subset is
small, this keeps a dependency off the server, and every rule it applies is in
this one file where it can be read.
"""
import json

KEYWORDS = (
    'type', 'properties', 'required', 'items', 'enum', 'minimum', 'maximum',
    'minLength', 'maxLength', 'minItems', 'maxItems', 'additionalProperties',
    'description', 'title', '$schema',
)

TYPES = ('object', 'array', 'string', 'number', 'integer', 'boolean', 'null')

MAX_DEPTH = 12
MAX_SCHEMA_CHARS = 8000
MAX_RESULT_CHARS = 20000
MAX_COMPETITORS = 200
MAX_ERRORS = 50


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _size(value):
    try:
        return len(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Checking a schema when a studio saves it
# ---------------------------------------------------------------------------

def check_schema(schema):
    """Problems with a schema as codes; an empty list means it may be saved."""
    if not isinstance(schema, dict):
        return ['SCHEMA_NOT_OBJECT']
    size = _size(schema)
    if size is None:
        return ['SCHEMA_NOT_OBJECT']
    if size > MAX_SCHEMA_CHARS:
        return ['SCHEMA_TOO_LARGE']
    found = []
    _check_node(schema, 1, found)
    return list(dict.fromkeys(found))


def _check_node(node, depth, found):
    if not isinstance(node, dict):
        found.append('SCHEMA_BAD_TYPE')
        return
    if depth > MAX_DEPTH:
        found.append('SCHEMA_TOO_DEEP')
        return
    for key, value in node.items():
        if key not in KEYWORDS:
            found.append('SCHEMA_UNKNOWN_KEYWORD:%s' % key)
            continue
        if key == 'type':
            names = value if isinstance(value, list) else [value]
            if not names or any(n not in TYPES for n in names):
                found.append('SCHEMA_BAD_TYPE')
        elif key == 'properties':
            if not isinstance(value, dict):
                found.append('SCHEMA_BAD_TYPE')
                continue
            for child in value.values():
                _check_node(child, depth + 1, found)
        elif key == 'required':
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                found.append('SCHEMA_BAD_TYPE')
        elif key == 'items':
            _check_node(value, depth + 1, found)
        elif key == 'enum':
            if not isinstance(value, list) or not value or len(value) > 100:
                found.append('SCHEMA_BAD_TYPE')
        elif key in ('minimum', 'maximum'):
            if not _is_number(value):
                found.append('SCHEMA_BAD_TYPE')
        elif key in ('minLength', 'maxLength', 'minItems', 'maxItems'):
            if not _is_count(value):
                found.append('SCHEMA_BAD_TYPE')
        elif key == 'additionalProperties':
            if isinstance(value, dict):
                _check_node(value, depth + 1, found)
            elif not isinstance(value, bool):
                found.append('SCHEMA_BAD_TYPE')
        elif not isinstance(value, str):     # description, title, $schema
            found.append('SCHEMA_BAD_TYPE')


# ---------------------------------------------------------------------------
# Validating a result against it
# ---------------------------------------------------------------------------

def _type_matches(value, name):
    if name == 'object':
        return isinstance(value, dict)
    if name == 'array':
        return isinstance(value, list)
    if name == 'string':
        return isinstance(value, str)
    if name == 'boolean':
        return isinstance(value, bool)
    if name == 'null':
        return value is None
    if name == 'number':
        return _is_number(value)
    if name == 'integer':
        return (isinstance(value, int) and not isinstance(value, bool)) or \
            (isinstance(value, float) and value.is_integer())
    return False


def _same(a, b):
    """Equality that does not let True stand in for 1, as Python's does."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def validate(instance, schema):
    """Every way `instance` breaks `schema`, as {path, code}, at most fifty."""
    errors = []
    _validate(instance, schema if isinstance(schema, dict) else {}, '$', errors)
    return errors[:MAX_ERRORS]


def _add(errors, path, code):
    if len(errors) < MAX_ERRORS:
        errors.append({'path': path, 'code': code})


def _validate(value, schema, path, errors):
    if len(errors) >= MAX_ERRORS:
        return
    if 'type' in schema:
        names = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        if not any(_type_matches(value, n) for n in names):
            _add(errors, path, 'TYPE')
            return
    if 'enum' in schema and not any(_same(value, option) for option in schema['enum']):
        _add(errors, path, 'ENUM')
    if _is_number(value):
        if 'minimum' in schema and value < schema['minimum']:
            _add(errors, path, 'MINIMUM')
        if 'maximum' in schema and value > schema['maximum']:
            _add(errors, path, 'MAXIMUM')
    if isinstance(value, str):
        if 'minLength' in schema and len(value) < schema['minLength']:
            _add(errors, path, 'MIN_LENGTH')
        if 'maxLength' in schema and len(value) > schema['maxLength']:
            _add(errors, path, 'MAX_LENGTH')
    if isinstance(value, list):
        if 'minItems' in schema and len(value) < schema['minItems']:
            _add(errors, path, 'MIN_ITEMS')
        if 'maxItems' in schema and len(value) > schema['maxItems']:
            _add(errors, path, 'MAX_ITEMS')
        if isinstance(schema.get('items'), dict):
            for index, item in enumerate(value):
                _validate(item, schema['items'], '%s[%d]' % (path, index), errors)
    if isinstance(value, dict):
        for name in schema.get('required') or []:
            if name not in value:
                _add(errors, path, 'REQUIRED')
        known = schema.get('properties') or {}
        for name, child in value.items():
            if name in known:
                _validate(child, known[name], '%s.%s' % (path, name), errors)
                continue
            extra = schema.get('additionalProperties', True)
            if extra is False:
                _add(errors, '%s.%s' % (path, name), 'ADDITIONAL')
            elif isinstance(extra, dict):
                _validate(child, extra, '%s.%s' % (path, name), errors)


# ---------------------------------------------------------------------------
# Our own shape, before the studio's
# ---------------------------------------------------------------------------

def envelope_problems(result):
    """What is wrong with the result as a V-ENT head to head result."""
    if not isinstance(result, dict):
        return [{'path': '$', 'code': 'TYPE'}]
    size = _size(result)
    if size is None or size > MAX_RESULT_CHARS:
        return [{'path': '$', 'code': 'TOO_LARGE'}]

    problems = []
    lineups = result.get('lineups')
    paired = (isinstance(lineups, list) and len(lineups) == 2
              and all(isinstance(l, dict) for l in lineups))
    if paired:
        first, second = lineups[0].get('side'), lineups[1].get('side')
        paired = ((_same(first, 1) and _same(second, 2))
                  or (_same(first, 2) and _same(second, 1)))
    if not paired:
        problems.append({'path': '$.lineups', 'code': 'TWO_LINEUPS'})

    if 'competitors' in result:
        people = result['competitors']
        if not isinstance(people, list):
            problems.append({'path': '$.competitors', 'code': 'TYPE'})
        elif len(people) > MAX_COMPETITORS:
            problems.append({'path': '$.competitors', 'code': 'MAX_ITEMS'})
        else:
            for index, person in enumerate(people):
                if not isinstance(person, dict):
                    problems.append({'path': '$.competitors[%d]' % index, 'code': 'TYPE'})
                elif not any(_same(person.get('side'), s) for s in (1, 2)):
                    problems.append({'path': '$.competitors[%d].side' % index, 'code': 'ENUM'})

    if 'general' in result and not isinstance(result['general'], dict):
        problems.append({'path': '$.general', 'code': 'TYPE'})
    return problems[:MAX_ERRORS]
