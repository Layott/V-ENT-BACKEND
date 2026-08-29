"""Solo challenges, per-game modes, and a country instead of a made-up region.

CEO, 29 August 2026, looking at the Challenge a team form: "should be able to
create solo challenges also and depending on the game other options specific to
that game should pop up, like if its freefire thats selected, option of the mode
like if it'll be clash squad or battle royale, or craftland, then the format
based off the mode they picked etc... Then the region is wrong and should be
countries, not nigerian regions."

Three faults, and the middle one is the interesting one. The form offered
Bo1/Bo3/Bo5 for every game on the platform, and that is wrong nearly everywhere
it was used: a Free Fire battle royale is scored on points across N matches and
has no "best of", Clash Squad is first-to-N rounds rather than a series of maps,
and Lone Wolf is fixed by the game itself at first to five. A picker that offers
a format the mode does not have is a picker that produces scrims nobody can
turn up and play.
"""
import json
import uuid

from django.test import TestCase
from django.utils import timezone

from .game_modes import mode_for, modes_for
from .models import Games, Scrim, Teams, Users


def a_user(name):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
    )
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class GameModeCatalogueTests(TestCase):
    """The catalogue itself, before anything is posted with it."""

    def test_free_fire_has_the_four_modes_people_actually_play(self):
        ids = {m['id'] for m in modes_for('Free Fire')}
        self.assertEqual(
            ids, {'battle_royale', 'clash_squad', 'lone_wolf', 'craftland'})

    def test_a_battle_royale_is_counted_in_matches_not_in_a_best_of(self):
        """Twelve squads drop together. There is no "best of three" to win."""
        br = mode_for('Free Fire', 'battle_royale')
        self.assertTrue(all('match' in f for f in br['formats']), br['formats'])
        self.assertNotIn('Bo3', br['formats'])

    def test_clash_squad_is_counted_in_rounds(self):
        cs = mode_for('Free Fire', 'clash_squad')
        self.assertTrue(all('round' in f.lower() for f in cs['formats']), cs['formats'])

    def test_lone_wolf_offers_only_what_the_game_allows(self):
        """Nine rounds, first to five, and Free Fire fixes that. Offering a
        choice would invent a format the game does not have."""
        lw = mode_for('Free Fire', 'lone_wolf')
        self.assertEqual(lw['formats'], ['First to 5 rounds'])
        self.assertIn(1, lw['sizes'])

    def test_craftland_asks_for_the_map_code(self):
        """It is somebody's own map. Without the code the opponent cannot
        find it."""
        self.assertIn('map_code', mode_for('Free Fire', 'craftland')['asks'])

    def test_ea_fc_is_one_against_one(self):
        for mode in modes_for('EA FC'):
            if mode['id'] == 'ultimate_team':
                self.assertEqual(mode['sizes'], [1])
                break
        else:
            self.fail('EA FC has no Ultimate Team mode')

    def test_the_yearly_ea_fc_titles_share_one_shape(self):
        """The catalogue carries EA FC, EA FC 24 and EA FC 25 as separate
        rows for the same game."""
        self.assertEqual([m['id'] for m in modes_for('EA FC 25')],
                         [m['id'] for m in modes_for('EA FC')])

    def test_a_game_with_no_researched_modes_gets_an_honest_generic(self):
        """Inventing mode names for a game nobody here plays competitively is
        worse than saying nothing: a wrong name in a picker reads as a fact."""
        modes = modes_for('Some Game That Does Not Exist')
        self.assertEqual(len(modes), 1)
        self.assertEqual(modes[0]['id'], 'standard')

    def test_an_unknown_mode_on_a_known_game_is_not_invented(self):
        self.assertIsNone(mode_for('Free Fire', 'rocket_league_please'))


