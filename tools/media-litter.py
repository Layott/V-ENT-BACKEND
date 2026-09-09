"""The 1x1 pixel images old test runs left in the local MEDIA_ROOT.

Row 231. 7339 of them, 67 bytes each, mostly `gallery/a_*.png`. They make a
local walk look broken: a gallery of slivers where pictures should be.

Deleting a file the database still points at would be worse than leaving it, so
this asks the database FIRST and only removes what nothing references.

    venv/Scripts/python.exe tools/media-litter.py            # say what it would do
    venv/Scripts/python.exe tools/media-litter.py --delete   # do it
"""
import os
import sys

import django

# Run from anywhere: this lives in tools/ and the project is its parent.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vent.settings')
os.environ.setdefault('DB_ENGINE', 'sqlite')
django.setup()

from django.apps import apps                      # noqa: E402
from django.conf import settings                  # noqa: E402

MEDIA = str(settings.MEDIA_ROOT)
SMALL = 200          # bytes. A real photograph is never this small.


def referenced():
    """Every media path any row points at, however the model spells it."""
    out = set()
    for model in apps.get_models():
        fields = [f.name for f in model._meta.fields
                  if f.get_internal_type() in ('FileField', 'ImageField')]
        if not fields:
            continue
        for row in model.objects.all().values_list(*fields):
            for value in row:
                if value:
                    out.add(str(value).replace('\\', '/').lstrip('/'))
    return out


def main():
    keep = referenced()
    print('%d media path(s) the database points at' % len(keep))

    litter, kept, bytes_freed = [], 0, 0
    for base, _dirs, files in os.walk(MEDIA):
        for name in files:
            path = os.path.join(base, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size >= SMALL:
                continue
            rel = os.path.relpath(path, MEDIA).replace('\\', '/')
            if rel in keep:
                kept += 1
                continue
            litter.append(path)
            bytes_freed += size

    print('%d tiny file(s) nothing references, %.1f MB'
          % (len(litter), bytes_freed / 1024 / 1024))
    print('%d tiny file(s) LEFT because a row points at them' % kept)

    if '--delete' not in sys.argv:
        print('\nNothing removed. Pass --delete to remove them.')
        return 0

    gone = 0
    for path in litter:
        try:
            os.remove(path)
            gone += 1
        except OSError as err:
            print('could not remove %s: %s' % (path, err))
    print('removed %d file(s)' % gone)
    return 0


if __name__ == '__main__':
    sys.exit(main())
