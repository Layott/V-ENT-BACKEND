"""The calendar, which is where recurring billing actually goes wrong.

Gate D2. Every case here is a date that breaks a naive implementation, and each
one is written as the sentence somebody would use to report it.
"""
from datetime import datetime, timezone as dt_timezone

from django.test import SimpleTestCase

from vent_billing import clock


def at(year, month, day, hour=9, minute=15):
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


class MonthlyClockTests(SimpleTestCase):
    """"I subscribed on the 31st and got billed on the 26th by December.\""""

    def test_january_31_lands_on_the_last_day_of_february(self):
        end = clock.add_period(at(2026, 1, 31), clock.MONTHLY, 31, 1)
        self.assertEqual((end.year, end.month, end.day), (2026, 2, 28))

    def test_the_anchor_comes_back_in_a_month_that_has_the_day(self):
        # The whole point of storing an anchor. Stepping from the clamped 28
        # February with the anchor still 31 gives 31 March, not 28 March.
        end = clock.add_period(at(2026, 2, 28), clock.MONTHLY, 31, 1)
        self.assertEqual((end.year, end.month, end.day), (2026, 3, 31))

    def test_a_whole_year_from_the_31st_never_drifts(self):
        cursor = at(2026, 1, 31)
        days = []
        for _ in range(12):
            cursor = clock.add_period(cursor, clock.MONTHLY, 31, 1)
            days.append(cursor.day)
        # 30-day months clamp to 30, February to 28, and every 31-day month
        # comes back to 31. A timedelta(days=30) implementation produces a
        # strictly decreasing sequence here, which is the bug.
        self.assertEqual(days, [28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31, 31])

    def test_a_leap_february_gets_the_29th(self):
        end = clock.add_period(at(2028, 1, 31), clock.MONTHLY, 31, 1)
        self.assertEqual((end.month, end.day), (2, 29))

    def test_december_rolls_the_year(self):
        end = clock.add_period(at(2026, 12, 15), clock.MONTHLY, 15, 12)
        self.assertEqual((end.year, end.month, end.day), (2027, 1, 15))

    def test_the_time_of_day_is_kept(self):
        end = clock.add_period(at(2026, 3, 10, 6, 45), clock.MONTHLY, 10, 3)
        self.assertEqual((end.hour, end.minute), (6, 45))
        self.assertEqual(end.tzinfo, dt_timezone.utc)


class YearlyClockTests(SimpleTestCase):
    """A yearly subscription taken out on 29 February."""

    def test_a_leap_day_clamps_to_the_28th(self):
        end = clock.add_period(at(2028, 2, 29), clock.YEARLY, 29, 2)
        self.assertEqual((end.year, end.month, end.day), (2029, 2, 28))

    def test_and_comes_back_on_the_next_leap_year(self):
        cursor = at(2028, 2, 29)
        seen = []
        for _ in range(4):
            cursor = clock.add_period(cursor, clock.YEARLY, 29, 2)
            seen.append((cursor.year, cursor.month, cursor.day))
        self.assertEqual(seen, [(2029, 2, 28), (2030, 2, 28),
                                (2031, 2, 28), (2032, 2, 29)])

    def test_a_yearly_plan_keeps_its_month(self):
        end = clock.add_period(at(2026, 3, 1), clock.YEARLY, 1, 3)
        self.assertEqual((end.year, end.month, end.day), (2027, 3, 1))

    def test_it_never_charges_twice_in_one_year(self):
        cursor = at(2028, 2, 29)
        years = []
        for _ in range(6):
            cursor = clock.add_period(cursor, clock.YEARLY, 29, 2)
            years.append(cursor.year)
        self.assertEqual(years, sorted(set(years)))
        self.assertEqual(len(years), len(set(years)))


class CatchUpTests(SimpleTestCase):
    """A box that was off for six weeks."""

    def test_periods_between_reaches_past_the_gap(self):
        rows = clock.periods_between(at(2026, 1, 15), at(2026, 4, 20),
                                     clock.MONTHLY, 15, 1)
        self.assertEqual([(r.month, r.day) for r in rows],
                         [(2, 15), (3, 15), (4, 15), (5, 15)])

    def test_it_stops_rather_than_running_away(self):
        rows = clock.periods_between(at(2020, 1, 1), at(2090, 1, 1),
                                     clock.MONTHLY, 1, 1, limit=5)
        self.assertEqual(len(rows), 5)


class IntervalTests(SimpleTestCase):
    def test_an_unknown_interval_raises_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            clock.add_period(at(2026, 1, 1), 'weekly', 1, 1)
