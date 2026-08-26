"""The organiser settings, and whether they actually bite.

An option that stores a value but changes nothing is worse than no option: it
tells an organiser a rule is in force when it is not. So each test here proves
a consequence, not a saved field. Registration is refused, a no-show is
forfeited, a bronze match decides third rather than the loser of a semi.
"""
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from vent_auth.models import Games, UserProfile, Users, UserWallet
from .models import Tournament, TournamentRegistration
from . import options as opts


def _token(user, token):
    user.login_session_token = token
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_token', 'login_session_created_at'])
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


class CleanTests(TestCase):
    """clean() is the only thing between the wizard and the database."""

    def test_every_key_is_present_even_from_nothing(self):
        cleaned = opts.clean(None)
        self.assertEqual(set(cleaned), set(opts.DEFAULTS))

    def test_unknown_keys_are_dropped(self):
        cleaned = opts.clean({'drop_tables': True, 'check_in_minutes': 30})
        self.assertNotIn('drop_tables', cleaned)
        self.assertEqual(cleaned['check_in_minutes'], 30)

    def test_numbers_are_clamped_not_trusted(self):
        cleaned = opts.clean({'check_in_minutes': 99999, 'min_age': -5, 'best_of': 400})
        self.assertEqual(cleaned['check_in_minutes'], 240)
        self.assertEqual(cleaned['min_age'], 0)
        self.assertEqual(cleaned['best_of'], 9)

    def test_rubbish_in_a_number_leaves_the_default(self):
        self.assertEqual(opts.clean({'group_size': 'four'})['group_size'], opts.DEFAULTS['group_size'])

    def test_an_invented_seeding_method_falls_back(self):
        self.assertEqual(opts.clean({'seeding_method': 'by_vibes'})['seeding_method'], 'registration')

    def test_seeding_words_match_the_bracket_service(self):
        from .services.bracket import seed_registrations  # noqa: F401  (import proves it exists)
        for word in ('registration', 'random', 'ranked', 'manual_order'):
            self.assertEqual(opts.clean({'seeding_method': word})['seeding_method'], word)

    def test_a_group_cannot_advance_more_than_it_holds(self):
        cleaned = opts.clean({'group_stage': True, 'group_size': 4, 'advance_per_group': 8})
        self.assertEqual(cleaned['advance_per_group'], 4)

    def test_an_escalating_bracket_cannot_shrink(self):
        cleaned = opts.clean({'best_of_mode': 'escalating', 'best_of': 5, 'best_of_final': 1})
        self.assertEqual(cleaned['best_of_final'], 5)


class BestOfTests(TestCase):
    def setUp(self):
        self.game = Games.objects.create(game_title='Bo Game')
        self.t = Tournament.objects.create(
            tournament_title='Bo test', tournament_game=self.game,
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            tournament_type='online', tournament_access='individual',
            entry_fee='Free', is_draft=False,
        )

    def test_fixed_is_the_same_every_round(self):
        self.t.options = opts.clean({'best_of_mode': 'fixed', 'best_of': 3})
        self.assertEqual(opts.best_of_for_round(self.t, 1, 4), 3)
        self.assertEqual(opts.best_of_for_round(self.t, 4, 4), 3)

    def test_escalating_ends_on_the_final_length(self):
        self.t.options = opts.clean({'best_of_mode': 'escalating', 'best_of': 1, 'best_of_final': 5})
        self.assertEqual(opts.best_of_for_round(self.t, 1, 4), 1)
        self.assertEqual(opts.best_of_for_round(self.t, 4, 4), 5)

    def test_every_round_length_is_odd_so_a_series_can_end(self):
        self.t.options = opts.clean({'best_of_mode': 'escalating', 'best_of': 1, 'best_of_final': 7})
        for r in range(1, 6):
            self.assertEqual(opts.best_of_for_round(self.t, r, 5) % 2, 1)


