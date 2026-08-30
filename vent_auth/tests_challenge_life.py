"""A challenge from posted to remembered.

CEO, 29-30 August 2026: "after you create challenge the user should be able to
edit it also. also when another user logs in and they see a challenge, how does
it look and if they choose to join or accept, does it work and what is the flow,
when it does work, they should then be able to talk with themselves to send
details and then record results also, the results should also show on their
profiles as history and challenges should also show past matches and games and
the data also." And: "for country should be able to open it to all, or select a
group of countries they want also."

Posting and accepting existed. Everything after the handshake did not.

The design decision worth testing hardest: a result is reported by one side and
confirmed by the other. A scrim has no referee, so whatever one player types is
the only account of what happened, and if reporting were enough the record would
be whatever the faster typist claimed.
"""
import json
import uuid

from django.test import TestCase
from django.utils import timezone

from .models import (Conversation, Games, Scrim, ScrimResult, TeamMembers,
                     Teams, Users)


def a_user(name, country=''):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
        country=country,
    )
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ChallengeLifeTests(TestCase):
    def setUp(self):
        self.poster, self.poster_auth = a_user('poster', country='Nigeria')
        self.rival, self.rival_auth = a_user('rival', country='Nigeria')
        self.game, _ = Games.objects.get_or_create(game_title='Free Fire')

    def _create(self, **body):
        payload = {'solo': True, 'game': 'Free Fire', 'mode': 'lone_wolf'}
        payload.update(body)
        res = self.client.post('/scrim/create/', data=json.dumps(payload),
                               content_type='application/json', **self.poster_auth)
        return res

    def _ref(self):
        return Scrim.objects.get().slug

    def _post(self, path, body=None, auth=None):
        return self.client.post(path, data=json.dumps(body or {}),
                                content_type='application/json',
                                **(auth or self.poster_auth))

    def _accepted(self):
        """A challenge that has been posted and accepted."""
        self._create(country='Nigeria')
        scrim = Scrim.objects.get()
        self._post('/scrim/%s/accept/' % scrim.id, {}, auth=self.rival_auth)
        scrim.refresh_from_db()
        return scrim

    # ------------------------------------------------------------- W1 country
    def test_a_challenge_can_be_open_to_everybody(self):
        res = self._create(open_to='anywhere')
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.assertEqual(Scrim.objects.get().open_to, 'anywhere')

    def test_a_challenge_can_name_a_group_of_countries(self):
        res = self._create(open_to='countries',
                           countries=['Nigeria', 'Ghana', 'Kenya'])
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.assertEqual(Scrim.objects.get().countries, ['Nigeria', 'Ghana', 'Kenya'])

    def test_a_group_with_no_countries_in_it_is_refused(self):
        res = self._create(open_to='countries', countries=[])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'NO_COUNTRIES')

    def test_somebody_outside_the_group_cannot_accept(self):
        """Enforced, not merely hidden. A filter that only hides things is a
        suggestion, and whoever edits the request gets in anyway."""
        self._create(open_to='countries', countries=['Ghana'])
        scrim = Scrim.objects.get()
        outsider, outsider_auth = a_user('outsider', country='Kenya')
        res = self._post('/scrim/%s/accept/' % scrim.id, {}, auth=outsider_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'WRONG_COUNTRY')

    def test_somebody_inside_the_group_can_accept(self):
        self._create(open_to='countries', countries=['Ghana', 'Nigeria'])
        scrim = Scrim.objects.get()
        res = self._post('/scrim/%s/accept/' % scrim.id, {}, auth=self.rival_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])

    def test_anywhere_lets_anybody_in(self):
        self._create(open_to='anywhere')
        scrim = Scrim.objects.get()
        _, far_auth = a_user('faraway', country='Brazil')
        res = self._post('/scrim/%s/accept/' % scrim.id, {}, auth=far_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])

    def test_an_open_challenge_appears_in_every_country_s_list(self):
        """A challenge open to everybody belongs in every country's list.
        Matching only the single country column hid exactly the ones that had
        been opened up."""
        self._create(open_to='anywhere')
        rows = self.client.get('/scrim/list/', {'country': 'Kenya'}).json()['data']['scrims']
        self.assertEqual(len(rows), 1)

    # --------------------------------------------------------------- W3 edit
    def test_the_poster_can_edit_while_it_is_open(self):
        self._create()
        res = self.client.patch('/scrim/%s/detail/' % self._ref(),
                                data=json.dumps({'notes': 'Bring your own gloo walls'}),
                                content_type='application/json', **self.poster_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(Scrim.objects.get().notes, 'Bring your own gloo walls')

    def test_somebody_else_cannot_edit_it(self):
        self._create()
        res = self.client.patch('/scrim/%s/detail/' % self._ref(),
                                data=json.dumps({'notes': 'mine now'}),
                                content_type='application/json', **self.rival_auth)
        self.assertEqual(res.status_code, 403)

    def test_it_cannot_be_rewritten_once_somebody_has_accepted(self):
        """The terms are what the other side agreed to. Quietly changing the
        format or the time under them is worse than refusing the edit."""
        self._accepted()
        res = self.client.patch('/scrim/%s/detail/' % self._ref(),
                                data=json.dumps({'format': 'First to 5 rounds'}),
                                content_type='application/json', **self.poster_auth)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'ALREADY_ACCEPTED')

    def test_changing_the_mode_drops_a_format_that_belonged_to_the_old_one(self):
        self._create(mode='battle_royale', format='3 matches')
        self.client.patch('/scrim/%s/detail/' % self._ref(),
                          data=json.dumps({'mode': 'lone_wolf'}),
                          content_type='application/json', **self.poster_auth)
        scrim = Scrim.objects.get()
        self.assertEqual(scrim.mode, 'lone_wolf')
        self.assertEqual(scrim.match_format, 'First to 5 rounds')

    def test_a_format_the_mode_does_not_have_is_refused_on_edit(self):
        self._create(mode='battle_royale')
        res = self.client.patch('/scrim/%s/detail/' % self._ref(),
                                data=json.dumps({'format': 'Bo3'}),
                                content_type='application/json', **self.poster_auth)
        self.assertEqual(res.status_code, 400)

    # ------------------------------------------------------------- W10 cancel
    def test_the_poster_can_call_it_off(self):
        self._create()
        res = self.client.delete('/scrim/%s/detail/' % self._ref(), **self.poster_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(Scrim.objects.get().status, 'cancelled')

    def test_a_cancelled_challenge_cannot_be_accepted(self):
        self._create()
        self.client.delete('/scrim/%s/detail/' % self._ref(), **self.poster_auth)
        scrim = Scrim.objects.get()
        res = self._post('/scrim/%s/accept/' % scrim.id, {}, auth=self.rival_auth)
        self.assertEqual(res.status_code, 409)

    def test_a_played_challenge_cannot_be_called_off(self):
        scrim = self._accepted()
        self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 5, 'score_b': 3})
        self._post('/scrim/%s/result/confirm/' % scrim.slug, {'agree': True},
                   auth=self.rival_auth)
        res = self.client.delete('/scrim/%s/detail/' % scrim.slug, **self.poster_auth)
        self.assertEqual(res.status_code, 409)

    # ---------------------------------------------------------- W4 the viewer
    def test_anybody_can_read_a_challenge_and_is_told_which_side_they_are_on(self):
        scrim = self._accepted()
        res = self.client.get('/scrim/%s/detail/' % scrim.slug, **self.rival_auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['scrim']['my_side'], 'b')

    def test_a_stranger_is_on_neither_side(self):
        scrim = self._accepted()
        _, stranger_auth = a_user('stranger')
        res = self.client.get('/scrim/%s/detail/' % scrim.slug, **stranger_auth)
        self.assertIsNone(res.json()['data']['scrim']['my_side'])

    # ------------------------------------------------------------- W5 talking
    def test_the_two_sides_can_open_a_conversation(self):
        scrim = self._accepted()
        res = self._post('/scrim/%s/talk/' % scrim.slug, {}, auth=self.rival_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertTrue(res.json()['data']['url'].startswith('/community/dm'))
        self.assertEqual(Conversation.objects.count(), 1)

    def test_opening_it_twice_reuses_the_same_conversation(self):
        scrim = self._accepted()
        self._post('/scrim/%s/talk/' % scrim.slug, {}, auth=self.rival_auth)
        self._post('/scrim/%s/talk/' % scrim.slug, {})
        self.assertEqual(Conversation.objects.count(), 1)

    def test_there_is_nobody_to_talk_to_before_it_is_accepted(self):
        self._create()
        res = self._post('/scrim/%s/talk/' % self._ref(), {})
        self.assertEqual(res.status_code, 409)

    def test_a_stranger_cannot_open_the_conversation(self):
        scrim = self._accepted()
        _, stranger_auth = a_user('stranger')
        res = self._post('/scrim/%s/talk/' % scrim.slug, {}, auth=stranger_auth)
        self.assertEqual(res.status_code, 403)

    # -------------------------------------------------------- W6 the result
    def test_one_side_reports_and_it_does_not_count_yet(self):
        scrim = self._accepted()
        res = self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 5, 'score_b': 2})
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.assertEqual(ScrimResult.objects.get().status, 'reported')
        scrim.refresh_from_db()
        self.assertEqual(scrim.status, 'accepted', 'a reported score should not '
                                                   'finish the challenge on its own')

    def test_the_result_says_which_side_reported_it(self):
        """So a page can tell "waiting for them" from "waiting for you"
        without working sides out again on the client."""
        scrim = self._accepted()
        self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 5, 'score_b': 2})
        res = self.client.get('/scrim/%s/detail/' % scrim.slug, **self.rival_auth)
        result = res.json()['data']['scrim']['result']
        self.assertEqual(result['reported_side'], 'a')

    def test_the_other_side_confirms_and_then_it_counts(self):
        scrim = self._accepted()
        self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 5, 'score_b': 2})
        res = self._post('/scrim/%s/result/confirm/' % scrim.slug, {'agree': True},
                         auth=self.rival_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(ScrimResult.objects.get().status, 'confirmed')
        scrim.refresh_from_db()
        self.assertEqual(scrim.status, 'played')

    def test_the_side_that_reported_cannot_confirm_its_own_score(self):
        """The whole point of two sides is that one of them is not you."""
        scrim = self._accepted()
        self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 5, 'score_b': 0})
        res = self._post('/scrim/%s/result/confirm/' % scrim.slug, {'agree': True})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'SAME_SIDE')

    def test_a_stranger_cannot_report_a_score(self):
        scrim = self._accepted()
        _, stranger_auth = a_user('stranger')
        res = self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 9, 'score_b': 0},
                         auth=stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_the_reporter_can_correct_themselves_before_anybody_answers(self):
        scrim = self._accepted()
        self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 5, 'score_b': 2})
        self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 5, 'score_b': 3})
        self.assertEqual(ScrimResult.objects.get().score_b, 3)

    def test_the_other_side_cannot_overwrite_a_reported_score(self):
        scrim = self._accepted()
        self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 5, 'score_b': 2})
        res = self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 0, 'score_b': 5},
                         auth=self.rival_auth)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'ALREADY_REPORTED')

    def test_a_confirmed_result_cannot_be_reported_over(self):
        scrim = self._accepted()
        self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 5, 'score_b': 2})
        self._post('/scrim/%s/result/confirm/' % scrim.slug, {'agree': True},
                   auth=self.rival_auth)
        res = self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 9, 'score_b': 0})
        self.assertEqual(res.status_code, 409)

    def test_a_negative_score_is_refused(self):
        scrim = self._accepted()
        res = self._post('/scrim/%s/result/' % scrim.slug, {'score_a': -1, 'score_b': 0})
        self.assertEqual(res.status_code, 400)

    def test_confirming_when_nobody_reported_says_so(self):
        scrim = self._accepted()
        res = self._post('/scrim/%s/result/confirm/' % scrim.slug, {'agree': True},
                         auth=self.rival_auth)
        self.assertEqual(res.status_code, 404)

    # ------------------------------------------------------------ W7 dispute
    def test_a_disagreement_keeps_both_accounts(self):
        """A dispute carrying only one set of numbers is not a dispute, it is
        a rewrite."""
        scrim = self._accepted()
        self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 5, 'score_b': 2})
        res = self._post('/scrim/%s/result/confirm/' % scrim.slug,
                         {'agree': False, 'score_a': 2, 'score_b': 5},
                         auth=self.rival_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])

        result = ScrimResult.objects.get()
        self.assertEqual(result.status, 'disputed')
        self.assertEqual((result.score_a, result.score_b), (5, 2))
        self.assertEqual((result.disputed_score_a, result.disputed_score_b), (2, 5))

    def test_a_disputed_challenge_is_not_finished(self):
        scrim = self._accepted()
        self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 5, 'score_b': 2})
        self._post('/scrim/%s/result/confirm/' % scrim.slug,
                   {'agree': False, 'score_a': 2, 'score_b': 5}, auth=self.rival_auth)
        scrim.refresh_from_db()
        self.assertNotEqual(scrim.status, 'played')

    def test_disagreeing_without_saying_what_it_was_is_refused(self):
        scrim = self._accepted()
        self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 5, 'score_b': 2})
        res = self._post('/scrim/%s/result/confirm/' % scrim.slug, {'agree': False},
                         auth=self.rival_auth)
        self.assertEqual(res.status_code, 400)

    # ------------------------------------------------------------ W8 history
    def _play(self, score_a, score_b):
        scrim = self._accepted()
        self._post('/scrim/%s/result/' % scrim.slug,
                   {'score_a': score_a, 'score_b': score_b})
        self._post('/scrim/%s/result/confirm/' % scrim.slug, {'agree': True},
                   auth=self.rival_auth)
        return scrim

    def test_a_finished_challenge_shows_on_both_profiles(self):
        self._play(5, 2)
        for who, expect in ((self.poster, 'won'), (self.rival, 'lost')):
            res = self.client.get('/scrim/history/%s/' % who.username)
            self.assertEqual(res.status_code, 200, res.content[:200])
            data = res.json()['data']
            self.assertEqual(data['record']['played'], 1)
            self.assertEqual(data['challenges'][0]['outcome'], expect)

    def test_the_record_counts_wins_losses_and_draws(self):
        self._play(5, 2)
        Scrim.objects.all().delete()
        self._play(1, 1)
        res = self.client.get('/scrim/history/%s/' % self.poster.username)
        rec = res.json()['data']['record']
        self.assertEqual(rec['drawn'], 1)

    def test_a_challenge_nobody_agreed_is_not_history(self):
        """Reported is not played. A record that counts unconfirmed scores is
        a record of what people claimed."""
        scrim = self._accepted()
        self._post('/scrim/%s/result/' % scrim.slug, {'score_a': 5, 'score_b': 2})
        res = self.client.get('/scrim/history/%s/' % self.poster.username)
        self.assertEqual(res.json()['data']['record']['played'], 0)

    def test_history_is_readable_without_signing_in(self):
        """A profile is public: the point of a record is that other people can
        see it before agreeing to play you."""
        self._play(3, 1)
        res = self.client.get('/scrim/history/%s/' % self.poster.username)
        self.assertEqual(res.status_code, 200)

    def test_history_for_somebody_who_does_not_exist(self):
        res = self.client.get('/scrim/history/nobody-at-all/')
        self.assertEqual(res.status_code, 404)

    # -------------------------------------------------------- W9 past matches
    def test_the_list_can_show_past_matches_with_their_scores(self):
        self._play(5, 2)
        res = self.client.get('/scrim/list/', {'status': 'past'})
        rows = res.json()['data']['scrims']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['result']['score_a'], 5)
        self.assertEqual(rows[0]['result']['status'], 'confirmed')

    def test_an_open_challenge_is_not_a_past_match(self):
        self._create()
        rows = self.client.get('/scrim/list/', {'status': 'past'}).json()['data']['scrims']
        self.assertEqual(len(rows), 0)

    def test_a_challenge_with_no_result_reports_none_rather_than_zeroes(self):
        """A challenge with no result is not the same as one that finished
        nil-nil."""
        self._create()
        rows = self.client.get('/scrim/list/').json()['data']['scrims']
        self.assertIsNone(rows[0]['result'])


