"""Shortening a tournament's address.

CEO, 2 September 2026, on the tournament Share dialog: "NO SHORTEN LINK
OPTION?"

There was not. `ShortLink.event` was a required foreign key, so a tournament
had nowhere to hang one, and the share card only renders its shorten button
when a screen hands it a function to call.

A tournament link is long for the same reasons a ticket link is, and gets
shortened for the same reasons: read aloud on a stream, printed on a flyer,
dropped into a WhatsApp group. So it gets the same mechanism rather than a
second one that drifts from it - the same token alphabet, the same "asking
twice returns the code you already printed" rule, and the same refusal to
store anywhere but a path on this site.
"""
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_event.models import ShortLink
from vent_tournament.models import Tournament


class TournamentShortLinkTests(TestCase):
    def setUp(self):
        self.owner = Users.objects.create(
            username='shortOwner', email='so@vent.test',
            login_session_token='short-owner-t'[:16], is_active=True)
        self.owner.login_session_created_at = timezone.now()
        self.owner.save()
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.owner.login_session_token}

        self.stranger = Users.objects.create(
            username='shortStranger', email='ss@vent.test',
            login_session_token='short-strang'[:16], is_active=True)
        self.stranger.login_session_created_at = timezone.now()
        self.stranger.save()
        self.stranger_auth = {
            'HTTP_AUTHORIZATION': 'Bearer %s' % self.stranger.login_session_token}

        game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        self.tournament = Tournament.objects.create(
            tournament_title='Rivalvry Series S2',
            tournament_game=game, tournament_creator=self.owner,
            start_date_and_time=timezone.now() + timezone.timedelta(days=3),
            end_date_and_time=timezone.now() + timezone.timedelta(days=4),
            is_draft=False)

    def shorten(self, auth=None, **body):
        return self.client.post(
            '/tournament/%s/short-links/' % self.tournament.slug,
            data=body, content_type='application/json',
            **(self.auth if auth is None else auth))

    # -------------------------------------------------------------- creating

    def test_the_organiser_can_shorten_their_tournament(self):
        res = self.shorten()
        self.assertEqual(res.status_code, 201, res.content[:300])
        link = res.json()['data']['link']
        self.assertTrue(link['token'])
        self.assertIn(self.tournament.slug, link['target'])

    def test_the_default_target_is_the_tournament_page(self):
        self.shorten()
        row = ShortLink.objects.get(tournament=self.tournament)
        self.assertEqual(row.target, '/tournaments/%s' % self.tournament.slug)

    def test_the_link_belongs_to_the_tournament_and_not_an_event(self):
        self.shorten()
        row = ShortLink.objects.get(tournament=self.tournament)
        self.assertIsNone(row.event)
        self.assertEqual(row.owner, self.tournament)

    def test_asking_twice_returns_the_code_already_printed(self):
        """An organiser pressing the button again wants the code on their
        flyer, not a new one that makes the first look wrong."""
        first = self.shorten().json()['data']['link']['token']
        again = self.shorten()
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()['data']['link']['token'], first)
        self.assertEqual(ShortLink.objects.filter(tournament=self.tournament).count(), 1)

    def test_a_label_can_be_given_and_corrected(self):
        self.shorten(label='flyer')
        self.shorten(label='radio read')
        row = ShortLink.objects.get(tournament=self.tournament)
        self.assertEqual(row.label, 'radio read')

    # ------------------------------------------------------- what is refused

    def test_it_cannot_be_pointed_off_this_site(self):
        """Letting a caller choose somewhere to redirect to is an open
        redirect: a v-ent.co address landing on a page they control, with the
        platform's name lending it credibility."""
        for bad in ('https://evil.example/phish', '//evil.example',
                    'javascript:alert(1)', 'not-a-path'):
            with self.subTest(bad=bad):
                res = self.shorten(target=bad)
                self.assertEqual(res.status_code, 400, bad)
                self.assertEqual(res.json()['code'], 'INVALID_TARGET')

    def test_a_stranger_cannot_shorten_somebody_elses_tournament(self):
        res = self.shorten(auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(ShortLink.objects.count(), 0)

    def test_a_signed_out_caller_cannot_shorten_it(self):
        res = self.client.post(
            '/tournament/%s/short-links/' % self.tournament.slug,
            data={}, content_type='application/json')
        self.assertIn(res.status_code, (401, 403))
        self.assertEqual(ShortLink.objects.count(), 0)

    # -------------------------------------------------------- using the code

    def test_the_short_address_resolves_to_the_tournament(self):
        token = self.shorten().json()['data']['link']['token']
        res = self.client.get('/s/%s/' % token)
        self.assertIn(res.status_code, (200, 301, 302))

    def test_a_retired_link_stops_working(self):
        link_id = self.shorten().json()['data']['link']['id']
        res = self.client.delete(
            '/tournament/%s/short-links/%d/' % (self.tournament.slug, link_id),
            **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        row = ShortLink.objects.get(pk=link_id)
        self.assertFalse(row.is_active)

    def test_retiring_switches_off_rather_than_deletes(self):
        """The address is printed on things that already exist. A code that
        comes back to life pointing somewhere else is worse than one that
        stops."""
        link_id = self.shorten().json()['data']['link']['id']
        self.client.delete(
            '/tournament/%s/short-links/%d/' % (self.tournament.slug, link_id),
            **self.auth)
        self.assertTrue(ShortLink.objects.filter(pk=link_id).exists())

    # ---------------------------------------------------------- listing them

    def test_the_organiser_can_list_their_links(self):
        self.shorten(label='flyer')
        res = self.client.get(
            '/tournament/%s/short-links/' % self.tournament.slug, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        self.assertEqual(len(data['links']), 1)
        self.assertTrue(data['origin'])

    def test_a_stranger_cannot_list_them(self):
        self.shorten()
        res = self.client.get(
            '/tournament/%s/short-links/' % self.tournament.slug,
            **self.stranger_auth)
        self.assertEqual(res.status_code, 403)
