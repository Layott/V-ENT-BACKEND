"""When and where a stage is played, when that is not the tournament's answer.

The spec asks for bracket dates, times and location separately from the
tournament's own, and a tournament had exactly one start, one end and one
address. Groups online on the Saturday and a playoff at a venue on the Sunday
could not be said at all.

What these tests are really holding is the INHERITANCE. Blank means the
tournament's, and there is one function that decides that, because five screens
each working it out is five chances to answer differently.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from . import stages
from .models import TournamentStage
from .tests import client_for, make_tournament, make_user


class CleanTests(TestCase):

    def _stage(self, **extra):
        base = {'format': 'round_robin', 'label': 'Groups', 'advances': 4}
        base.update(extra)
        return stages.clean(base)

    def test_a_stage_with_nothing_set_inherits(self):
        cleaned = self._stage()
        self.assertIsNone(cleaned['starts_at'])
        self.assertEqual(cleaned['place_type'], '')
        self.assertEqual(cleaned['location'], '')

    def test_it_reads_an_instant(self):
        cleaned = self._stage(starts_at='2026-10-03T09:00:00+01:00')
        self.assertEqual(cleaned['starts_at'].isoformat(),
                         '2026-10-03T09:00:00+01:00')

    def test_a_date_that_is_not_one_is_refused(self):
        with self.assertRaises(stages.StageError):
            self._stage(starts_at='saturday morning')

    def test_a_stage_cannot_end_before_it_starts(self):
        with self.assertRaises(stages.StageError):
            self._stage(starts_at='2026-10-03T09:00:00+01:00',
                        ends_at='2026-10-02T09:00:00+01:00')

    def test_a_place_it_cannot_be(self):
        with self.assertRaises(stages.StageError):
            self._stage(place_type='underwater')

    def test_the_three_it_can_be(self):
        for place in ('online', 'physical', 'hybrid'):
            self.assertEqual(self._stage(place_type=place)['place_type'], place)


class InheritanceTests(TestCase):

    def setUp(self):
        self.owner = make_user(500)
        self.tournament = make_tournament(self.owner)
        self.tournament.tournament_type = 'online'
        self.tournament.tournament_location = ''
        self.tournament.virtual_link = 'https://example.test/lobby'
        self.tournament.save(update_fields=['tournament_type',
                                            'tournament_location',
                                            'virtual_link'])

    def _stage(self, **fields):
        return TournamentStage.objects.create(
            tournament=self.tournament, order=fields.pop('order', 0),
            label='Playoff', format='single_elimination', **fields)

    def test_blank_means_the_tournaments_own(self):
        stage = self._stage()
        starts, ends, own = stage.effective_when()
        self.assertEqual(starts, self.tournament.start_date_and_time)
        self.assertEqual(ends, self.tournament.end_date_and_time)
        self.assertFalse(own)

        place, where, link, own_where = stage.effective_where()
        self.assertEqual(place, 'online')
        self.assertEqual(link, 'https://example.test/lobby')
        self.assertFalse(own_where)

    def test_a_stage_with_its_own_day_says_so(self):
        when = timezone.now() + timedelta(days=9)
        stage = self._stage(starts_at=when)
        starts, ends, own = stage.effective_when()
        self.assertEqual(starts, when)
        self.assertTrue(own)
        # Only an end was left blank, so the tournament's end still applies
        # rather than the stage having no end at all.
        self.assertEqual(ends, self.tournament.end_date_and_time)

    def test_a_stage_at_a_venue_inside_an_online_tournament(self):
        """The case the spec is actually about."""
        stage = self._stage(place_type='physical',
                            location='Landmark Centre, Lagos')
        place, where, _link, own = stage.effective_where()
        self.assertEqual(place, 'physical')
        self.assertEqual(where, 'Landmark Centre, Lagos')
        self.assertTrue(own)


class EndpointTests(TestCase):

    def setUp(self):
        self.owner = make_user(510)
        self.tournament = make_tournament(self.owner)
        self.client = client_for(self.owner)
        self.ref = self.tournament.tournament_id

    def _put(self, stage_list):
        return self.client.put('/tournament/%s/stages/set/' % self.ref,
                               {'stages': stage_list}, format='json')

    def test_a_plan_carries_its_dates_through_the_round_trip(self):
        res = self._put([
            {'format': 'round_robin', 'label': 'Groups', 'advances': 4,
             'starts_at': '2026-10-03T09:00:00+01:00',
             'place_type': 'online'},
            {'format': 'single_elimination', 'label': 'Playoff', 'advances': 0,
             'starts_at': '2026-10-04T14:00:00+01:00',
             'place_type': 'physical', 'location': 'Landmark Centre, Lagos'},
        ])
        self.assertEqual(res.status_code, 200, res.data)

        read = self.client.get('/tournament/%s/stages/' % self.ref)
        rows = read.data['data']['stages']
        self.assertEqual(rows[1]['where']['location'], 'Landmark Centre, Lagos')
        self.assertTrue(rows[1]['where']['is_its_own'])
        self.assertTrue(rows[0]['when']['is_its_own'])
        # And the tournament's own window is sent alongside, so a stage that
        # inherits reads as inheriting rather than as blank.
        self.assertIn('starts_at', read.data['data']['tournament_when'])

    def test_a_stage_with_no_dates_reports_the_tournaments(self):
        self._put([
            {'format': 'round_robin', 'label': 'Groups', 'advances': 4},
            {'format': 'single_elimination', 'label': 'Playoff', 'advances': 0},
        ])
        read = self.client.get('/tournament/%s/stages/' % self.ref)
        row = read.data['data']['stages'][0]
        self.assertFalse(row['when']['is_its_own'])
        self.assertEqual(row['when']['starts_at'],
                         self.tournament.start_date_and_time)

    def test_a_bad_date_names_the_stage_it_is_on(self):
        res = self._put([
            {'format': 'round_robin', 'label': 'Groups', 'advances': 4},
            {'format': 'single_elimination', 'label': 'Playoff', 'advances': 0,
             'starts_at': 'sometime'},
        ])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'VALIDATION_FAILED')
        self.assertEqual(res.data['stage_index'], 1)
        self.assertEqual(res.data['field'], 'starts_at')
