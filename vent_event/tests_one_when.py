"""An event has ONE answer to when it happens.

CEO, 7 September 2026: "The model has two ways to say when an event happens -
fix this."

`start_date` and `end_date` carried the comment "canonical". The legacy trio
`event_date` / `start_time` / `end_time` carried "kept for back-compat". And
`starts_at()` and `ends_at()` - which every check-in window, every door, every
reminder and every "has it started" test is computed from - read the LEGACY
trio.

Nothing reconciled them. So a caller that set the canonical pair and not the
trio moved the event everywhere it is DISPLAYED and nowhere it is REASONED
about: the page said Friday, the door still opened on Thursday. The five events
in production agree today, which is luck. This is closing the trap before it
springs rather than repairing damage, which is the only cheap time to do it.

The rule now: `start_date` wins, `save()` derives the trio from it, and the
reasoning methods read the canonical column first.
"""
from datetime import date, datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_event.models import Event


def an_event(**kwargs):
    user = Users.objects.create(
        username='when_%s' % timezone.now().timestamp(),
        email='when_%s@vent.test' % timezone.now().timestamp(),
        is_active=True)
    game, _ = Games.objects.get_or_create(game_title='EA FC 26')
    fields = {
        'name': 'When Test', 'game': game, 'creator': user,
        'event_type': 'physical', 'desc': 'x', 'entry_fee': 0,
        'reg_start_date': timezone.now(),
        'reg_end_date': timezone.now() + timedelta(days=1),
    }
    fields.update(kwargs)
    return Event.objects.create(**fields)


