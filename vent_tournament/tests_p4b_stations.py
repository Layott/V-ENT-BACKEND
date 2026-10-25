"""Stations and waves, and the clash check (P4b part 4).

Design section 12: "Stations and waves; a conflict check for players entered in
linked tournaments at the same time." A station is where a match is played
("PC 3", "Table 2"), a wave the slot it belongs to ("Wave A"). Staff set both
from the console. A person due at two matches inside one break
(match_interval_minutes) is a clash, across every tournament joined to this
one by a feed, and so is one station holding two matches at once. A clash
refuses with MATCH_CLASH and the list of what it clashes with, unless the
organiser says to keep it (allow_clash), on both the place door and the time
door.
"""
import itertools
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import BracketMatch, Progression, TournamentRegistration, TournamentStage
from .tests import client_for, make_tournament, make_user, register

_N = itertools.count(4810000)


def person():
    return make_user(next(_N))


def entry(t, user):
    """The user's one registration in the tournament, made the first time."""
    found = TournamentRegistration.objects.filter(tournament=t, user=user).first()
    return found or register(t, user)


def match(t, a, b, when=None, number=1):
    return BracketMatch.objects.create(
        tournament=t, round_number=1, match_number=number,
        participant_1=entry(t, a) if a else None,
        participant_2=entry(t, b) if b else None,
        scheduled_at=when)


def link(source_t, target_t):
    """A feed from one tournament's stage to another's, the way circuits and
    qualifiers join them."""
    s = TournamentStage.objects.create(tournament=source_t, order=0, label='Qualifier',
                                       format='single_elimination')
    g = TournamentStage.objects.create(tournament=target_t, order=0, label='Finals',
                                       format='single_elimination')
    Progression.objects.create(source=s, target=g, place_to=2)