class ScrimCreateTests(TestCase):
    def setUp(self):
        self.me, self.auth = a_user('poster')
        # The catalogue matches on the title, so it has to be the real one,
        # and a migration may already have seeded it.
        self.free_fire, _ = Games.objects.get_or_create(game_title='Free Fire')
        self.team = Teams.objects.create(
            team_name='Vermillion %s' % uuid.uuid4().hex[:5], game=self.free_fire,
            description='x', team_creator=self.me, team_owner=self.me,
            penalty_points=0, number_of_members=1,
        )

    def _post(self, **body):
        return self.client.post('/scrim/create/', data=json.dumps(body),
                                content_type='application/json', **self.auth)

    # ------------------------------------------------------------ solo
    def test_a_player_can_post_a_solo_challenge_with_no_team(self):
        res = self._post(solo=True, game='Free Fire', mode='lone_wolf',
                         country='Nigeria')
        self.assertEqual(res.status_code, 201, res.content[:400])
        scrim = Scrim.objects.get()
        self.assertTrue(scrim.is_solo)
        self.assertIsNone(scrim.team_id)
        self.assertEqual(scrim.player_id, self.me.user_id)
        self.assertEqual(scrim.team_size, 1)

    def test_a_solo_challenge_says_so_in_the_payload(self):
        res = self._post(solo=True, game='Free Fire', mode='lone_wolf')
        row = res.json()['data']['scrim']
        self.assertTrue(row['is_solo'])
        self.assertTrue(row['team_a']['solo'])
        self.assertEqual(row['team_a']['name'], self.me.username)

    def test_a_team_scrim_still_needs_a_team_you_belong_to(self):
        stranger, stranger_auth = a_user('stranger')
        res = self.client.post(
            '/scrim/create/',
            data=json.dumps({'team_id': self.team.team_id, 'game': 'Free Fire',
                             'mode': 'clash_squad'}),
            content_type='application/json', **stranger_auth)
        self.assertEqual(res.status_code, 403, res.content[:300])

    def test_a_solo_post_cannot_ask_for_a_mode_that_needs_a_squad(self):
        """Clash Squad is four a side. Accepting a solo one wastes the time of
        whoever turns up."""
        res = self._post(solo=True, game='Free Fire', mode='clash_squad',
                         team_size=4)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'VALIDATION_ERROR')

    # ------------------------------------------------------------ modes
    def test_the_mode_has_to_be_one_the_game_has(self):
        res = self._post(solo=True, game='Free Fire', mode='hardpoint')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'UNKNOWN_MODE')
        self.assertIn('Clash Squad', res.json()['message'])

    def test_a_format_from_another_mode_is_refused(self):
        """"Bo3" is a real format and meaningless for a battle royale. The
        form should never offer it there, and the endpoint should not take it
        if the form is edited before it is sent."""
        res = self._post(solo=True, game='Free Fire', mode='battle_royale',
                         format='Bo3')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'UNKNOWN_FORMAT')

    def test_a_format_the_mode_does_have_is_kept(self):
        res = self._post(solo=True, game='Free Fire', mode='battle_royale',
                         format='3 matches')
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.assertEqual(Scrim.objects.get().match_format, '3 matches')

    def test_craftland_without_a_map_code_is_refused(self):
        res = self._post(solo=True, game='Free Fire', mode='craftland')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'MAP_CODE_REQUIRED')

    def test_craftland_with_a_map_code_is_posted_and_keeps_it(self):
        res = self._post(solo=True, game='Free Fire', mode='craftland',
                         map_code='#12345678', format='First to 5 rounds')
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.assertEqual(Scrim.objects.get().map_code, '#12345678')

    def test_the_payload_carries_a_readable_mode_name(self):
        res = self._post(solo=True, game='Free Fire', mode='lone_wolf')
        self.assertEqual(res.json()['data']['scrim']['mode_label'], 'Lone Wolf')

    # ---------------------------------------------------------- country
    def test_a_country_is_stored_as_a_country(self):
        """The old list mixed Nigerian zones, ISO codes and continents in one
        picker, so it could not be compared with a player's own country."""
        res = self._post(solo=True, game='Free Fire', mode='lone_wolf',
                         country='Nigeria')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Scrim.objects.get().country, 'Nigeria')

    def test_the_list_can_be_filtered_by_country(self):
        self._post(solo=True, game='Free Fire', mode='lone_wolf', country='Nigeria')
        self._post(solo=True, game='Free Fire', mode='lone_wolf', country='Kenya')
        res = self.client.get('/scrim/list/', {'country': 'Nigeria'})
        rows = res.json()['data']['scrims']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['country'], 'Nigeria')

    def test_the_list_can_be_filtered_to_solo_only(self):
        self._post(solo=True, game='Free Fire', mode='lone_wolf')
        self.client.post('/scrim/create/',
                         data=json.dumps({'team_id': self.team.team_id,
                                          'game': 'Free Fire', 'mode': 'clash_squad'}),
                         content_type='application/json', **self.auth)
        rows = self.client.get('/scrim/list/', {'kind': 'solo'}).json()['data']['scrims']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['is_solo'])

    # -------------------------------------------------------- challenge
    def test_a_solo_challenge_can_name_a_player(self):
        rival, _ = a_user('rival')
        res = self._post(solo=True, game='Free Fire', mode='lone_wolf',
                         opponent=rival.username)
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.assertEqual(Scrim.objects.get().challenged_player_id, rival.user_id)

    def test_you_cannot_challenge_yourself(self):
        res = self._post(solo=True, game='Free Fire', mode='lone_wolf',
                         opponent=self.me.username)
        self.assertEqual(res.status_code, 400)

    def test_another_player_accepts_a_solo_challenge_without_a_team(self):
        self._post(solo=True, game='Free Fire', mode='lone_wolf')
        scrim = Scrim.objects.get()

        rival, rival_auth = a_user('rival')
        res = self.client.post(f'/scrim/{scrim.id}/accept/', data=json.dumps({}),
                               content_type='application/json', **rival_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        scrim.refresh_from_db()
        self.assertEqual(scrim.status, 'accepted')
        self.assertEqual(scrim.opponent_player_id, rival.user_id)

    def test_you_cannot_accept_your_own_challenge(self):
        self._post(solo=True, game='Free Fire', mode='lone_wolf')
        scrim = Scrim.objects.get()
        res = self.client.post(f'/scrim/{scrim.id}/accept/', data=json.dumps({}),
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)

    def test_a_challenge_aimed_at_one_player_refuses_everybody_else(self):
        rival, _ = a_user('rival')
        self._post(solo=True, game='Free Fire', mode='lone_wolf',
                   opponent=rival.username)
        scrim = Scrim.objects.get()

        _, other_auth = a_user('other')
        res = self.client.post(f'/scrim/{scrim.id}/accept/', data=json.dumps({}),
                               content_type='application/json', **other_auth)
        self.assertEqual(res.status_code, 403)

    # --------------------------------------------------------- catalogue
    def test_the_form_can_read_the_catalogue_without_signing_in(self):
        """Somebody deciding whether to join should be able to see what the
        platform runs."""
        res = self.client.get('/scrim/games/')
        self.assertEqual(res.status_code, 200)
        games = res.json()['data']['games']
        self.assertIn('Free Fire', games)
        self.assertIn('clash_squad', [m['id'] for m in games['Free Fire']])