class TheTwoHalvesAgreeTests(TestCase):
    def test_setting_the_canonical_pair_fills_the_trio(self):
        """The fault, stated as a test.

        Before this, an event created with only `start_date` had a null
        `event_date`, so `starts_at()` returned None and every window computed
        from it was closed - on an event whose page showed a date perfectly.
        """
        start = timezone.make_aware(datetime(2026, 10, 3, 18, 30))
        end = timezone.make_aware(datetime(2026, 10, 3, 23, 0))
        e = an_event(start_date=start, end_date=end)
        e.refresh_from_db()
        self.assertEqual(e.event_date, date(2026, 10, 3))
        self.assertEqual(e.start_time, time(18, 30))
        self.assertEqual(e.end_time, time(23, 0))

    def test_setting_only_the_trio_fills_the_canonical_pair(self):
        """The older create path and every existing row do this."""
        e = an_event(event_date=date(2026, 10, 4),
                     start_time=time(9, 0), end_time=time(17, 0))
        e.refresh_from_db()
        self.assertIsNotNone(e.start_date)
        self.assertIsNotNone(e.end_date)
        self.assertEqual(timezone.localtime(e.start_date).hour, 9)
        self.assertEqual(timezone.localtime(e.end_date).hour, 17)

    def test_starts_at_and_the_canonical_column_are_the_same_moment(self):
        start = timezone.make_aware(datetime(2026, 10, 5, 14, 0))
        e = an_event(start_date=start, end_date=start + timedelta(hours=3))
        self.assertEqual(e.starts_at(), e.start_date)
        self.assertEqual(e.ends_at(), e.end_date)

    def test_moving_the_event_moves_BOTH_halves(self):
        """The exact shape that would have shipped: an edit that sets the
        canonical pair, leaving the door reading the old day."""
        e = an_event(start_date=timezone.make_aware(datetime(2026, 10, 6, 10, 0)),
                     end_date=timezone.make_aware(datetime(2026, 10, 6, 12, 0)))
        e.start_date = timezone.make_aware(datetime(2026, 11, 20, 19, 0))
        e.end_date = timezone.make_aware(datetime(2026, 11, 20, 22, 0))
        e.save()
        e.refresh_from_db()
        self.assertEqual(e.event_date, date(2026, 11, 20))
        self.assertEqual(e.start_time, time(19, 0))
        self.assertEqual(timezone.localtime(e.starts_at()).day, 20)

    def test_a_save_naming_its_fields_still_writes_the_derived_ones(self):
        """The trap the slug helper documents, in a second place.

        `save(update_fields=['start_date'])` computes the new trio and then
        drops it, because the derived columns are not in the list. That is the
        whole rename path failing silently, one model over.
        """
        e = an_event(start_date=timezone.make_aware(datetime(2026, 10, 7, 8, 0)),
                     end_date=timezone.make_aware(datetime(2026, 10, 7, 9, 0)))
        e.start_date = timezone.make_aware(datetime(2026, 12, 1, 16, 45))
        e.save(update_fields=['start_date'])
        e.refresh_from_db()
        self.assertEqual(e.event_date, date(2026, 12, 1))
        self.assertEqual(e.start_time, time(16, 45))

    def test_an_overnight_event_composed_from_the_trio_ends_the_next_day(self):
        """21:00 to 02:00 finishes tomorrow. Comparing the two times
        numerically would end it five hours before it began."""
        e = an_event(event_date=date(2026, 10, 8),
                     start_time=time(21, 0), end_time=time(2, 0))
        e.refresh_from_db()
        self.assertEqual(timezone.localtime(e.end_date).date(), date(2026, 10, 9))
        self.assertGreater(e.ends_at(), e.starts_at())

    def test_moving_it_by_the_TRIO_alone_is_not_reverted(self):
        """The fault the first version of this introduced.

        "`start_date` wins" is right for a new row and wrong for an edit that
        touched only the trio: the canonical column still holds the OLD moment,
        so preferring it reverts the change and reports success. Three self
        check-in tests caught it, because they move an event exactly this way.

        The side that CHANGED is the side that wins.
        """
        e = an_event(start_date=timezone.make_aware(datetime(2026, 10, 9, 10, 0)),
                     end_date=timezone.make_aware(datetime(2026, 10, 9, 12, 0)))
        e = Event.objects.get(pk=e.pk)          # loaded, so the baseline exists
        e.event_date = date(2027, 1, 15)
        e.start_time = time(20, 0)
        e.end_time = time(23, 0)
        e.save()
        e.refresh_from_db()
        self.assertEqual(e.event_date, date(2027, 1, 15))
        self.assertEqual(timezone.localtime(e.start_date).date(), date(2027, 1, 15))
        self.assertEqual(timezone.localtime(e.start_date).hour, 20)

    def test_two_edits_in_a_row_on_the_same_instance_both_land(self):
        """The baseline is refreshed after a save, or the second edit compares
        against the state from two saves ago."""
        e = an_event(start_date=timezone.make_aware(datetime(2026, 10, 10, 9, 0)),
                     end_date=timezone.make_aware(datetime(2026, 10, 10, 11, 0)))
        e = Event.objects.get(pk=e.pk)
        e.start_date = timezone.make_aware(datetime(2026, 10, 11, 9, 0))
        e.save()
        e.start_date = timezone.make_aware(datetime(2026, 10, 12, 15, 30))
        e.save()
        e.refresh_from_db()
        self.assertEqual(e.event_date, date(2026, 10, 12))
        self.assertEqual(e.start_time, time(15, 30))

    def test_a_string_from_a_caller_does_not_raise(self):
        """`Event.objects.create(start_time='19:00')` reaches save() before
        Django has coerced anything, and `datetime.combine` refuses a string.
        132 tests failed on this."""
        e = an_event(event_date='2026-10-13', start_time='19:00', end_time='22:00')
        e.refresh_from_db()
        self.assertEqual(e.event_date, date(2026, 10, 13))
        self.assertEqual(timezone.localtime(e.start_date).hour, 19)

    def test_an_event_with_no_dates_at_all_still_answers_None(self):
        """A draft with nothing filled in must not raise."""
        e = an_event()
        self.assertIsNone(e.starts_at())
        self.assertIsNone(e.ends_at())

    def test_the_self_check_in_window_follows_the_canonical_date(self):
        """The consequence worth naming: the door is computed from
        `starts_at()`, so an event moved by the canonical column alone used to
        keep admitting people on the old day."""
        start = timezone.now() + timedelta(days=30)
        e = an_event(start_date=start, end_date=start + timedelta(hours=4),
                     self_check_in=True)
        opens, closes = e.self_check_in_window()
        self.assertIsNotNone(opens)
        self.assertLess(opens, e.starts_at())
        self.assertEqual(closes, e.ends_at())
