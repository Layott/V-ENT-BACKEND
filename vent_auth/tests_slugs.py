"""Readable addresses, and the promise that old ones keep working.

Two properties matter. A link with the name in it resolves, and every link
already in the world - shared in a group chat, sitting in an August claim email,
bookmarked - resolves to the same thing it did before.
"""
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Teams, Users
from vent_auth.slugs import build_slug, lookup_kwargs
from vent_event.models import Event
from vent_tournament.models import Tournament


class SlugBuildingTests(TestCase):
    def setUp(self):
        self.game = Games.objects.create(game_title='Free Fire')
        self.user = Users.objects.create(username='organiser', email='o@vent.test')

    def make_tournament(self, title):
        return Tournament.objects.create(
            tournament_title=title, tournament_game=self.game, tournament_creator=self.user,
            start_date_and_time=timezone.now(), end_date_and_time=timezone.now(),
        )

    def test_a_slug_is_made_from_the_name(self):
        t = self.make_tournament('Naija Free Fire Weekly #12')
        self.assertEqual(t.slug, 'naija-free-fire-weekly-12')

    def test_two_things_with_the_same_name_do_not_collide(self):
        first = self.make_tournament('Lagos Open')
        second = self.make_tournament('Lagos Open')
        self.assertEqual(first.slug, 'lagos-open')
        self.assertEqual(second.slug, 'lagos-open-2')

    def test_renaming_does_not_move_the_address(self):
        t = self.make_tournament('Original Name')
        original = t.slug
        t.tournament_title = 'Something Else Entirely'
        t.save()
        t.refresh_from_db()
        self.assertEqual(t.slug, original)

    def test_a_name_with_nothing_sluggable_still_gets_an_address(self):
        t = self.make_tournament('中文名字')
        self.assertTrue(t.slug)
        self.assertRegex(t.slug, r'^[a-z0-9-]+$')

    def test_teams_and_events_get_them_too(self):
        team = Teams.objects.create(
            team_name='Lagos Rangers', game=self.game, description='',
            team_creator=self.user, team_owner=self.user, penalty_points=0, number_of_members=1,
        )
        self.assertEqual(team.slug, 'lagos-rangers')

        event = Event.objects.create(
            name='V-ENT Lagos Meetup', game=self.game, creator=self.user, event_type='physical',
            desc='Meetup', entry_fee=0, reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            event_date=timezone.now().date(), start_time=timezone.now().time(),
            end_time=timezone.now().time(),
        )
        self.assertEqual(event.slug, 'v-ent-lagos-meetup')


class LookupTests(TestCase):
    def test_a_number_is_read_as_an_id(self):
        self.assertEqual(lookup_kwargs('25', id_field='tournament_id'), {'tournament_id': 25})

    def test_anything_else_is_read_as_a_slug(self):
        self.assertEqual(
            lookup_kwargs('naija-weekly', id_field='tournament_id'), {'slug': 'naija-weekly'},
        )

    def test_whitespace_does_not_change_the_reading(self):
        self.assertEqual(lookup_kwargs('  25 ', id_field='tournament_id'), {'tournament_id': 25})


class ResolutionTests(TestCase):
    def setUp(self):
        self.game = Games.objects.create(game_title='Free Fire')
        self.user = Users.objects.create(username='organiser2', email='o2@vent.test')
        self.tournament = Tournament.objects.create(
            tournament_title='Naija Weekly', tournament_game=self.game,
            tournament_creator=self.user, start_date_and_time=timezone.now(),
            end_date_and_time=timezone.now(), is_draft=False, tournament_visibility='public',
        )
        self.team = Teams.objects.create(
            team_name='Kano Falcons', game=self.game, description='',
            team_creator=self.user, team_owner=self.user, penalty_points=0, number_of_members=1,
        )

    def test_a_tournament_resolves_by_id_and_by_slug(self):
        by_id = self.client.get(f'/tournament/view-tournament/{self.tournament.tournament_id}/')
        by_slug = self.client.get(f'/tournament/view-tournament/{self.tournament.slug}/')
        self.assertEqual(by_id.status_code, 200)
        self.assertEqual(by_slug.status_code, 200)
        self.assertEqual(
            by_id.json()['data']['tournament_id'],
            by_slug.json()['data']['tournament_id'],
        )

    def test_a_team_resolves_by_id_and_by_slug(self):
        by_id = self.client.get(f'/team/get-team-details/{self.team.team_id}/')
        by_slug = self.client.get(f'/team/get-team-details/{self.team.slug}/')
        self.assertEqual(by_id.status_code, 200)
        self.assertEqual(by_slug.status_code, 200)
        self.assertEqual(
            by_id.json()['data']['team']['id'], by_slug.json()['data']['team']['id'],
        )

    def test_a_slug_that_does_not_exist_is_a_404_not_a_crash(self):
        res = self.client.get('/tournament/view-tournament/no-such-thing/')
        self.assertIn(res.status_code, (404, 400))

    def test_the_payload_carries_the_slug_so_links_can_be_built(self):
        res = self.client.get(f'/tournament/view-tournament/{self.tournament.tournament_id}/')
        self.assertEqual(res.json()['data']['slug'], 'naija-weekly')
