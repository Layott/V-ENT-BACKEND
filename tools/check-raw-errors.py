#!/usr/bin/env python3
"""Text from an exception, or from a payment gateway, sent to a person.

CEO, 29 September 2026: "I DONT LIKE THAT USERS ARE SEEING THIS KIND OF
ERROR: The payment could not be started: Format is Authorization Bearer
[secret key] ... check the entire site for errors like this to make sure,
users see proper messages for errors instead of code."

That sentence was Paystack's own, passed through `'...: %s' % exc`. The same
shape was in thirty places: `except Exception as e: return Response({'message':
str(e)})` hands a person Python's words, and `PayError(GATEWAY_ERROR,
str(exc))` smuggles a gateway's words inside one of our own errors.

What is allowed: an exception class of OURS that carries a `code` (WalletError,
PayoutError, ...), because its message is written by us for a person and its
code is what the screen translates. Everything else, including the gateway's
Refused/Unreachable and a bare Exception, is raw.

Flags, inside `except <raw> as NAME:`:
  * a `return` whose value uses NAME (a response built from it);
  * a `raise <OurError>(...)` whose arguments use NAME (text passed through);
  * `x['message'|'error'|'detail'|'reason'] = ...NAME...`.
Logging calls are ignored: the log is exactly where that text belongs.

    python tools/check-raw-errors.py
    python tools/check-raw-errors.py --self-test
"""
import ast
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATEWAY = {'Refused', 'Unreachable'}
TEXT_KEYS = {'message', 'error', 'detail', 'reason'}
LOG_NAMES = {'logger', 'log', 'logging', 'print', 'warnings'}
# vent_auth/errors.py: they take the exception only to log it, and answer a
# person with a fixed sentence and a code.
SAFE_HELPERS = {'server_error', 'bad_input', 'gateway_refused', 'gateway_down'}


def domain_errors(trees):
    """Our own exception classes that carry a code."""
    found = set()
    for tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {getattr(b, 'id', getattr(b, 'attr', '')) for b in node.bases}
            # Any exception class of OURS: its messages are written here, for
            # a person (ListingError, StageError, WalletError ...).
            if any(b.endswith(('Error', 'Exception')) for b in bases):
                found.add(node.name)
    # Subclasses of a coded error inherit its code.
    changed = True
    while changed:
        changed = False
        for tree in trees:
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name not in found:
                    bases = {getattr(b, 'id', getattr(b, 'attr', '')) for b in node.bases}
                    if bases & found:
                        found.add(node.name)
                        changed = True
    # Django's validators speak to people by design (password rules).
    return (found | {'DjangoValidationError'}) - GATEWAY


def _type_names(handler):
    t = handler.type
    items = t.elts if isinstance(t, ast.Tuple) else [t] if t is not None else []
    return {getattr(i, 'attr', getattr(i, 'id', '')) for i in items}


def _uses(node, name):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == name:
            return True
    return False


RESPONDERS = re.compile(r'^(Response|JsonResponse|HttpResponse\w*|_?err(or)?|_?bad\w*|_?fail\w*|_?refuse\w*|_?problem)$')


def _builds_response(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            callee = getattr(sub.func, 'id', getattr(sub.func, 'attr', ''))
            if RESPONDERS.match(callee or ''):
                return True
    return False


def _is_log_call(call):
    f = call.func
    if getattr(f, 'id', getattr(f, 'attr', '')) in SAFE_HELPERS:
        return True
    root = f
    while isinstance(root, ast.Attribute):
        root = root.value
    return isinstance(root, ast.Name) and root.id in LOG_NAMES


def _strip_logging(node):
    """The node with logging calls removed, so text sent to a log is not flagged."""
    class Strip(ast.NodeTransformer):
        def visit_Call(self, call):
            if _is_log_call(call):
                return ast.Constant(value=None)
            return self.generic_visit(call)
    import copy
    return Strip().visit(copy.deepcopy(node))


def findings_in(tree, domain, rel):
    out = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or not handler.name:
            continue
        types = _type_names(handler)
        if types and types <= domain:
            continue
        name = handler.name
        for stmt in handler.body:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Return) and node.value is not None:
                    # Only a response reaches a person; a helper returning a
                    # reason to its caller (a cron, a log) does not.
                    if _builds_response(node.value) and _uses(_strip_logging(node.value), name):
                        out.append((rel, node.lineno, 'returns text from %s' % (', '.join(sorted(types)) or 'an exception')))
                elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                    callee = getattr(node.exc.func, 'id', getattr(node.exc.func, 'attr', ''))
                    if callee in domain and any(_uses(a, name) for a in node.exc.args):
                        out.append((rel, node.lineno, 'passes %s text into %s' % (', '.join(sorted(types)), callee)))
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                                and target.slice.value in TEXT_KEYS and _uses(node.value, name)):
                            out.append((rel, node.lineno, "puts %s text in ['%s']" % (', '.join(sorted(types)), target.slice.value)))
    return out


