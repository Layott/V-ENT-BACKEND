"""A tournament inside an event carries the event's website look (P8).

CEO, inbox 480: "we want people to be able to whitelist v-ent on their site or
put tournament registrations and brackets system under their own website from
v-ent." A tournament's widgets on the organiser's website start in the colours
the organiser already chose for the event's own website, so the event block on
the tournament payload says what they are. Read through vent_event.site, the
one reader of those settings.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_tournament.models import Tournament

from .models import Event, EventTournamentLink


class BrandLookTests(TestCase):
    def setUp(self):
        self.organiser = Users.objects.create(
            username='bl_org', email='bl_org@vent.test', is_active=True)
        game = Games.objects.create(game_title='EA FC BL')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Look Festival', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=8),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4),
            site_accent='#c0392b', site_theme='light')
        self.tournament = Tournament.objects.create(
            tournament_title='Look Cup', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=6),
            bracket_type='single_elimination', is_draft=False)

    def event_block(self):
        res = self.client.get('/tournament/view-tournament/%s/' % self.tournament.slug)
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        return data.get('tournament', data).get('event')

    def test_brand_carries_the_site_look(self):
        EventTournamentLink.objects.create(
            event=self.event, tournament=self.tournament, linked_by=self.organiser)
        block = self.event_block()
        self.assertEqual(block['site'], {'accent': '#c0392b', 'theme': 'light'})

    def test_no_event_no_look(self):
        self.assertIsNone(self.event_block())

    def test_an_unset_look_reads_as_the_defaults(self):
        self.event.site_accent = ''
        self.event.site_theme = ''
        self.event.save()
        EventTournamentLink.objects.create(
            event=self.event, tournament=self.tournament, linked_by=self.organiser)
        self.assertEqual(self.event_block()['site'], {'accent': '', 'theme': 'dark'})
