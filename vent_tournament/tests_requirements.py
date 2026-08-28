"""What an organiser can demand before somebody registers.

The CEO flagged this one as needing to be done especially properly, and the
reason is in the shape of the ask: not four more toggles, but a list an
organiser composes - a connected game account, an in-game name, a team logo,
follow these accounts and tell us your username, download this and give us the
field I have named.

So the tests are mostly about two things:

  * a refusal NAMES what to do, because "you are not eligible" sends somebody to
    support and "connect your Free Fire account" they can fix in a minute
  * a tournament with no requirements set stops nobody, which is the default and
    must stay the default
"""
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from vent_auth.models import (
    GameAccount, Games, Teams, UserProfile, Users, UserWallet,
)

from . import requirements as req
from .models import (
    EntryRequirement, EntrySubmission, Tournament, TournamentRegistration,
)


def a_user(name, **extra):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('r-%s' % name)[:16], is_active=True, **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ValidationTests(TestCase):
    def test_a_country_requirement_needs_a_country(self):
        with self.assertRaises(req.RequirementError) as caught:
            req.clean({'kind': 'country', 'config': {'countries': []}})
        self.assertEqual(caught.exception.field, 'countries')

    def test_a_download_names_its_own_field(self):
        """"Riot ID" and "Epic username" are not the same question, and a
        generic "Username" asks neither."""
        with self.assertRaises(req.RequirementError) as caught:
            req.clean({'kind': 'download',
                       'config': {'url': 'https://x.test', 'field_label': ''}})
        self.assertEqual(caught.exception.field, 'field_label')

    def test_a_download_needs_somewhere_to_download_from(self):
        with self.assertRaises(req.RequirementError) as caught:
            req.clean({'kind': 'download',
                       'config': {'field_label': 'Riot ID', 'url': 'not a url'}})
        self.assertEqual(caught.exception.field, 'url')

    def test_a_social_follow_needs_at_least_one_account(self):
        with self.assertRaises(req.RequirementError):
            req.clean({'kind': 'social_follow', 'config': {'links': []}})

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(req.RequirementError):
            req.clean({'kind': 'vibes', 'config': {}})

    def test_the_catalogue_says_who_checks_each_one(self):
        by_kind = {row['kind']: row for row in req.kind_catalogue()}
        self.assertEqual(by_kind['country']['checked_by'], req.AUTOMATIC)
        self.assertEqual(by_kind['social_follow']['checked_by'], req.SUBMITTED)
        self.assertEqual(by_kind['partner_verified']['checked_by'], req.PARTNER)


class AutomaticCheckTests(TestCase):
    def setUp(self):
        self.game = Games.objects.get_or_create(game_title='Req Probe')[0]
        self.user, self.auth = a_user('entrant')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Req Probe Cup', tournament_creator=self.user,
            tournament_game=self.game, tournament_type='online',
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            bracket_type='single_elimination',
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=3), is_draft=False,
        )

    def check(self, kind, **config):
        return req.check_automatic(
            {'kind': kind, 'config': config}, self.user, tournament=self.tournament)

    def test_a_missing_game_account_says_which_game_to_connect(self):
        met, reason = self.check('game_account')
        self.assertFalse(met)
        self.assertIn('Req Probe', reason)

    def test_a_connected_account_passes(self):
        GameAccount.objects.create(user=self.user, game=self.game,
                                   game_username='entrant#1234')
        met, _reason = self.check('game_account')
        self.assertTrue(met)

    def test_an_account_with_no_in_game_name_fails_the_details_check(self):
        GameAccount.objects.create(user=self.user, game=self.game, game_username='')
        met, reason = self.check('game_details')
        self.assertFalse(met)
        self.assertIn('in-game name', reason)

    def test_the_country_refusal_names_the_countries(self):
        self.user.country = 'GH'
        self.user.save(update_fields=['country'])
        met, reason = self.check('country', countries=['NG', 'KE'])
        self.assertFalse(met)
        self.assertIn('NG', reason)

    def test_a_matching_country_passes(self):
        self.user.country = 'ng'
        self.user.save(update_fields=['country'])
        met, _reason = self.check('country', countries=['NG'])
        self.assertTrue(met)

    def test_age_asks_for_a_birthday_before_refusing_on_one(self):
        """Refusing somebody for want of a date they never entered has to say so."""
        met, reason = self.check('min_age', min_age=18)
        self.assertFalse(met)
        self.assertIn('date of birth', reason)

    def test_somebody_old_enough_passes(self):
        UserProfile.objects.create(
            user=self.user, date_of_birth=date.today() - timedelta(days=365 * 25))
        met, _reason = self.check('min_age', min_age=18)
        self.assertTrue(met)

    def test_somebody_too_young_is_refused(self):
        UserProfile.objects.create(
            user=self.user, date_of_birth=date.today() - timedelta(days=365 * 12))
        met, reason = self.check('min_age', min_age=18)
        self.assertFalse(met)
        self.assertIn('18', reason)

    def test_a_missing_profile_picture_is_named(self):
        met, reason = self.check('profile_image')
        self.assertFalse(met)
        self.assertIn('picture', reason)

    def test_a_team_without_a_logo_is_refused(self):
        team = Teams.objects.create(
            team_name='Logoless', game=self.game, description='',
            team_creator=self.user, team_owner=self.user,
            penalty_points=0, number_of_members=1)
        met, reason = req.check_automatic(
            {'kind': 'team_logo', 'config': {}}, self.user,
            tournament=self.tournament, team=team)
        self.assertFalse(met)
        self.assertIn('logo', reason)


