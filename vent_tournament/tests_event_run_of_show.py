"""An event's overlays read the event's run of show.

Found on 8 September by opening the now and next graphic on a broadcast bound
to an EVENT. It drew nothing, while the event's run sheet held 161 cues and was
marked public. `programme` was built from `EventSession` rows, the event had
none, and the sheet was never read.

A tournament has had this since the run of show shipped, and its lookup even
reaches sideways into the event's sheet when the tournament is a segment of a
convention day. The event, which is where the sheet lives, was the one surface
that could not read its own. Same class as every other one sided fault here:
two surfaces, one job, one of them built.
"""
from datetime import time, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from vent_event.models import Event, EventSession

from .models import BroadcastSession, RunSheet, RunSheetDay, RunSheetItem


def an_organiser(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('e-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class EventRunOfShowTests(TestCase):

    def setUp(self):
        self.organiser, self.auth = an_organiser('event_ros_org')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Convention With A Running Order', creator=self.organiser,
            event_type='physical', desc='x', entry_fee=0,
            start_date=now + timedelta(days=1),
            end_date=now + timedelta(days=1, hours=8),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(hours=12))
        self.ref = self.event.slug or self.event.event_id

        # 13:00 UTC is 14:00 in Lagos on the same date, so the day never wraps
        # and no assertion depends on when the suite is run.
        self.frozen = now.replace(hour=13, minute=0, second=0, microsecond=0)
        self.today = (self.frozen + timedelta(hours=1)).date()

    def a_sheet(self, visibility=RunSheet.PUBLIC):
        sheet = RunSheet.objects.create(
            event=self.event, name='Convention day one',
            time_zone='Africa/Lagos', visibility=visibility,
            created_by=self.organiser)
        day = RunSheetDay.objects.create(sheet=sheet, label='Day 1',
                                         date=self.today, position=0)
        RunSheetItem.objects.create(
            day=day, phase='DOORS', activity='Doors open', owner='Front of house',
            starts_at=time(13, 0), ends_at=time(13, 30), position=0)
        RunSheetItem.objects.create(
            day=day, phase='MAIN STAGE', activity='Cosplay parade',
            owner='Main hall', starts_at=time(13, 45), ends_at=time(14, 15),
            position=1)
        RunSheetItem.objects.create(
            day=day, phase='MAIN STAGE', activity='Finals', owner='Arena',
            starts_at=time(14, 30), ends_at=time(15, 30), position=2)
        return sheet, day

    def feed(self):
        with patch('django.utils.timezone.now', return_value=self.frozen):
            res = self.client.get('/event/%s/overlay-feed/' % self.ref)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    def studio_feed(self, sheet_visibility=RunSheet.PUBLIC):
        session = BroadcastSession.objects.create(
            event=self.event, name='Event studio')
        with patch('django.utils.timezone.now', return_value=self.frozen):
            res = self.client.get('/studio/%s/feed/' % session.token)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    # ------------------------------------------------------------- the gap

    def test_the_programme_comes_from_the_run_sheet_when_there_are_no_sessions(self):
        """The case that was broken: a real running order, an empty graphic."""
        self.a_sheet()
        data = self.feed()
        titles = [row['title'] for row in data['programme']]
        self.assertEqual(titles, ['Doors open', 'Cosplay parade', 'Finals'])

    def test_now_and_next_come_from_the_run_sheet(self):
        self.a_sheet()
        data = self.feed()
        self.assertEqual(data['event']['now_on'], 'Cosplay parade')
        self.assertEqual(data['event']['room'], 'Main hall')
        self.assertEqual(data['event']['next_on'], 'Finals')
        self.assertEqual(data['event']['next_room'], 'Arena')

    def test_the_block_itself_travels_under_the_same_name_as_a_tournament(self):
        self.a_sheet()
        block = self.feed()['run_of_show']
        self.assertEqual(block['day_label'], 'Day 1')
        self.assertEqual(block['time_zone'], 'Africa/Lagos')
        self.assertEqual(block['now']['activity'], 'Cosplay parade')
        self.assertEqual(block['next']['activity'], 'Finals')
        self.assertEqual(len(block['items']), 3)

    # -------------------------------------------------------- who may read it

    def test_a_private_sheet_is_withheld_from_the_public_feed(self):
        self.a_sheet(visibility=RunSheet.PRIVATE)
        data = self.feed()
        self.assertEqual(data['run_of_show']['now'], None)
        self.assertEqual(data['programme'], [])

    def test_the_studio_sees_a_private_sheet_because_it_holds_the_token(self):
        self.a_sheet(visibility=RunSheet.PRIVATE)
        data = self.studio_feed()
        self.assertEqual(data['run_of_show']['now']['activity'], 'Cosplay parade')
        self.assertEqual([row['title'] for row in data['programme']],
                         ['Doors open', 'Cosplay parade', 'Finals'])
        self.assertEqual(data['event']['now_on'], 'Cosplay parade')

    # ------------------------------------------------- the audience schedule

    def test_a_published_session_wins_over_the_sheet(self):
        """The audience schedule is what the audience is holding."""
        self.a_sheet()
        EventSession.objects.create(
            event=self.event, title='Keynote', stage='Main hall',
            starts_at=self.frozen - timedelta(minutes=10),
            ends_at=self.frozen + timedelta(minutes=50), is_published=True)
        data = self.feed()
        self.assertEqual(data['event']['now_on'], 'Keynote')
        self.assertEqual([row['title'] for row in data['programme']], ['Keynote'])

    def test_an_event_with_neither_draws_nothing_rather_than_breaking(self):
        data = self.feed()
        self.assertEqual(data['programme'], [])
        self.assertEqual(data['event']['now_on'], '')
        self.assertEqual(data['run_of_show']['items'], [])

    def test_the_version_moves_when_the_cue_on_screen_changes(self):
        """The sheet does not change at 14:00. The graphic has to."""
        self.a_sheet()
        first = self.feed()['version']
        later = self.frozen + timedelta(hours=1)   # 15:00 Lagos, past the finals cue
        with patch('django.utils.timezone.now', return_value=later):
            res = self.client.get('/event/%s/overlay-feed/' % self.ref)
        self.assertNotEqual(first, res.json()['data']['version'])