class PlaceDoorTests(TestCase):
    def setUp(self):
        self.org = person()
        self.t = make_tournament(self.org)
        self.a, self.b = person(), person()
        self.when = timezone.now() + timedelta(days=2)
        self.m = match(self.t, self.a, self.b, self.when)
        self.url = '/tournament/match/%d/place/' % self.m.id

    def test_staff_set_the_station_and_the_wave(self):
        res = client_for(self.org).post(self.url, {'station': 'PC 3', 'wave': 'Wave A'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['status'], 'success')
        self.assertEqual(body['data']['match']['station'], 'PC 3')
        self.assertEqual(body['data']['match']['wave'], 'Wave A')
        self.m.refresh_from_db()
        self.assertEqual((self.m.station, self.m.wave), ('PC 3', 'Wave A'))

    def test_blank_clears_them(self):
        self.m.station, self.m.wave = 'PC 3', 'Wave A'
        self.m.save(update_fields=['station', 'wave'])
        res = client_for(self.org).post(self.url, {'station': '', 'wave': ''}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.m.refresh_from_db()
        self.assertEqual((self.m.station, self.m.wave), ('', ''))

    def test_a_key_left_out_is_left_alone(self):
        self.m.wave = 'Wave B'
        self.m.save(update_fields=['wave'])
        client_for(self.org).post(self.url, {'station': 'Table 2'}, format='json')
        self.m.refresh_from_db()
        self.assertEqual((self.m.station, self.m.wave), ('Table 2', 'Wave B'))

    def test_a_player_in_the_match_may_not(self):
        res = client_for(self.a).post(self.url, {'station': 'PC 1'}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'FORBIDDEN')

    def test_nobody_signed_in_may_not(self):
        res = APIClient().post(self.url, {'station': 'PC 1'}, format='json')
        self.assertIn(res.status_code, (401, 403))

    def test_a_finished_match_is_not_placed(self):
        self.m.status = 'completed'
        self.m.save(update_fields=['status'])
        res = client_for(self.org).post(self.url, {'station': 'PC 1'}, format='json')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'MATCH_NOT_OPEN')

    def test_too_long_is_refused_with_a_code(self):
        res = client_for(self.org).post(self.url, {'station': 'x' * 41}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['status'], 'error')
        self.assertTrue(res.json().get('code'))

    def test_the_public_match_row_carries_them(self):
        self.m.station, self.m.wave = 'PC 3', 'Wave A'
        self.m.save(update_fields=['station', 'wave'])
        from . import match_shape
        row = match_shape.match_row(self.m)
        self.assertEqual((row['station'], row['wave']), ('PC 3', 'Wave A'))


class ClashTests(TestCase):
    def setUp(self):
        self.org = person()
        self.t = make_tournament(self.org)
        self.a, self.b, self.c = person(), person(), person()
        self.when = timezone.now() + timedelta(days=2)
        self.m = match(self.t, self.a, self.b, self.when)

    def place(self, m, data):
        return client_for(self.org).post('/tournament/match/%d/place/' % m.id, data, format='json')

    def test_one_station_with_two_matches_at_once_is_refused(self):
        other = match(self.t, self.c, person(), self.when + timedelta(minutes=10), number=2)
        self.place(self.m, {'station': 'PC 3'})
        res = self.place(other, {'station': 'pc 3'})
        self.assertEqual(res.status_code, 409, res.content)
        body = res.json()
        self.assertEqual(body['code'], 'MATCH_CLASH')
        self.assertEqual(body['data']['clashes'][0]['kind'], 'station')
        self.assertEqual(body['data']['clashes'][0]['match_id'], self.m.id)

    def test_keep_it_anyway_saves_and_still_lists_the_clash(self):
        other = match(self.t, self.c, person(), self.when, number=2)
        self.place(self.m, {'station': 'PC 3'})
        res = self.place(other, {'station': 'PC 3', 'allow_clash': True})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['clashes'][0]['kind'], 'station')
        other.refresh_from_db()
        self.assertEqual(other.station, 'PC 3')

    def test_the_same_station_after_the_break_is_no_clash(self):
        other = match(self.t, self.c, person(), self.when + timedelta(minutes=45), number=2)
        self.place(self.m, {'station': 'PC 3'})
        res = self.place(other, {'station': 'PC 3'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['clashes'], [])

    def test_a_person_due_in_a_linked_tournament_at_the_same_time_is_a_clash(self):
        side = make_tournament(self.org)
        link(side, self.t)
        match(side, self.a, self.c, self.when + timedelta(minutes=5))
        res = client_for(self.org).post('/tournament/match/%d/time/' % self.m.id,
                                        {'scheduled_at': (self.when + timedelta(minutes=1)).isoformat()},
                                        format='json')
        self.assertEqual(res.status_code, 409, res.content)
        clash = res.json()['data']['clashes'][0]
        self.assertEqual(clash['kind'], 'person')
        self.assertEqual(clash['person'], self.a.username)
        self.assertEqual(clash['tournament']['slug'], side.slug)

    def test_an_unlinked_tournament_is_none_of_this_ones_business(self):
        side = make_tournament(self.org)
        match(side, self.a, self.c, self.when)
        res = client_for(self.org).post('/tournament/match/%d/time/' % self.m.id,
                                        {'scheduled_at': self.when.isoformat()}, format='json')
        self.assertEqual(res.status_code, 200, res.content)

    def test_the_time_door_keeps_a_clash_when_told_to(self):
        side = make_tournament(self.org)
        link(self.t, side)
        match(side, self.b, self.c, self.when)
        res = client_for(self.org).post('/tournament/match/%d/time/' % self.m.id,
                                        {'scheduled_at': self.when.isoformat(), 'allow_clash': True},
                                        format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['clashes'][0]['person'], self.b.username)

    def test_a_finished_match_is_never_a_clash(self):
        other = match(self.t, self.a, self.c, self.when, number=2)
        other.status = 'completed'
        other.save(update_fields=['status'])
        res = client_for(self.org).post('/tournament/match/%d/time/' % self.m.id,
                                        {'scheduled_at': self.when.isoformat()}, format='json')
        self.assertEqual(res.status_code, 200, res.content)

    def test_the_same_person_twice_in_one_tournament_is_a_clash(self):
        other = match(self.t, self.a, self.c, self.when + timedelta(minutes=20), number=2)
        res = client_for(self.org).post('/tournament/match/%d/time/' % other.id,
                                        {'scheduled_at': self.when.isoformat()}, format='json')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['data']['clashes'][0]['match_id'], self.m.id)

    def test_a_match_with_no_time_has_nothing_to_clash_with(self):
        loose = match(self.t, self.a, self.c, None, number=2)
        res = self.place(loose, {'station': 'PC 9'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['clashes'], [])