class EvaluateTests(TestCase):
    def setUp(self):
        self.user, self.auth = a_user('evaluated')

    def test_no_requirements_stops_nobody(self):
        """The default, and it must stay the default."""
        self.assertEqual(req.evaluate([], self.user), [])
        self.assertEqual(req.blocking(req.evaluate([], self.user)), [])

    def test_a_submitted_requirement_is_unmet_until_somebody_approves_it(self):
        rows = [{'kind': 'custom_field',
                 'config': {'field_label': 'Riot ID'}, 'required': True}]
        results = req.evaluate(rows, self.user)
        self.assertFalse(results[0]['met'])
        self.assertTrue(results[0]['needs_submission'])

        waiting = req.evaluate(rows, self.user,
                               submissions={'custom_field': {'status': 'pending'}})
        self.assertFalse(waiting[0]['met'])
        self.assertFalse(waiting[0]['needs_submission'])
        self.assertIn('Waiting', waiting[0]['reason'])

        done = req.evaluate(rows, self.user,
                            submissions={'custom_field': {'status': 'approved'}})
        self.assertTrue(done[0]['met'])

    def test_a_refusal_carries_the_reason_it_was_refused(self):
        results = req.evaluate(
            [{'kind': 'custom_field', 'config': {'field_label': 'Riot ID'}}],
            self.user,
            submissions={'custom_field': {'status': 'refused',
                                          'note': 'That is not a Riot ID.'}})
        self.assertIn('not a Riot ID', results[0]['reason'])

    def test_an_optional_requirement_does_not_block(self):
        rows = [{'kind': 'custom_field',
                 'config': {'field_label': 'How did you hear about us'},
                 'required': False}]
        results = req.evaluate(rows, self.user)
        self.assertFalse(results[0]['met'])
        self.assertEqual(req.blocking(results), [])


class EndpointTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('req_owner')
        self.player, self.player_auth = a_user('req_player')
        self.stranger, self.stranger_auth = a_user('req_stranger')
        game = Games.objects.get_or_create(game_title='Endpoint Probe')[0]
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Endpoint Probe Cup', tournament_creator=self.owner,
            tournament_game=game, tournament_type='online',
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            bracket_type='single_elimination',
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=3), is_draft=False,
        )

    def url(self, suffix=''):
        return '/tournament/%s/requirements/%s' % (self.tournament.tournament_id, suffix)

    def test_anybody_can_read_what_is_required(self):
        """Before they have an account, let alone an entry fee."""
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['data']['open_to_everyone'])
        self.assertTrue(res.json()['data']['catalogue'])

    def test_the_owner_composes_the_list_in_order(self):
        res = self.client.put(self.url('set/'), data={'requirements': [
            {'kind': 'verified_email'},
            {'kind': 'social_follow',
             'config': {'links': ['https://x.com/vent']}},
        ]}, content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()['data']['requirements']
        self.assertEqual([r['kind'] for r in rows],
                         ['verified_email', 'social_follow'])
        self.assertEqual([r['order'] for r in rows], [0, 1])

    def test_a_stranger_cannot_compose_them(self):
        res = self.client.put(self.url('set/'), data={'requirements': []},
                              content_type='application/json', **self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)

    def test_the_same_requirement_twice_is_refused(self):
        res = self.client.put(self.url('set/'), data={'requirements': [
            {'kind': 'verified_email'}, {'kind': 'verified_email'},
        ]}, content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 400, res.content)

    def test_an_entrant_sees_exactly_what_they_owe(self):
        self.client.put(self.url('set/'), data={'requirements': [
            {'kind': 'profile_image'},
        ]}, content_type='application/json', **self.owner_auth)

        res = self.client.get(self.url('mine/'), **self.player_auth)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()['data']
        self.assertFalse(body['may_enter'])
        self.assertIn('picture', body['outstanding'][0]['reason'])

    def test_sending_a_submission_puts_it_in_the_queue(self):
        self.client.put(self.url('set/'), data={'requirements': [
            {'kind': 'custom_field', 'config': {'field_label': 'Riot ID'}},
        ]}, content_type='application/json', **self.owner_auth)
        rid = EntryRequirement.objects.get(tournament=self.tournament).id

        res = self.client.post(self.url('%s/submit/' % rid),
                               data={'value': {'Riot ID': 'player#1234'}},
                               content_type='application/json', **self.player_auth)
        self.assertEqual(res.status_code, 200, res.content)

        queue = self.client.get(self.url('queue/'), **self.owner_auth).json()['data']
        self.assertEqual(queue['counts']['pending'], 1)
        self.assertEqual(queue['submissions'][0]['user']['username'], 'req_player')

    def test_an_automatic_requirement_cannot_be_submitted_against(self):
        self.client.put(self.url('set/'), data={'requirements': [
            {'kind': 'profile_image'},
        ]}, content_type='application/json', **self.owner_auth)
        rid = EntryRequirement.objects.get(tournament=self.tournament).id
        res = self.client.post(self.url('%s/submit/' % rid), data={'value': 'x'},
                               content_type='application/json', **self.player_auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'NOT_SUBMITTABLE')

    def test_refusing_without_a_reason_is_refused(self):
        """They will send exactly the same thing again otherwise."""
        self.client.put(self.url('set/'), data={'requirements': [
            {'kind': 'custom_field', 'config': {'field_label': 'Riot ID'}},
        ]}, content_type='application/json', **self.owner_auth)
        rid = EntryRequirement.objects.get(tournament=self.tournament).id
        self.client.post(self.url('%s/submit/' % rid), data={'value': 'x'},
                         content_type='application/json', **self.player_auth)
        sid = EntrySubmission.objects.get().id

        res = self.client.post(self.url('queue/%s/' % sid),
                               data={'decision': 'refused'},
                               content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'note')

    def test_approving_lets_them_in(self):
        self.client.put(self.url('set/'), data={'requirements': [
            {'kind': 'custom_field', 'config': {'field_label': 'Riot ID'}},
        ]}, content_type='application/json', **self.owner_auth)
        rid = EntryRequirement.objects.get(tournament=self.tournament).id
        self.client.post(self.url('%s/submit/' % rid), data={'value': 'x'},
                         content_type='application/json', **self.player_auth)
        sid = EntrySubmission.objects.get().id
        self.client.post(self.url('queue/%s/' % sid), data={'decision': 'approved'},
                         content_type='application/json', **self.owner_auth)

        body = self.client.get(self.url('mine/'), **self.player_auth).json()['data']
        self.assertTrue(body['may_enter'])

    def test_sending_again_after_a_refusal_reopens_it(self):
        """Somebody refused for a typo fixes it without asking anybody."""
        self.client.put(self.url('set/'), data={'requirements': [
            {'kind': 'custom_field', 'config': {'field_label': 'Riot ID'}},
        ]}, content_type='application/json', **self.owner_auth)
        rid = EntryRequirement.objects.get(tournament=self.tournament).id
        self.client.post(self.url('%s/submit/' % rid), data={'value': 'wrong'},
                         content_type='application/json', **self.player_auth)
        sid = EntrySubmission.objects.get().id
        self.client.post(self.url('queue/%s/' % sid),
                         data={'decision': 'refused', 'note': 'Not a Riot ID.'},
                         content_type='application/json', **self.owner_auth)

        self.client.post(self.url('%s/submit/' % rid), data={'value': 'right#1234'},
                         content_type='application/json', **self.player_auth)
        again = EntrySubmission.objects.get()
        self.assertEqual(again.status, 'pending')
        self.assertEqual(again.note, '')

    def test_replacing_the_list_keeps_what_was_already_submitted(self):
        """Reordering must not throw away somebody's answer."""
        self.client.put(self.url('set/'), data={'requirements': [
            {'kind': 'custom_field', 'config': {'field_label': 'Riot ID'}},
        ]}, content_type='application/json', **self.owner_auth)
        rid = EntryRequirement.objects.get(tournament=self.tournament).id
        self.client.post(self.url('%s/submit/' % rid), data={'value': 'kept'},
                         content_type='application/json', **self.player_auth)

        self.client.put(self.url('set/'), data={'requirements': [
            {'kind': 'verified_email'},
            {'kind': 'custom_field', 'config': {'field_label': 'Riot ID'}},
        ]}, content_type='application/json', **self.owner_auth)

        self.assertEqual(EntrySubmission.objects.count(), 1)
        self.assertEqual(EntrySubmission.objects.get().value, 'kept')

    def test_the_entrant_is_told_which_row_to_answer(self):
        """Without the id the checklist draws a Send button with nowhere to send."""
        self.client.put(self.url('set/'), data={'requirements': [
            {'kind': 'custom_field', 'config': {'field_label': 'Riot ID'}},
        ]}, content_type='application/json', **self.owner_auth)
        rid = EntryRequirement.objects.get(tournament=self.tournament).id

        body = self.client.get(self.url('mine/'), **self.player_auth).json()['data']
        self.assertEqual(body['requirements'][0]['id'], rid)
        self.assertEqual(body['requirements'][0]['config']['field_label'], 'Riot ID')

class JoinGateTests(TestCase):
    """The refusal has to arrive over HTTP, not only from the helper.

    An eligibility rule that evaluates correctly in a unit test and is never
    consulted by the registration path is the same as no rule at all, and it is
    worse than none because the organiser believes it is in force.
    """

    def setUp(self):
        self.game = Games.objects.get_or_create(game_title='Gate Game')[0]
        self.organiser = Users.objects.create(
            username='gate_org', email='gate_org@vent.test')
        self.tournament = Tournament.objects.create(
            tournament_title='Gate Cup', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            tournament_type='online', tournament_access='individual',
            entry_fee='Free', is_draft=False, status='published',
        )
        self.player = Users.objects.create(
            username='gate_player', email='gate_player@vent.test',
            country='Nigeria', is_active=True)
        self.player.login_session_token = 'gatetoken1234567'[:16]
        self.player.login_session_created_at = timezone.now()
        self.player.save(update_fields=['login_session_token',
                                        'login_session_created_at'])
        self.headers = {'HTTP_AUTHORIZATION':
                        'Bearer %s' % self.player.login_session_token}

    def _join(self):
        return self.client.post(
            reverse('join_tournament'),
            {'tournament_id': self.tournament.tournament_id},
            content_type='application/json', **self.headers)

    def test_no_requirements_still_lets_everybody_in(self):
        """The default, and by far the most common case."""
        res = self._join()
        self.assertIn(res.status_code, (200, 201), res.content)
        self.assertTrue(TournamentRegistration.objects.filter(
            tournament=self.tournament, user=self.player).exists())

    def test_an_unmet_requirement_refuses_and_names_it(self):
        EntryRequirement.objects.create(
            tournament=self.tournament, kind='profile_image', config={}, order=0)
        res = self._join()
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json()['code'], 'REQUIREMENTS_NOT_MET')
        self.assertIn('picture', res.json()['message'])
        self.assertFalse(TournamentRegistration.objects.filter(
            tournament=self.tournament).exists())

    def test_the_refusal_lists_everything_outstanding_not_only_the_first(self):
        """Fixing one thing and being refused again for the next is the flow
        that makes people give up."""
        EntryRequirement.objects.create(
            tournament=self.tournament, kind='profile_image', config={}, order=0)
        EntryRequirement.objects.create(
            tournament=self.tournament, kind='country',
            config={'countries': ['GH']}, order=1)
        outstanding = self._join().json()['data']['outstanding']
        self.assertEqual({r['kind'] for r in outstanding},
                         {'profile_image', 'country'})

    def test_an_optional_requirement_does_not_stop_the_registration(self):
        EntryRequirement.objects.create(
            tournament=self.tournament, kind='custom_field',
            config={'field_label': 'How did you hear about us'},
            required=False, order=0)
        res = self._join()
        self.assertIn(res.status_code, (200, 201), res.content)

    def test_meeting_the_requirement_lets_them_in(self):
        EntryRequirement.objects.create(
            tournament=self.tournament, kind='country',
            config={'countries': ['NIGERIA']}, order=0)
        res = self._join()
        self.assertIn(res.status_code, (200, 201), res.content)
