"""Every state `backup_status` can report, built as a real directory of files.

Both directions, because a checker that only ever proves the healthy case has
two meanings when it says ok and nothing distinguishes them. Each of these is a
condition that has actually happened on this project or is one step away from it:

- an empty directory: what the box looked like before 7 September, when the
  cron had been failing on every run since it was written
- a dump under the size floor: the schema-only dump, which is what a failing
  mysqldump leaves behind while still exiting 0
- a stale dump: a cron that stopped running, which nothing else can detect,
  because a job that does not run produces no failure to report
"""
import gzip
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from vent_auth.management.commands.backup_status import MIN_BYTES, inspect_backups


class BackupStatusTests(SimpleTestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _dump(self, name, size=MIN_BYTES + 5000, age_hours=0):
        path = self.dir / name
        # Written through gzip so the fixture is a real gzip, but the payload is
        # incompressible: the check reads the size ON DISK, and repeated SQL
        # text compresses about two hundred to one, so a "60K" fixture of
        # repeated CREATE TABLE lines lands at 272 bytes and trips the floor it
        # was built to clear. Random bytes make the on-disk size the size asked
        # for, which is the number under test.
        with gzip.open(path, 'wb') as fh:
            fh.write(b'-- CREATE TABLE `vent_auth_users`\n')
            fh.write(os.urandom(size))
        if age_hours:
            when = time.time() - age_hours * 3600
            os.utime(path, (when, when))
        return path

    def test_a_missing_directory_is_reported_not_crashed(self):
        report = inspect_backups(self.dir / 'nope')
        self.assertFalse(report['ok'])
        self.assertIn('does not exist', report['problem'])

    def test_an_empty_directory_is_a_problem(self):
        report = inspect_backups(self.dir)
        self.assertFalse(report['ok'])
        self.assertIn('no database dump', report['problem'])
        self.assertEqual(report['count'], 0)

    def test_a_healthy_recent_dump_is_ok(self):
        self._dump('db-2026-09-08-0300.sql.gz')
        report = inspect_backups(self.dir)
        self.assertTrue(report['ok'], report['problem'])
        self.assertEqual(report['newest'], 'db-2026-09-08-0300.sql.gz')
        self.assertEqual(report['age_hours'], 0)
        self.assertIsNone(report['problem'])

    def test_a_dump_below_the_size_floor_is_refused(self):
        path = self.dir / 'db-tiny.sql.gz'
        with gzip.open(path, 'wb') as fh:
            fh.write(b'-- schema only, no rows\n')
        self.assertLess(path.stat().st_size, MIN_BYTES)

        report = inspect_backups(self.dir)
        self.assertFalse(report['ok'])
        self.assertIn('no data in it', report['problem'])

    def test_a_stale_dump_is_refused_even_though_it_is_a_good_file(self):
        """The one nothing else catches: the file is fine, the cron is dead."""
        # 40.5 rather than 40. The file is stamped at one instant and the age is
        # measured at a later one, so a flat 40 is really 39.9999 hours by the
        # time it is read and floors to 39. Standalone the gap is small enough
        # to round the way the test wanted; inside the full suite it was not,
        # and the test failed there and only there. The half hour of margin
        # makes the fixture mean what it says regardless of how loaded the
        # machine is.
        self._dump('db-old.sql.gz', age_hours=40.5)
        report = inspect_backups(self.dir)
        self.assertFalse(report['ok'])
        self.assertEqual(report['age_hours'], 40)
        self.assertIn('A run was missed', report['problem'])

    def test_the_age_limit_is_the_one_asked_for(self):
        self._dump('db-old.sql.gz', age_hours=40)
        self.assertTrue(inspect_backups(self.dir, max_age_hours=48)['ok'])
        self.assertFalse(inspect_backups(self.dir, max_age_hours=24)['ok'])

    def test_the_newest_is_chosen_by_time_not_by_name(self):
        """A name sorts lexically and a backup taken after a clock change or
        restored from elsewhere will not sort where its age says it should."""
        self._dump('db-2026-09-01-0300.sql.gz', age_hours=1)
        self._dump('db-2026-09-08-0300.sql.gz', age_hours=50)

        report = inspect_backups(self.dir)
        self.assertEqual(report['newest'], 'db-2026-09-01-0300.sql.gz')
        self.assertTrue(report['ok'], report['problem'])
        self.assertEqual(report['count'], 2)

    def test_only_database_dumps_are_counted(self):
        """The media archives live in the same directory and are not dumps."""
        self._dump('db-2026-09-08-0300.sql.gz')
        (self.dir / 'media-2026-09-08-0300.tar.gz').write_bytes(b'x' * 1000)

        report = inspect_backups(self.dir)
        self.assertEqual(report['count'], 1)
        self.assertTrue(report['ok'])

    def test_a_dump_stamped_in_the_future_is_zero_hours_old_not_minus_one(self):
        """The clock-skew case, pinned because it really happened.

        A dump written moments ago reported MINUS ONE hours old in two runs out
        of five: its mtime sat a few milliseconds ahead of `now`, and `//`
        floors toward negative infinity, so -0.003 seconds became -1 hours.
        Exaggerated here to five minutes in the future so the case is
        deterministic rather than a race.
        """
        path = self._dump('db-future.sql.gz')
        ahead = time.time() + 300
        os.utime(path, (ahead, ahead))

        report = inspect_backups(self.dir)
        self.assertEqual(report['age_hours'], 0)
        self.assertTrue(report['ok'], report['problem'])

    def test_age_is_measured_against_a_supplied_now(self):
        self._dump('db-2026-09-08-0300.sql.gz')
        # 31 hours and 30 minutes, not a flat 31. The file is stamped a few
        # milliseconds before `now` is taken, so a flat 31 comes out as
        # 30.9999 hours, floors to 30, and sits exactly on the 30 hour limit
        # rather than over it. The half hour puts the case unambiguously on the
        # side of the line it is meant to test.
        later = datetime.now(timezone.utc) + timedelta(hours=31, minutes=30)
        report = inspect_backups(self.dir, now=later)
        self.assertFalse(report['ok'])
        self.assertEqual(report['age_hours'], 31)
