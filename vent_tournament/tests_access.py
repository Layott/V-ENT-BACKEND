"""Who may enter: protected visibility, invite codes, and the approval queue.

The first test is the important one. `protected` has been a choice in the
creation wizard the whole time and enforced nowhere: it changed how the
tournament was LISTED and not who could register, so an organiser who chose it
believed they had closed a door that was never shut.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users, UserWallet

from .models import Tournament, TournamentInvite, TournamentRegistration


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('a-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('aw%s' % name)[:10], user=user, wallet_balance=0)
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class AccessTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('ac_org')
        self.player, self.player_auth = a_user('ac_player')
        self.stranger, self.stranger_auth = a_user('ac_other')
        game = Games.objects.create(game_title='EA FC AC')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Access Probe', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=7),
            bracket_type='single_elimination', is_draft=False,
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0,
            min_number_of_teams=2, max_number_of_teams=32,
        )

    def register(self, auth=None, **extra):
        body = {'tournament_id': self.tournament.tournament_id}
        body.update(extra)
        return self.client.post('/tournament/register-tournament/', data=body,
                                content_type='application/json',
                                **(auth or self.player_auth))

    def invites_url(self):
        return '/tournament/%s/invites/' % self.tournament.tournament_id

    def regs_url(self):
        return '/tournament/%s/registrations/' % self.tournament.tournament_id

    # ------------------------------------------------------ protected

    def test_public_lets_anybody_register(self):
        self.assertIn(self.register().status_code, (200, 201))

    def test_protected_refuses_without_a_code(self):
        # The setting existed and enforced nothing. Anybody who found the page
        # could register into a tournament the organiser had marked closed.
        self.tournament.tournament_visibility = 'protected'
        self.tournament.save()
        res = self.register()
        self.assertEqual(res.status_code, 403, res.json())
        self.assertEqual(res.json()['code'], 'INVITE_REQUIRED')
        self.assertFalse(TournamentRegistration.objects.filter(
            tournament=self.tournament).exists())

    def test_protected_lets_a_code_holder_in(self):
        self.tournament.tournament_visibility = 'protected'
        self.tournament.save()
        TournamentInvite.objects.create(tournament=self.tournament, code='LAGOS1')
        self.assertIn(self.register(invite_code='LAGOS1').status_code, (200, 201))

    def test_the_code_is_not_case_sensitive(self):
        self.tournament.tournament_visibility = 'protected'
        self.tournament.save()
        TournamentInvite.objects.create(tournament=self.tournament, code='LAGOS1')
        self.assertIn(self.register(invite_code=' lagos1 ').status_code, (200, 201))

    def test_a_spent_code_does_not_work_twice(self):
        self.tournament.tournament_visibility = 'protected'
        self.tournament.save()
        TournamentInvite.objects.create(tournament=self.tournament, code='ONCE')
        self.assertIn(self.register(invite_code='ONCE').status_code, (200, 201))

        second, second_auth = a_user('ac_second')
        res = self.register(auth=second_auth, invite_code='ONCE')
        self.assertEqual(res.status_code, 403)

    def test_a_code_can_be_shared_by_a_group(self):
        self.tournament.tournament_visibility = 'protected'
        self.tournament.save()
        TournamentInvite.objects.create(
            tournament=self.tournament, code='SQUAD', max_uses=3)
        self.assertIn(self.register(invite_code='SQUAD').status_code, (200, 201))
        _u, other_auth = a_user('ac_mate')
        self.assertIn(self.register(auth=other_auth, invite_code='SQUAD').status_code,
                      (200, 201))

    def test_a_code_from_another_tournament_does_not_open_this_one(self):
        game = Games.objects.create(game_title='Other AC')
        now = timezone.now()
        other = Tournament.objects.create(
            tournament_title='Elsewhere', tournament_game=game,
            tournament_creator=self.stranger,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=7),
            bracket_type='single_elimination', is_draft=False,
            entry_fee='Free', entry_fee_price=0)
        TournamentInvite.objects.create(tournament=other, code='THEIRS')

        self.tournament.tournament_visibility = 'protected'
        self.tournament.save()
        self.assertEqual(self.register(invite_code='THEIRS').status_code, 403)

    # ---------------------------------------------------- approval queue

    def test_without_approval_a_registration_is_confirmed(self):
        self.register()
        reg = TournamentRegistration.objects.get(tournament=self.tournament)
        self.assertEqual(reg.status, 'confirmed')

    def test_with_approval_it_waits(self):
        self.tournament.approve_registrations = True
        self.tournament.save()
        self.register()
        reg = TournamentRegistration.objects.get(tournament=self.tournament)
        self.assertEqual(reg.status, 'pending')

    def test_the_organiser_accepts(self):
        self.tournament.approve_registrations = True
        self.tournament.save()
        self.register()
        reg = TournamentRegistration.objects.get(tournament=self.tournament)

        res = self.client.post(self.regs_url(), data={
            'decision': 'accept', 'registration_ids': [reg.pk]},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        reg.refresh_from_db()
        self.assertEqual(reg.status, 'confirmed')

    def test_the_organiser_declines(self):
        self.tournament.approve_registrations = True
        self.tournament.save()
        self.register()
        reg = TournamentRegistration.objects.get(tournament=self.tournament)
        self.client.post(self.regs_url(), data={
            'decision': 'decline', 'registration_ids': [reg.pk]},
            content_type='application/json', **self.auth)
        reg.refresh_from_db()
        self.assertEqual(reg.status, 'withdrawn')

    def test_a_stranger_decides_nothing(self):
        self.tournament.approve_registrations = True
        self.tournament.save()
        self.register()
        reg = TournamentRegistration.objects.get(tournament=self.tournament)
        res = self.client.post(self.regs_url(), data={
            'decision': 'accept', 'registration_ids': [reg.pk]},
            content_type='application/json', **self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        reg.refresh_from_db()
        self.assertEqual(reg.status, 'pending')

    def test_the_queue_says_how_many_are_waiting(self):
        self.tournament.approve_registrations = True
        self.tournament.save()
        self.register()
        body = self.client.get(self.regs_url(), **self.auth).json()['data']
        self.assertTrue(body['approval_required'])
        self.assertEqual(body['pending'], 1)
        self.assertEqual(body['registrations'][0]['name'], 'ac_player')

    # ---------------------------------------------------------- codes

    def test_the_organiser_mints_a_batch(self):
        res = self.client.post(self.invites_url(), data={'count': 5},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 201, res.json())
        self.assertEqual(res.json()['data']['made'], 5)
        self.assertEqual(TournamentInvite.objects.filter(
            tournament=self.tournament).count(), 5)

    def test_the_organiser_supplies_their_own(self):
        res = self.client.post(self.invites_url(),
                               data={'codes': 'ALPHA\nBRAVO, CHARLIE'},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 201, res.json())
        codes = set(TournamentInvite.objects.filter(tournament=self.tournament)
                    .values_list('code', flat=True))
        self.assertEqual(codes, {'ALPHA', 'BRAVO', 'CHARLIE'})

    def test_the_free_limit_is_sixty_four(self):
        self.client.post(self.invites_url(), data={'count': 64},
                         content_type='application/json', **self.auth)
        res = self.client.post(self.invites_url(), data={'count': 1},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'CODE_LIMIT')

    def test_generated_codes_avoid_the_letters_people_mistype(self):
        self.client.post(self.invites_url(), data={'count': 20},
                         content_type='application/json', **self.auth)
        for code in TournamentInvite.objects.filter(
                tournament=self.tournament).values_list('code', flat=True):
            self.assertFalse(set(code) & set('IO01'), code)

    def test_they_download_as_text(self):
        self.client.post(self.invites_url(), data={'count': 3},
                         content_type='application/json', **self.auth)
        res = self.client.get(self.invites_url().replace('/invites/', '/invites/download/'),
                              **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertIn('attachment', res['Content-Disposition'])

    def test_they_download_as_csv(self):
        self.client.post(self.invites_url(), data={'count': 2},
                         content_type='application/json', **self.auth)
        res = self.client.get(
            self.invites_url().replace('/invites/', '/invites/download/'),
            {'as': 'csv'}, **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertIn('csv', res['Content-Disposition'])

    def test_clearing_leaves_the_used_ones_alone(self):
        # A code somebody registered with is part of the record of how they got in.
        self.tournament.tournament_visibility = 'protected'
        self.tournament.save()
        TournamentInvite.objects.create(tournament=self.tournament, code='USED')
        TournamentInvite.objects.create(tournament=self.tournament, code='FRESH')
        self.register(invite_code='USED')

        self.client.delete(self.invites_url(), **self.auth)
        left = set(TournamentInvite.objects.filter(tournament=self.tournament)
                   .values_list('code', flat=True))
        self.assertEqual(left, {'USED'})

    def test_a_stranger_cannot_see_the_codes(self):
        res = self.client.get(self.invites_url(), **self.stranger_auth)
        self.assertEqual(res.status_code, 403)