class TeamChallengeResultTests(TestCase):
    """The same lifecycle, for a challenge between two teams."""

    def setUp(self):
        self.owner_a, self.auth_a = a_user('owner_a', country='Nigeria')
        self.owner_b, self.auth_b = a_user('owner_b', country='Nigeria')
        self.game, _ = Games.objects.get_or_create(game_title='Free Fire')

        def team(name, owner):
            t = Teams.objects.create(
                team_name='%s %s' % (name, uuid.uuid4().hex[:5]), game=self.game,
                description='x', team_creator=owner, team_owner=owner,
                penalty_points=0, number_of_members=1)
            TeamMembers.objects.create(team=t, user=owner, role='owner')
            return t

        self.team_a = team('Alpha', self.owner_a)
        self.team_b = team('Beta', self.owner_b)

    def test_a_team_member_is_on_their_team_s_side(self):
        """Somebody in the team is part of it whether or not they personally
        pressed anything."""
        res = self.client.post('/scrim/create/', data=json.dumps({
            'team_id': self.team_a.team_id, 'game': 'Free Fire',
            'mode': 'clash_squad', 'country': 'Nigeria'}),
            content_type='application/json', **self.auth_a)
        self.assertEqual(res.status_code, 201, res.content[:300])
        scrim = Scrim.objects.get()

        self.client.post('/scrim/%s/accept/' % scrim.id,
                         data=json.dumps({'team_id': self.team_b.team_id}),
                         content_type='application/json', **self.auth_b)

        player, player_auth = a_user('player', country='Nigeria')
        TeamMembers.objects.create(team=self.team_b, user=player, role='member')

        res = self.client.get('/scrim/%s/detail/' % scrim.slug, **player_auth)
        self.assertEqual(res.json()['data']['scrim']['my_side'], 'b')

    def test_either_team_can_report_and_the_other_confirms(self):
        self.client.post('/scrim/create/', data=json.dumps({
            'team_id': self.team_a.team_id, 'game': 'Free Fire',
            'mode': 'clash_squad', 'country': 'Nigeria'}),
            content_type='application/json', **self.auth_a)
        scrim = Scrim.objects.get()
        self.client.post('/scrim/%s/accept/' % scrim.id,
                         data=json.dumps({'team_id': self.team_b.team_id}),
                         content_type='application/json', **self.auth_b)

        self.client.post('/scrim/%s/result/' % scrim.slug,
                         data=json.dumps({'score_a': 4, 'score_b': 1}),
                         content_type='application/json', **self.auth_a)
        res = self.client.post('/scrim/%s/result/confirm/' % scrim.slug,
                               data=json.dumps({'agree': True}),
                               content_type='application/json', **self.auth_b)
        self.assertEqual(res.status_code, 200, res.content[:300])
        scrim.refresh_from_db()
        self.assertEqual(scrim.status, 'played')
