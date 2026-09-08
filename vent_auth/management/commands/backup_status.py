"""What the platform itself can see about its own backups.

    python manage.py backup_status
    python manage.py backup_status --json
    python manage.py backup_status --max-age-hours 30

## Why this exists

`deploy/backup.sh` knows whether the run it just did worked. `--check-freshness`
knows whether a run happened at all. Both of those live in cron, and cron is
exactly the thing that stops running without telling anybody.

This is the same question asked from inside the application, where a person can
reach it without SSH. It reads the backup directory and reports what is there:
the newest dump, how old it is, whether it is big enough to be real, and how
many are being kept. Nothing about it needs a shell on the box.

It is deliberately READ ONLY. It does not take a backup, and it must not: a
command that can write gigabytes to disk is not a thing to leave reachable, and
the nightly cron is the right place to do it from.

The size floor is a floor, not a target. A dump of this platform was 152K on
7 September and 156K on 8 September; anything under 50K is a dump of a schema
with nothing in it, which is the failure this whole area exists to catch.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand

DEFAULT_DIR = '/srv/vent/backups'

# Under this, a dump cannot be holding the platform. See the module docstring.
MIN_BYTES = 50 * 1024

# A nightly backup plus drift. Same figure as backup.sh --check-freshness, and
# it is the same judgement, so if one moves the other must.
DEFAULT_MAX_AGE_HOURS = 30


class Command(BaseCommand):
    help = 'Report on the state of the database backups without needing a shell on the box.'

    def add_arguments(self, parser):
        parser.add_argument('--dir', default=os.environ.get('BACKUP_DEST', DEFAULT_DIR))
        parser.add_argument('--max-age-hours', type=int, default=DEFAULT_MAX_AGE_HOURS)
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        report = inspect_backups(Path(options['dir']), options['max_age_hours'])

        if options['as_json']:
            self.stdout.write(json.dumps(report, indent=2))
            return

        if not report['ok']:
            self.stdout.write(self.style.ERROR(f"backups: {report['problem']}"))
        else:
            self.stdout.write(self.style.SUCCESS('backups: ok'))

        self.stdout.write(f"  directory   {report['directory']}")
        self.stdout.write(f"  dumps kept  {report['count']}")
        if report['newest']:
            self.stdout.write(f"  newest      {report['newest']}")
            self.stdout.write(f"  age         {report['age_hours']} hours")
            self.stdout.write(f"  size        {report['size_bytes']} bytes")


def inspect_backups(directory, max_age_hours=DEFAULT_MAX_AGE_HOURS, now=None):
    """The whole answer as a dict, so a test and a screen read the same thing.

    Split out from `handle` deliberately: a management command whose logic lives
    inside `handle` can only be tested by capturing stdout, and a test that
    parses printed text breaks every time the wording changes.
    """
    now = now or datetime.now(timezone.utc)
    report = {
        'directory': str(directory),
        'count': 0,
        'newest': None,
        'age_hours': None,
        'size_bytes': None,
        'ok': False,
        'problem': None,
    }

    if not directory.is_dir():
        report['problem'] = f'{directory} does not exist. Nothing is being backed up here.'
        return report

    dumps = sorted(directory.glob('db-*.sql.gz'), key=lambda p: p.stat().st_mtime, reverse=True)
    report['count'] = len(dumps)

    if not dumps:
        report['problem'] = 'there is no database dump at all'
        return report

    newest = dumps[0]
    stat = newest.stat()
    # Clamped at zero, and this is not defensive padding: without it a dump
    # written moments ago reports MINUS ONE hours old. The file's mtime can sit
    # a few milliseconds ahead of `now` (filesystem timestamp granularity, or
    # ordinary clock skew on a networked disk), which makes the difference
    # slightly negative, and `//` floors toward negative infinity, so -0.003
    # seconds becomes -1 hours rather than 0.
    #
    # Found by a test that failed two runs in five. Nothing can be younger than
    # zero hours old, so the clamp is also just true.
    age_seconds = max(0.0, (now - datetime.fromtimestamp(stat.st_mtime, timezone.utc)).total_seconds())
    age_hours = int(age_seconds // 3600)

    report['newest'] = newest.name
    report['age_hours'] = age_hours
    report['size_bytes'] = stat.st_size

    # Age first. A stale backup is the more dangerous of the two, because a
    # fresh empty one at least gets noticed the moment somebody looks.
    if age_hours > max_age_hours:
        report['problem'] = (
            f'the newest backup is {age_hours} hours old, over the {max_age_hours} hour limit. '
            'A run was missed and nothing reported it.'
        )
        return report

    if stat.st_size < MIN_BYTES:
        report['problem'] = (
            f'the newest backup is only {stat.st_size} bytes, under the {MIN_BYTES} byte floor. '
            'That is a dump with no data in it.'
        )
        return report

    report['ok'] = True
    return report