class EntryRestrictionTests(TestCase):
    """Refusals happen before money moves, or they are worth nothing."""

    def setUp(self):
        self.game = Games.objects.create(game_title='Gate Game')
        self.organiser = Users.objects.create(username='gate_org', email='org@example.com')
        self.t = Tournament.objects.create(
            tournament_title='Gated', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            tournament_type='online', tournament_access='individual',
            entry_fee='Free', is_draft=False, status='published',
        )
        self.player = Users.objects.create(
            username='gate_player', email='player@example.com', country='Nigeria', is_active=True,
        )
        UserWallet.objects.create(user_wallet_id='GW1', user=self.player, wallet_balance=0)

    def test_an_open_tournament_refuses_nobody(self):
        self.t.options = opts.clean({})
        self.assertIsNone(opts.entry_refusal(self.t, self.player))

    def test_an_unverified_account_is_refused(self):
        self.player.is_active = False
        self.t.options = opts.clean({'require_verified_email': True})
        self.assertIn('verified email', opts.entry_refusal(self.t, self.player))

    def test_the_wrong_country_is_refused_by_name(self):
        self.t.options = opts.clean({'restrict_country': 'Ghana'})
        refusal = opts.entry_refusal(self.t, self.player)
        self.assertIn('Ghana', refusal)

    def test_the_right_country_passes_whatever_the_casing(self):
        self.player.country = 'nigeria'
        self.t.options = opts.clean({'restrict_country': 'Nigeria'})
        self.assertIsNone(opts.entry_refusal(self.t, self.player))

    def test_an_age_limit_without_a_birthday_asks_for_one(self):
        self.t.options = opts.clean({'min_age': 18})
        self.assertIn('date of birth', opts.entry_refusal(self.t, self.player))

    def test_too_young_is_refused(self):
        UserProfile.objects.create(
            user=self.player, date_of_birth=date.today() - timedelta(days=365 * 14),
        )
        self.t.options = opts.clean({'min_age': 18})
        self.assertIn('18', opts.entry_refusal(self.t, self.player))

    def test_old_enough_passes(self):
        UserProfile.objects.create(
            user=self.player, date_of_birth=date.today() - timedelta(days=365 * 30),
        )
        self.t.options = opts.clean({'min_age': 18})
        self.assertIsNone(opts.entry_refusal(self.t, self.player))

    def test_identity_requirement_reads_the_wallet(self):
        self.t.options = opts.clean({'require_kyc': True})
        self.assertIn('verified identity', opts.entry_refusal(self.t, self.player))
        wallet = self.player.wallet
        wallet.kyc_verified = True
        wallet.save(update_fields=['kyc_verified'])
        self.player.refresh_from_db()
        self.assertIsNone(opts.entry_refusal(self.t, self.player))


class JoinEnforcementTests(TestCase):
    """The refusal has to arrive over HTTP, not only from the helper."""

    def setUp(self):
        self.game = Games.objects.create(game_title='Join Game')
        self.organiser = Users.objects.create(username='join_org', email='jo@example.com')
        self.t = Tournament.objects.create(
            tournament_title='Join test', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            tournament_type='online', tournament_access='individual',
            entry_fee='Free', is_draft=False, status='published',
            max_number_of_teams=2,
            options=opts.clean({'restrict_country': 'Ghana'}),
        )
        self.player = Users.objects.create(
            username='join_player', email='jp@example.com', country='Nigeria', is_active=True,
        )
        self.headers = _token(self.player, 'jointoken1234567')

    def test_the_wrong_country_gets_403_not_a_registration(self):
        res = self.client.post(
            reverse('join_tournament'),
            {'tournament_id': self.t.tournament_id},
            content_type='application/json', **self.headers,
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'NOT_ELIGIBLE')
        self.assertFalse(TournamentRegistration.objects.filter(tournament=self.t).exists())

    def test_a_full_tournament_refuses_the_next_person(self):
        self.t.options = opts.clean({})
        self.t.save(update_fields=['options'])
        for i in range(2):
            other = Users.objects.create(username=f'filler{i}', email=f'f{i}@example.com')
            TournamentRegistration.objects.create(tournament=self.t, user=other, status='confirmed')

        res = self.client.post(
            reverse('join_tournament'),
            {'tournament_id': self.t.tournament_id},
            content_type='application/json', **self.headers,
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'TOURNAMENT_FULL')
        self.assertEqual(TournamentRegistration.objects.filter(tournament=self.t).count(), 2)

    def test_an_eligible_player_still_gets_in(self):
        self.t.options = opts.clean({'restrict_country': 'Nigeria'})
        self.t.save(update_fields=['options'])
        res = self.client.post(
            reverse('join_tournament'),
            {'tournament_id': self.t.tournament_id},
            content_type='application/json', **self.headers,
        )
        self.assertIn(res.status_code, (200, 201), res.content)
        self.assertTrue(
            TournamentRegistration.objects.filter(tournament=self.t, user=self.player).exists()
        )


