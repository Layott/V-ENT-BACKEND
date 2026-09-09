#!/usr/bin/env python3
"""`FRONTEND_URL` points at a frontend that is actually there.

Second time this value has been wrong, so it gets a catcher.

    August: production carried `https://test.app.v-ent.co`, and every emailed
            link went to a host that is not the platform. Nothing errored.
            People clicked the link in their verification email and landed
            nowhere. See `project_frontend_url_bug`.
    9 September: the LOCAL .env still carried the same dead host, so every
            studio URL, share link and reset link built on this machine pointed
            at it. Found by copying a studio URL during a walk and reading it.

The failure is silent in both directions, which is why it survives: the value is
only ever read when a link is BUILT, and the link is then handed to somebody
else. Nothing on the machine that built it ever fetches it.

    python tools/check-frontend-url.py
    python tools/check-frontend-url.py --self-test

What it checks, and deliberately nothing more:

  * the value is set at all;
  * it is not one of the hosts known to be retired;
  * on a DEBUG machine it points at localhost, because a developer building a
    link that opens production is the same fault wearing the other hat;
  * it parses as an absolute http(s) URL with a host.

It does NOT fetch the URL. A checker that needs the frontend running is a
checker that fails on a backend-only machine and gets ignored.
"""
import os
import sys
from urllib.parse import urlparse

#: Hosts that were once right and are now dead. Each is here because a link
#: built with it actually reached somebody.
RETIRED = {
    'test.app.v-ent.co': 'retired in August; every emailed link 404d',
    'app.v-ent.co': '301s to the apex now; the apex is canonical',
    'vermillionent.pythonanywhere.com': 'the old host, gone',
}


def read_env_value(path, name):
    """The value of one key in a .env file, or None. No dependency on dotenv."""
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                if key.strip() == name:
                    return value.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def judge(url, debug):
    """`None` if it is fine, or a sentence saying what is wrong with it.

    `debug` is the machine's own DEBUG setting: a developer whose links open
    production is as wrong as production opening a dead test host, and it is
    the half nobody thinks of.
    """
    if not url:
        return 'FRONTEND_URL is not set. Every link built here has no host.'

    parts = urlparse(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return 'FRONTEND_URL is not an absolute http(s) URL: %r' % url

    host = parts.hostname
    if host in RETIRED:
        return 'FRONTEND_URL points at %s, which is %s' % (host, RETIRED[host])

    local = host in ('localhost', '127.0.0.1', '0.0.0.0')
    if debug and not local:
        return ('DEBUG is on but FRONTEND_URL is %s. Links built on this '
                'machine would open production.' % host)
    if not debug and local:
        return ('DEBUG is off but FRONTEND_URL is %s. Links built here go '
                'nowhere for anybody else.' % host)
    return None


def self_test():
    cases = [
        ('nothing set', None, True, True),
        ('a retired host is caught', 'https://test.app.v-ent.co', False, True),
        ('the apex is fine in production', 'https://v-ent.co', False, False),
        ('localhost is fine in development', 'http://localhost:3005', True, False),
        ('localhost in production is caught', 'http://localhost:3005', False, True),
        ('production host in development is caught', 'https://v-ent.co', True, True),
        ('not a URL at all', 'v-ent.co', False, True),
        ('a port is not a problem', 'http://127.0.0.1:3001', True, False),
    ]
    failures = 0
    for name, url, debug, should_complain in cases:
        complained = judge(url, debug) is not None
        ok = complained == should_complain
        if not ok:
            failures += 1
        print('%s: %s' % ('ok  ' if ok else 'FAIL', name))
    print('%d self-test case(s) %s'
          % (len(cases), 'pass' if failures == 0 else 'FAILED'))
    return 0 if failures == 0 else 1


def main():
    if '--self-test' in sys.argv:
        return self_test()

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(here, '.env')

    url = os.environ.get('FRONTEND_URL') or read_env_value(env_path, 'FRONTEND_URL')
    debug_raw = os.environ.get('DEBUG') or read_env_value(env_path, 'DEBUG') or 'False'
    debug = str(debug_raw).strip().lower() in ('1', 'true', 'yes', 'on')

    problem = judge(url, debug)
    if problem:
        print('FRONTEND_URL: %s' % (url if url else '(not set)'))
        print(problem)
        print('')
        print('Every link this machine builds carries this host: verification')
        print('and reset emails, share links, studio browser sources, short')
        print('links. Nothing here ever fetches them, so a wrong value is')
        print('silent until it reaches somebody else.')
        print('1 frontend URL checked, 1 wrong')
        return 1

    print('FRONTEND_URL=%s, DEBUG=%s' % (url, debug))
    print('1 frontend URL checked, 0 wrong')
    return 0


if __name__ == '__main__':
    sys.exit(main())
