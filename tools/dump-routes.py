#!/usr/bin/env python3
"""Write every URL pattern this backend serves, one per line.

Feeds `V-ENT-FRONTEND/scripts/check-api-paths.mjs`, which joins the list
against every path the frontend fetches and reports the ones that go nowhere.

Run by check-all before that row, against the backend being committed, into
a fresh file. The workspace copy of this script wrote `V-ENT/tools/_routes.txt`
and nothing ran it: the list the catcher read was from 12 September on
29 September, which is the snapshot-nobody-refreshes failure its own header
warned about.

    python tools/dump-routes.py --out /tmp/routes.txt
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    out = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else os.path.join(REPO, 'tools', '_routes.txt')
    sys.path.insert(0, REPO)
    os.chdir(REPO)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vent.settings')
    os.environ.setdefault('DB_ENGINE', 'sqlite')

    import django
    django.setup()
    from django.urls import get_resolver

    found = []

    def walk(resolver, prefix=''):
        for entry in resolver.url_patterns:
            if hasattr(entry, 'url_patterns'):
                walk(entry, prefix + str(entry.pattern))
            else:
                found.append(prefix + str(entry.pattern))

    walk(get_resolver())
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(sorted(set(found))) + '\n')
    print('%d route(s) written to %s' % (len(set(found)), out))


if __name__ == '__main__':
    main()