class CheckInTests(TestCase):
    def setUp(self):
        self.game = Games.objects.create(game_title='Check Game')
        self.organiser = Users.objects.create(username='ci_org', email='cio@example.com')
        self.t = Tournament.objects.create(
            tournament_title='Check in test', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=timezone.now() + timedelta(minutes=5),
            end_date_and_time=timezone.now() + timedelta(hours=3),
            tournament_type='online', tournament_access='individual',
            entry_fee='Free', is_draft=False, status='published',
            options=opts.clean({'check_in_minutes': 15, 'forfeit_without_check_in': True}),
        )
        self.players, self.regs, self.heads = [], [], []
        for i in range(4):
            u = Users.objects.create(username=f'ci_p{i}', email=f'cip{i}@example.com', is_active=True)
            self.players.append(u)
            self.regs.append(
                TournamentRegistration.objects.create(tournament=self.t, user=u, status='confirmed')
            )
            self.heads.append(_token(u, f'citoken{i}0000000'[:16]))
        self.org_head = _token(self.organiser, 'ciorgtoken123456')

    def url(self, name):
        return reverse(name, args=[self.t.tournament_id])

    def test_status_says_open_inside_the_window(self):
        res = self.client.get(self.url('check_in_status'), **self.heads[0])
        body = res.json()['data']
        self.assertTrue(body['required'])
        self.assertTrue(body['open_now'])
        self.assertFalse(body['checked_in'])

    def test_a_player_can_check_in_and_it_sticks(self):
        res = self.client.post(self.url('check_in'), **self.heads[0])
        self.assertEqual(res.status_code, 200, res.content)
        self.regs[0].refresh_from_db()
        self.assertIsNotNone(self.regs[0].checked_in_at)

    def test_checking_in_twice_is_not_an_error(self):
        self.client.post(self.url('check_in'), **self.heads[0])
        res = self.client.post(self.url('check_in'), **self.heads[0])
        self.assertEqual(res.status_code, 200)
        self.assertIn('already', res.json()['message'].lower())

    def test_somebody_not_registered_cannot_check_in(self):
        stranger = Users.objects.create(username='ci_stranger', email='cis@example.com')
        head = _token(stranger, 'cistranger123456')
        res = self.client.post(self.url('check_in'), **head)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'NOT_REGISTERED')

    def test_too_early_is_refused_with_the_opening_time(self):
        self.t.start_date_and_time = timezone.now() + timedelta(hours=5)
        self.t.save(update_fields=['start_date_and_time'])
        res = self.client.post(self.url('check_in'), **self.heads[0])
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'TOO_EARLY')

    def test_after_the_start_it_is_too_late(self):
        self.t.start_date_and_time = timezone.now() - timedelta(minutes=1)
        self.t.save(update_fields=['start_date_and_time'])
        res = self.client.post(self.url('check_in'), **self.heads[0])
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'TOO_LATE')

    def test_closing_forfeits_only_the_no_shows(self):
        for head in self.heads[:3]:
            self.client.post(self.url('check_in'), **head)

        res = self.client.post(self.url('close_check_in'), **self.org_head)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()['data']
        self.assertEqual(len(body['checked_in']), 3)
        self.assertEqual(len(body['forfeited']), 1)

        for reg in self.regs[:3]:
            reg.refresh_from_db()
            self.assertEqual(reg.status, 'confirmed')
        self.regs[3].refresh_from_db()
        self.assertEqual(self.regs[3].status, 'disqualified')
        self.assertEqual(self.regs[3].forfeited_reason, 'Did not check in')

    def test_closing_never_empties_the_tournament(self):
        self.client.post(self.url('check_in'), **self.heads[0])
        res = self.client.post(self.url('close_check_in'), **self.org_head)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'TOO_FEW_CHECKED_IN')
        for reg in self.regs:
            reg.refresh_from_db()
            self.assertEqual(reg.status, 'confirmed')

    def test_closing_shuts_the_window_even_though_the_clock_says_otherwise(self):
        """The bug this covers: the window is derived from the start time, so
        without a recorded close an entrant could check in after the organiser
        had already forfeited the no-shows and signed off the roster."""
        for head in self.heads[:3]:
            self.client.post(self.url('check_in'), **head)
        self.client.post(self.url('close_check_in'), **self.org_head)

        self.t.refresh_from_db()
        self.assertIsNotNone(self.t.check_in_closed_at)

        # The start time has not arrived, so by the clock alone this is open.
        self.assertGreater(self.t.start_date_and_time, timezone.now())

        status_res = self.client.get(self.url('check_in_status'), **self.heads[0])
        body = status_res.json()['data']
        self.assertFalse(body['open_now'])
        self.assertTrue(body['closed'])
        self.assertTrue(body['closed_by_organiser'])

    def test_a_late_entrant_cannot_check_in_after_the_close(self):
        for head in self.heads[:3]:
            self.client.post(self.url('check_in'), **head)
        self.client.post(self.url('close_check_in'), **self.org_head)

        # player4 was forfeited, so use a fresh entrant who was never removed.
        latecomer = Users.objects.create(
            username='ci_late', email='cilate@example.com', is_active=True,
        )
        TournamentRegistration.objects.create(
            tournament=self.t, user=latecomer, status='confirmed',
        )
        head = _token(latecomer, 'cilatetoken12345')

        res = self.client.post(self.url('check_in'), **head)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'TOO_LATE')

    def test_check_in_cannot_be_closed_twice(self):
        for head in self.heads[:3]:
            self.client.post(self.url('check_in'), **head)
        self.client.post(self.url('close_check_in'), **self.org_head)

        res = self.client.post(self.url('close_check_in'), **self.org_head)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'ALREADY_CLOSED')

    def test_extending_cannot_reopen_a_closed_window(self):
        for head in self.heads[:3]:
            self.client.post(self.url('check_in'), **head)
        self.client.post(self.url('close_check_in'), **self.org_head)

        res = self.client.post(
            self.url('extend_check_in'), {'minutes': 30},
            content_type='application/json', **self.org_head,
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'ALREADY_CLOSED')

    def test_only_the_organiser_can_close(self):
        res = self.client.post(self.url('close_check_in'), **self.heads[0])
        self.assertEqual(res.status_code, 403)

    def test_extending_moves_the_window_with_the_start(self):
        before = self.t.start_date_and_time
        res = self.client.post(
            self.url('extend_check_in'), {'minutes': 30},
            content_type='application/json', **self.org_head,
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.t.refresh_from_db()
        self.assertEqual(
            int((self.t.start_date_and_time - before).total_seconds() // 60), 30,
        )

    def test_a_tournament_without_check_in_says_so(self):
        self.t.options = opts.clean({'check_in_minutes': 0})
        self.t.save(update_fields=['options'])
        res = self.client.get(self.url('check_in_status'), **self.heads[0])
        self.assertFalse(res.json()['data']['required'])


class ThirdPlaceTests(TestCase):
    """A prize table with a third position needs a match that decides third."""

    def setUp(self):
        self.game = Games.objects.create(game_title='Bronze Game')
        self.organiser = Users.objects.create(username='tp_org', email='tpo@example.com')
        self.t = Tournament.objects.create(
            tournament_title='Bronze test', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            tournament_type='online', tournament_access='individual',
            entry_fee='Free', is_draft=False, status='published',
            bracket_type='single_elimination',
            options=opts.clean({'third_place_match': True}),
        )
        self.regs = []
        for i in range(4):
            u = Users.objects.create(username=f'tp_p{i}', email=f'tpp{i}@example.com')
            self.regs.append(
                TournamentRegistration.objects.create(tournament=self.t, user=u, status='confirmed')
            )

    def test_a_bronze_match_is_created_and_wired_to_both_semis(self):
        from .services import bracket as bracket_service
        from .models import BracketMatch

        summary = bracket_service.generate(self.t, self.organiser, seed_strategy='registration')
        self.assertIsNotNone(summary['third_place_match_id'])

        bronze = BracketMatch.objects.get(pk=summary['third_place_match_id'])
        semis = BracketMatch.objects.filter(tournament=self.t, round_number=1)
        self.assertEqual(semis.count(), 2)
        for semi in semis:
            self.assertEqual(semi.loser_to_match_id, bronze.id)
        self.assertEqual({s.loser_to_slot for s in semis}, {1, 2})

    def test_without_the_option_no_bronze_match_exists(self):
        from .services import bracket as bracket_service

        self.t.options = opts.clean({'third_place_match': False})
        self.t.save(update_fields=['options'])
        summary = bracket_service.generate(self.t, self.organiser, seed_strategy='registration')
        self.assertIsNone(summary['third_place_match_id'])

    def test_the_bronze_winner_places_third_not_the_loser(self):
        from .services import bracket as bracket_service
        from .services import advance
        from .models import BracketMatch

        bracket_service.generate(self.t, self.organiser, seed_strategy='registration')

        semis = list(BracketMatch.objects.filter(tournament=self.t, round_number=1))
        for semi in semis:
            semi.winner = semi.participant_1
            semi.score_p1, semi.score_p2 = 1, 0
            semi.status = 'completed'
            semi.completed_at = timezone.now()
            semi.save()

        final = BracketMatch.objects.get(tournament=self.t, is_final=True)
        final.refresh_from_db()
        final.winner = final.participant_1
        final.score_p1, final.score_p2 = 1, 0
        final.status = 'completed'
        final.completed_at = timezone.now()
        final.save()

        bronze = BracketMatch.objects.get(
            tournament=self.t, round_number=2, match_number=2,
        )
        bronze.refresh_from_db()
        self.assertIsNotNone(bronze.participant_1)
        self.assertIsNotNone(bronze.participant_2)
        bronze_winner = bronze.participant_2      # deliberately the second slot
        bronze.winner = bronze_winner
        bronze.score_p1, bronze.score_p2 = 0, 1
        bronze.status = 'completed'
        bronze.completed_at = timezone.now()
        bronze.save()

        self.t.refresh_from_db()
        advance.assign_final_positions(self.t)

        bronze_winner.refresh_from_db()
        self.assertEqual(bronze_winner.final_position, 3)
        bronze.participant_1.refresh_from_db()
        self.assertEqual(bronze.participant_1.final_position, 4)


class SeedingWiringTests(TestCase):
    """The method chosen at creation is the method the draw uses."""

    def setUp(self):
        self.game = Games.objects.create(game_title='Seed Game')
        self.organiser = Users.objects.create(username='sd_org', email='sdo@example.com')
        self.t = Tournament.objects.create(
            tournament_title='Seed test', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            tournament_type='online', tournament_access='individual',
            entry_fee='Free', is_draft=False, status='published',
            bracket_type='single_elimination',
            options=opts.clean({'seeding_method': 'registration', 'check_in_minutes': 0}),
        )
        self.regs = []
        for i in range(4):
            u = Users.objects.create(username=f'sd_p{i}', email=f'sdp{i}@example.com')
            self.regs.append(
                TournamentRegistration.objects.create(tournament=self.t, user=u, status='confirmed')
            )
        self.org_head = _token(self.organiser, 'sdorgtoken123456')

    def test_registration_order_seeds_first_come_first_seeded(self):
        res = self.client.post(
            reverse('generate_bracket', args=[self.t.tournament_id]),
            {}, content_type='application/json', **self.org_head,
        )
        self.assertIn(res.status_code, (200, 201), res.content)
        seeds = {r.id: r.seed for r in TournamentRegistration.objects.filter(tournament=self.t)}
        self.assertEqual([seeds[r.id] for r in self.regs], [1, 2, 3, 4])

    def test_the_generation_record_keeps_the_method_used(self):
        from .models import BracketGeneration

        self.client.post(
            reverse('generate_bracket', args=[self.t.tournament_id]),
            {}, content_type='application/json', **self.org_head,
        )
        gen = BracketGeneration.objects.get(tournament=self.t)
        self.assertEqual(gen.seed_strategy, 'registration')

    def test_a_bracket_is_refused_while_no_shows_are_still_in(self):
        self.t.options = opts.clean({'check_in_minutes': 15, 'forfeit_without_check_in': True})
        self.t.start_date_and_time = timezone.now() - timedelta(minutes=1)
        self.t.save(update_fields=['options', 'start_date_and_time'])

        res = self.client.post(
            reverse('generate_bracket', args=[self.t.tournament_id]),
            {}, content_type='application/json', **self.org_head,
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'CHECK_IN_OPEN')

    def test_the_organiser_can_seed_them_anyway(self):
        self.t.options = opts.clean({'check_in_minutes': 15, 'forfeit_without_check_in': True})
        self.t.start_date_and_time = timezone.now() - timedelta(minutes=1)
        self.t.save(update_fields=['options', 'start_date_and_time'])

        res = self.client.post(
            reverse('generate_bracket', args=[self.t.tournament_id]),
            {'ignore_check_in': True}, content_type='application/json', **self.org_head,
        )
        self.assertIn(res.status_code, (200, 201), res.content)


class OptionsRoundTripTests(TestCase):
    """What the wizard sends has to survive the trip and come back readable."""

    def setUp(self):
        self.game = Games.objects.create(game_title='Trip Game')
        self.organiser = Users.objects.create(username='rt_org', email='rto@example.com')
        self.head = _token(self.organiser, 'rtorgtoken123456')

    def test_options_arrive_as_json_and_come_back_on_the_detail(self):
        import json

        res = self.client.post(reverse('create_tournament'), {
            'tournament_title': 'Round trip',
            'game': 'Trip Game',
            'tournament_type': 'online',
            'tournament_access': 'individual',
            'tournament_visibility': 'public',
            'entry_type': 'Free',
            'start_date_and_time': (timezone.now() + timedelta(days=1)).isoformat(),
            'end_date_and_time': (timezone.now() + timedelta(days=2)).isoformat(),
            'is_draft': 'false',
            'options': json.dumps({
                'check_in_minutes': 30,
                'third_place_match': True,
                'seeding_method': 'ranked',
                'restrict_country': 'Nigeria',
                'min_age': 16,
            }),
        }, **self.head)
        self.assertIn(res.status_code, (200, 201), res.content)

        t = Tournament.objects.get(tournament_title='Round trip')
        self.assertEqual(t.options['check_in_minutes'], 30)
        self.assertTrue(t.options['third_place_match'])
        self.assertEqual(t.options['seeding_method'], 'ranked')

        detail = self.client.get(reverse('view_tournament', args=[str(t.tournament_id)]))
        body = detail.json()['data']
        self.assertEqual(body['options']['restrict_country'], 'Nigeria')
        self.assertEqual(body['options']['min_age'], 16)
        self.assertTrue(body['check_in']['required'])

    def test_editing_one_option_does_not_wipe_the_others(self):
        import json

        t = Tournament.objects.create(
            tournament_title='Partial edit', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            tournament_type='online', tournament_access='individual',
            entry_fee='Free', is_draft=False,
            options=opts.clean({'restrict_country': 'Nigeria', 'min_age': 18}),
        )

        res = self.client.put(
            reverse('edit_tournament', args=[t.tournament_id]),
            json.dumps({'options': {'check_in_minutes': 60}}),
            content_type='application/json', **self.head,
        )
        self.assertEqual(res.status_code, 200, res.content)

        t.refresh_from_db()
        self.assertEqual(t.options['check_in_minutes'], 60)
        self.assertEqual(t.options['restrict_country'], 'Nigeria')
        self.assertEqual(t.options['min_age'], 18)


class OneShapeTests(TestCase):
    """One model per thing means one shape per thing.

    The fault this guards against: a field is added for one audience, so the
    card and the page it links to quietly describe different tournaments, and
    nobody notices until somebody asks why their tournament looks different from
    everybody else's.
    """

    def setUp(self):
        self.game = Games.objects.create(game_title='Shape Game')
        self.organiser = Users.objects.create(username='shape_org', email='so@example.com')
        self.t = Tournament.objects.create(
            tournament_title='Shape test', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            tournament_type='online', tournament_access='individual',
            entry_fee='Free', is_draft=False, status='published',
            options=opts.clean({'check_in_minutes': 30, 'restrict_country': 'Nigeria'}),
        )

    def test_the_card_and_the_detail_agree_on_the_settings(self):
        from vent_tournament.views import serialize_tournament_card

        card = serialize_tournament_card(self.t)
        detail = self.client.get(
            reverse('view_tournament', args=[str(self.t.tournament_id)])
        ).json()['data']

        self.assertEqual(card['options'], detail['options'])
        self.assertEqual(card['options']['check_in_minutes'], 30)
        self.assertEqual(card['options']['restrict_country'], 'Nigeria')

    def test_the_card_carries_the_check_in_window_too(self):
        from vent_tournament.views import serialize_tournament_card

        card = serialize_tournament_card(self.t)
        self.assertIsNotNone(card['check_in'])
        self.assertTrue(card['check_in']['required'])

    def test_the_list_endpoint_carries_them_as_well(self):
        """Whatever surface a tournament appears on, it is the same tournament."""
        res = self.client.get(reverse('get_all_tournaments'))
        data = res.json()['data']
        found = None
        for value in data.values():
            if isinstance(value, list):
                for item in value:
                    if item.get('tournament_id') == self.t.tournament_id:
                        found = item
        self.assertIsNotNone(found, 'the tournament should be in the list')
        self.assertIn('options', found)
        self.assertEqual(found['options']['check_in_minutes'], 30)

    def test_every_surface_uses_the_same_key_for_the_address(self):
        """id, tournament_id and slug mean the same thing everywhere, or links
        built on one surface break on another."""
        from vent_tournament.views import serialize_tournament_card

        card = serialize_tournament_card(self.t)
        detail = self.client.get(
            reverse('view_tournament', args=[str(self.t.tournament_id)])
        ).json()['data']

        for key in ('id', 'tournament_id', 'slug', 'name', 'title', 'status'):
            self.assertIn(key, card, f'card is missing {key}')
            self.assertIn(key, detail, f'detail is missing {key}')
            self.assertEqual(card[key], detail[key], f'{key} differs between card and detail')