def sources():
    for app in sorted(os.listdir(REPO)):
        path = os.path.join(REPO, app)
        if not (app.startswith('vent') and os.path.isdir(path)):
            continue
        for dirpath, dirnames, files in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in ('migrations', '__pycache__')]
            for f in files:
                if f.endswith('.py') and not f.startswith('tests'):
                    full = os.path.join(dirpath, f)
                    yield os.path.relpath(full, REPO).replace(os.sep, '/'), full


def self_test():
    domain_src = (
        "class WalletError(Exception):\n"
        "    def __init__(self, code, message):\n"
        "        self.code = code\n"
        "class Refused(Exception):\n"
        "    pass\n")
    cases = [
        ('a raw exception returned', "def v():\n    try:\n        x()\n    except Exception as e:\n        return Response({'message': str(e)})\n", 1),
        ('a gateway reason formatted in', "def v():\n    try:\n        x()\n    except paystack.Refused as exc:\n        return _err('Could not start: %s' % exc, 'PAYMENT_REFUSED')\n", 1),
        ('gateway text smuggled into our error', "def v():\n    try:\n        x()\n    except Refused as exc:\n        raise WalletError('GATEWAY', str(exc))\n", 1),
        ('our coded error is fine', "def v():\n    try:\n        x()\n    except wallets.WalletError as exc:\n        return Response({'code': exc.code, 'message': str(exc)})\n", 0),
        ('logged, not returned', "def v():\n    try:\n        x()\n    except Exception as e:\n        logger.exception('boom %s', e)\n        return Response({'message': 'Something went wrong.'})\n", 0),
        ('a helper returning a reason to its caller', "def f():\n    try:\n        x()\n    except URLError as exc:\n        return None, 'Could not reach: %s' % exc\n", 0),
        ('handed to the errors helper, which logs it', "def v():\n    try:\n        x()\n    except ValueError as e:\n        return bad_input(e)\n", 0),
        ('a reason put in a field', "def v():\n    try:\n        x()\n    except Refused as exc:\n        out['error'] = str(exc)\n", 1),
    ]
    domain = domain_errors([ast.parse(domain_src)])
    failed = 0
    for label, src, want in cases:
        got = len(findings_in(ast.parse(src), domain, 'x.py'))
        ok = got == want
        failed += not ok
        print('%s %s: %d (want %d)' % ('ok  ' if ok else 'FAIL', label, got, want))
    print('%d self-test case(s) pass' % (len(cases) - failed) if not failed else '%d FAILED' % failed)
    return failed == 0


def main():
    if '--self-test' in sys.argv:
        sys.exit(0 if self_test() else 1)
    parsed = []
    for rel, full in sources():
        try:
            parsed.append((rel, ast.parse(open(full, encoding='utf-8').read())))
        except SyntaxError:
            continue
    domain = domain_errors([t for _, t in parsed])
    found = []
    for rel, tree in parsed:
        found += findings_in(tree, domain, rel)
    for rel, line, what in sorted(found):
        print('  %s:%d  %s' % (rel, line, what))
    print('%d file(s) read, %d place(s) that send exception or gateway text to a person'
          % (len(parsed), len(found)))
    sys.exit(1 if found else 0)


if __name__ == '__main__':
    main()
