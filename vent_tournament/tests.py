"""Tests for the M1 tournament lifecycle: bracket generation, participant score
report/confirm with auto-advance, disputes, prize distribution, and organizer
cancel. Run with the in-memory SQLite settings (MySQL is unreachable locally):

    python manage.py test vent_tournament --settings=vent_tournament.test_settings
"""
import json
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from django.db import transaction
from rest_framework.test import APIClient

from django.contrib.contenttypes.models import ContentType

from vent_auth.models import Users, UserWallet, Transaction, Games
from .models import (
    Tournament, TournamentRegistration, BracketMatch, TournamentDispute,
    TournamentPrizeDistribution, PrizePayout, Sponsors,
)
from .services import bracket as bracket_service


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_user(i, *, kyc=False, staff=False):
    user = Users.objects.create(
        username=f'player{i}', email=f'player{i}@test.co',
        login_session_token=f'tok{i:012d}'[:16],
        login_session_created_at=timezone.now(),
        is_active=True, is_staff=staff,
    )
    UserWallet.objects.create(
        user_wallet_id=uuid.uuid4().hex[:10], user=user,
        wallet_balance=0, kyc_verified=kyc,
    )
    return user


def make_tournament(creator, *, bracket_type='single_elimination', entry_fee='Free',
                    entry_fee_price=0, prize_type='no_prize', status='registration_open',
                    score_mode='both_players_confirm'):
    now = timezone.now()
    return Tournament.objects.create(
        tournament_title=f'Test Cup {uuid.uuid4().hex[:6]}',
        tournament_creator=creator,
        tournament_type='online',
        tournament_access='individual',
        tournament_visibility='public',
        entry_fee=entry_fee,
        entry_fee_price=entry_fee_price,
        prize_type=prize_type,
        bracket_type=bracket_type,
        score_confirmation_mode=score_mode,
        start_date_and_time=now + timedelta(days=1),
        end_date_and_time=now + timedelta(days=2),
        is_draft=False,
        status=status,
    )


def register(tournament, user, *, paid=False):
    return TournamentRegistration.objects.create(
        tournament=tournament, user=user, status='confirmed',
        entry_fee_paid=paid,
    )


def client_for(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f'Bearer {user.login_session_token}')
    return c


def gen_bracket(tournament, creator, strategy='random'):
    with transaction.atomic():
        return bracket_service.generate(tournament, generated_by=creator, seed_strategy=strategy)


# ---------------------------------------------------------------------------
# Bracket generation
# ---------------------------------------------------------------------------

class SingleElimGenerationTests(TestCase):
    def test_four_players_makes_three_matches_two_rounds(self):
        org = make_user(0)
        t = make_tournament(org)
        for i in range(1, 5):
            register(t, make_user(i))

        summary = gen_bracket(t, org)

        self.assertEqual(summary['bracket_type'], 'single_elimination')
        self.assertEqual(summary['rounds_count'], 2)
        self.assertEqual(summary['matches_created'], 3)
        self.assertEqual(t.bracket_matches.filter(round_number=1).count(), 2)
        self.assertEqual(t.bracket_matches.filter(round_number=2).count(), 1)
        t.refresh_from_db()
        self.assertEqual(t.status, 'live')

    def test_three_players_creates_bye_that_advances_immediately(self):
        org = make_user(0)
        t = make_tournament(org)
        for i in range(1, 4):
            register(t, make_user(i))

        gen_bracket(t, org)

        # bracket_size padded to 4 -> exactly one round-1 bye.
        r1 = t.bracket_matches.filter(round_number=1)
        self.assertEqual(r1.count(), 2)
        byes = r1.filter(status='bye')
        self.assertEqual(byes.count(), 1)
        # The bye winner must already occupy a slot in the round-2 (final) match.
        final = t.bracket_matches.get(round_number=2)
        occupied = [p for p in (final.participant_1_id, final.participant_2_id) if p]
        self.assertEqual(len(occupied), 1)
        self.assertEqual(occupied[0], byes.first().winner_id)

    def test_generate_refuses_second_time(self):
        org = make_user(0)
        t = make_tournament(org)
        for i in range(1, 5):
            register(t, make_user(i))
        gen_bracket(t, org)
        with self.assertRaises(bracket_service.BracketError) as ctx:
            gen_bracket(t, org)
        self.assertEqual(ctx.exception.code, 'bracket_already_generated')

    def test_generate_refuses_below_minimum(self):
        org = make_user(0)
        t = make_tournament(org)
        register(t, make_user(1))  # only 1 participant
        with self.assertRaises(bracket_service.BracketError) as ctx:
            gen_bracket(t, org)
        self.assertEqual(ctx.exception.code, 'not_enough_participants')


class DoubleElimGenerationTests(TestCase):
    def test_four_players_structure(self):
        org = make_user(0)
        t = make_tournament(org, bracket_type='double_elimination')
        for i in range(1, 5):
            register(t, make_user(i))

        summary = gen_bracket(t, org)

        self.assertEqual(summary['bracket_type'], 'double_elimination')
        # WB(2+1) + LB(1+1) + single GF(1) = 6 matches (bracket reset deferred to M2).
        self.assertEqual(t.bracket_matches.count(), 6)
        self.assertEqual(t.bracket_matches.filter(bracket_side='winners').count(), 3)
        self.assertEqual(t.bracket_matches.filter(bracket_side='losers').count(), 2)
        gf = t.bracket_matches.get(bracket_side='grand_final')
        self.assertTrue(gf.is_final)

    def test_four_players_full_playthrough_completes(self):
        org = make_user(0)
        t = make_tournament(org, bracket_type='double_elimination')
        for i in range(1, 5):
            register(t, make_user(i))
        gen_bracket(t, org)

        def resolve_all_ready(limit=20):
            """Complete every fully-populated, unresolved match until none remain."""
            for _ in range(limit):
                ready = [
                    m for m in t.bracket_matches.all()
                    if m.status in ('scheduled', 'in_progress')
                    and m.participant_1_id and m.participant_2_id
                ]
                if not ready:
                    return
                for m in ready:
                    m.winner = m.participant_1
                    m.score_p1, m.score_p2 = 1, 0
                    m.status = 'completed'
                    m.completed_at = timezone.now()
                    m.save()  # signal routes winner + loser onward

        resolve_all_ready()
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed', 'double-elim bracket should reach completion')
        self.assertIsNotNone(t.completed_at)
        # Champion is decided by the grand final.
        gf = t.bracket_matches.get(bracket_side='grand_final')
        champ = TournamentRegistration.objects.get(pk=gf.winner_id)
        self.assertEqual(champ.final_position, 1)


class RoundRobinGenerationTests(TestCase):
    def test_four_players_six_matches(self):
        org = make_user(0)
        t = make_tournament(org, bracket_type='round_robin')
        for i in range(1, 5):
            register(t, make_user(i))
        summary = gen_bracket(t, org)
        self.assertEqual(summary['bracket_type'], 'round_robin')
        # n*(n-1)/2 = 6.
        self.assertEqual(t.bracket_matches.count(), 6)


# ---------------------------------------------------------------------------
# Report -> confirm -> auto-advance -> completion (via the real endpoints)
# ---------------------------------------------------------------------------

class MatchFlowTests(TestCase):
    def _play(self, client, match, p1_score, p2_score, reporter, confirmer):
        r = client_for(reporter).post(
            f'/tournament/match/{match.id}/report-score/',
            {'score_p1': p1_score, 'score_p2': p2_score}, format='json',
        )
        self.assertEqual(r.status_code, 200, r.content)
        c = client_for(confirmer).post(
            f'/tournament/match/{match.id}/confirm-score/',
            {'agree': True}, format='json',
        )
        self.assertEqual(c.status_code, 200, c.content)
        return c

    def test_full_single_elim_playthrough(self):
        org = make_user(0)
        t = make_tournament(org, prize_type='winner_takes_all')
        players = {}
        for i in range(1, 5):
            u = make_user(i)
            reg = register(t, u)
            players[reg.id] = u

        # Generate via the endpoint to exercise auth + the view.
        gen = client_for(org).post(f'/tournament/{t.tournament_id}/generate-bracket/', {}, format='json')
        self.assertEqual(gen.status_code, 201, gen.content)

        semis = list(t.bracket_matches.filter(round_number=1).order_by('match_number'))
        self.assertEqual(len(semis), 2)

        # Play both semifinals; winner is always participant_1 (score 2-1).
        for m in semis:
            p1_user = players[m.participant_1_id]
            p2_user = players[m.participant_2_id]
            resp = self._play(None, m, 2, 1, reporter=p1_user, confirmer=p2_user)
            m.refresh_from_db()
            self.assertEqual(m.status, 'completed')
            self.assertEqual(m.winner_id, m.participant_1_id)

        final = t.bracket_matches.get(round_number=2)
        final.refresh_from_db()
        # Both semifinal winners should have auto-advanced into the final.
        self.assertIsNotNone(final.participant_1_id)
        self.assertIsNotNone(final.participant_2_id)

        fin_p1 = players[final.participant_1_id]
        fin_p2 = players[final.participant_2_id]
        self._play(None, final, 3, 0, reporter=fin_p1, confirmer=fin_p2)

        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')
        self.assertIsNotNone(t.completed_at)
        champ = TournamentRegistration.objects.get(pk=final.participant_1_id)
        runner = TournamentRegistration.objects.get(pk=final.participant_2_id)
        champ.refresh_from_db()
        runner.refresh_from_db()
        self.assertEqual(champ.final_position, 1)
        self.assertEqual(runner.final_position, 2)

    def test_reject_opens_dispute(self):
        org = make_user(0)
        t = make_tournament(org)
        players = {}
        for i in range(1, 5):
            u = make_user(i)
            reg = register(t, u)
            players[reg.id] = u
        gen_bracket(t, org)

        m = t.bracket_matches.filter(round_number=1).first()
        p1_user = players[m.participant_1_id]
        p2_user = players[m.participant_2_id]

        r = client_for(p1_user).post(
            f'/tournament/match/{m.id}/report-score/',
            {'score_p1': 2, 'score_p2': 1}, format='json')
        self.assertEqual(r.status_code, 200, r.content)

        c = client_for(p2_user).post(
            f'/tournament/match/{m.id}/confirm-score/',
            {'agree': False, 'dispute_description': 'That score is wrong.'}, format='json')
        self.assertEqual(c.status_code, 200, c.content)
        self.assertEqual(c.json()['data']['status'], 'disputed')

        m.refresh_from_db()
        self.assertEqual(m.status, 'disputed')
        self.assertTrue(TournamentDispute.objects.filter(match=m, status='open').exists())

    def test_non_participant_cannot_report(self):
        org = make_user(0)
        t = make_tournament(org)
        for i in range(1, 5):
            register(t, make_user(i))
        gen_bracket(t, org)
        outsider = make_user(99)
        m = t.bracket_matches.filter(round_number=1).first()
        r = client_for(outsider).post(
            f'/tournament/match/{m.id}/report-score/',
            {'score_p1': 2, 'score_p2': 1}, format='json')
        self.assertEqual(r.status_code, 403, r.content)

    def test_organizer_only_mode_blocks_participant_report(self):
        org = make_user(0)
        t = make_tournament(org, score_mode='organizer_only')
        players = {}
        for i in range(1, 5):
            u = make_user(i)
            reg = register(t, u)
            players[reg.id] = u
        gen_bracket(t, org)
        m = t.bracket_matches.filter(round_number=1).first()
        p1_user = players[m.participant_1_id]
        r = client_for(p1_user).post(
            f'/tournament/match/{m.id}/report-score/',
            {'score_p1': 2, 'score_p2': 1}, format='json')
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()['code'], 'ORGANIZER_ONLY_MODE')

    def test_raise_dispute_endpoint(self):
        org = make_user(0)
        t = make_tournament(org)
        players = {}
        for i in range(1, 5):
            u = make_user(i)
            reg = register(t, u)
            players[reg.id] = u
        gen_bracket(t, org)
        m = t.bracket_matches.filter(round_number=1).first()
        p1_user = players[m.participant_1_id]
        r = client_for(p1_user).post(
            f'/tournament/match/{m.id}/raise-dispute/',
            {'description': 'Opponent cheated', 'evidence_urls': ['http://x/1.png']},
            format='json')
        self.assertEqual(r.status_code, 201, r.content)
        m.refresh_from_db()
        self.assertEqual(m.status, 'disputed')


# ---------------------------------------------------------------------------
# Prize distribution
# ---------------------------------------------------------------------------

class PrizeDistributionTests(TestCase):
    def _completed_tournament_with_prizes(self):
        org = make_user(0)
        t = make_tournament(org, prize_type='distributed')
        regs = []
        for i in range(1, 5):
            regs.append(register(t, make_user(i)))
        gen_bracket(t, org)
        # Force a deterministic finish by playing through directly on the models.
        # Position winners 1..4 by setting final_position + completing matches.
        # Simplest: drive completion by resolving matches through the model.
        semis = list(t.bracket_matches.filter(round_number=1).order_by('match_number'))
        for m in semis:
            m.winner = m.participant_1
            m.score_p1, m.score_p2 = 2, 1
            m.status = 'completed'
            m.completed_at = timezone.now()
            m.save()  # signal advances winner
        final = t.bracket_matches.get(round_number=2)
        final.refresh_from_db()
        final.winner = final.participant_1
        final.score_p1, final.score_p2 = 3, 0
        final.status = 'completed'
        final.completed_at = timezone.now()
        final.save()  # completes tournament
        t.refresh_from_db()
        TournamentPrizeDistribution.objects.create(tournament=t, position=1, prize=1000)
        TournamentPrizeDistribution.objects.create(tournament=t, position=2, prize=500)
        return org, t

    def test_distribute_credits_winner_wallet(self):
        org, t = self._completed_tournament_with_prizes()
        self.assertEqual(t.status, 'completed')

        champ_reg = TournamentRegistration.objects.get(tournament=t, final_position=1)
        runner_reg = TournamentRegistration.objects.get(tournament=t, final_position=2)

        resp = client_for(org).post(
            f'/tournament/{t.tournament_id}/distribute-prizes/', {}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)

        champ_wallet = UserWallet.objects.get(user=champ_reg.user)
        runner_wallet = UserWallet.objects.get(user=runner_reg.user)
        self.assertEqual(champ_wallet.wallet_balance, 1000)
        self.assertEqual(runner_wallet.wallet_balance, 500)
        self.assertEqual(PrizePayout.objects.filter(tournament=t).count(), 2)
        self.assertTrue(Transaction.objects.filter(wallet=champ_wallet, type='prize', amount=1000).exists())

    def test_distribute_is_idempotent(self):
        org, t = self._completed_tournament_with_prizes()
        client_for(org).post(f'/tournament/{t.tournament_id}/distribute-prizes/', {}, format='json')
        resp2 = client_for(org).post(f'/tournament/{t.tournament_id}/distribute-prizes/', {}, format='json')
        self.assertEqual(resp2.status_code, 409, resp2.content)
        self.assertEqual(resp2.json()['code'], 'ALREADY_DISTRIBUTED')
        # No double credit.
        champ_reg = TournamentRegistration.objects.get(tournament=t, final_position=1)
        self.assertEqual(UserWallet.objects.get(user=champ_reg.user).wallet_balance, 1000)


# ---------------------------------------------------------------------------
# Organizer cancel + refund
# ---------------------------------------------------------------------------

class CancelRefundTests(TestCase):
    def test_organizer_cancel_refunds_entry_fees(self):
        org = make_user(0)
        t = make_tournament(org, entry_fee='Paid', entry_fee_price=100, prize_type='no_prize')
        wallets = []
        for i in range(1, 4):
            u = make_user(i, kyc=True)
            reg = register(t, u, paid=True)
            # Simulate they already paid: their balance was debited at registration.
            wallets.append(UserWallet.objects.get(user=u))

        resp = client_for(org).post(f'/tournament/{t.tournament_id}/cancel/',
                                    {'reason': 'Not enough players'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['refunded_count'], 3)
        self.assertEqual(data['total_refunded'], 300)

        for w in wallets:
            w.refresh_from_db()
            self.assertEqual(w.wallet_balance, 100)  # refunded 100 each
        t.refresh_from_db()
        self.assertEqual(t.status, 'cancelled')
        self.assertEqual(t.registrations.filter(status='withdrawn').count(), 3)

    def test_cannot_cancel_after_matches_played(self):
        org = make_user(0)
        t = make_tournament(org)
        players = {}
        for i in range(1, 5):
            u = make_user(i)
            reg = register(t, u)
            players[reg.id] = u
        gen_bracket(t, org)
        m = t.bracket_matches.filter(round_number=1).first()
        m.winner = m.participant_1
        m.status = 'completed'
        m.completed_at = timezone.now()
        m.save()
        resp = client_for(org).post(f'/tournament/{t.tournament_id}/cancel/', {}, format='json')
        self.assertEqual(resp.status_code, 409, resp.content)


# ---------------------------------------------------------------------------
# Registration flow (KYC gate + confirmed status)
# ---------------------------------------------------------------------------

class RegistrationFlowTests(TestCase):
    def test_paid_registration_requires_kyc(self):
        org = make_user(0)
        t = make_tournament(org, entry_fee='Paid', entry_fee_price=50)
        player = make_user(1, kyc=False)
        w = UserWallet.objects.get(user=player)
        w.wallet_balance = 100
        w.pin_hash = None
        w.save()
        resp = client_for(player).post(
            '/tournament/register-tournament/',
            {'tournament_id': t.tournament_id, 'pin': '1234'}, format='json')
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertEqual(resp.json().get('code'), 'KYC_REQUIRED')

    def test_free_registration_is_confirmed(self):
        org = make_user(0)
        t = make_tournament(org, entry_fee='Free')
        player = make_user(1)
        resp = client_for(player).post(
            '/tournament/register-tournament/',
            {'tournament_id': t.tournament_id}, format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        reg = TournamentRegistration.objects.get(tournament=t, user=player)
        self.assertEqual(reg.status, 'confirmed')


# ---------------------------------------------------------------------------
# Sponsors M2M integrity (guards what migration 0011 reconciles)
# ---------------------------------------------------------------------------

class SponsorM2MTests(TestCase):
    """Guards the Tournament.sponsors M2M relationship that create_tournament
    writes (`Sponsors.objects.create(...)` + `tournament.sponsors.add(...)`).

    NOTE: run under test_settings the schema is built from the models (migrations
    are disabled), so this exercises the M2M/FK integrity under Django's per-test
    constraint check, NOT the migrated schema. The migration-drift proof (that a
    fresh MIGRATED database also accepts this write) is the separate FK-enforced
    fresh-migrate check + the MySQL run handed to FE-events.
    """

    def test_add_sponsor_and_read_back(self):
        org = make_user(0)
        t = make_tournament(org)
        # Mirror create_tournament's sponsor block.
        sponsor = Sponsors.objects.create(
            name='Acme Corp',
            sponsor_type=ContentType.objects.get_for_model(Users),
            sponsor_id_object=org.pk,
        )
        t.sponsors.add(sponsor)  # FK-violates if the through FK targets the wrong table

        self.assertEqual(list(t.sponsors.all()), [sponsor])
        # reverse read used by get_all_tournaments / view_user_drafted_tournaments
        self.assertEqual([s.sponsor_id for s in t.sponsors.all()], [sponsor.sponsor_id])


class CreateTournamentWithSponsorsEndpointTests(TestCase):
    """End-to-end for the sponsor launch flow via the ACTUAL wizard payload:
    create-tournament (JSON-stringified sponsor arrays in multipart) -> the
    sponsors persist -> view-tournament serializes them without a 500.
    """

    def _create_payload(self, **overrides):
        payload = {
            'tournament_title': 'Sponsored Cup',
            'game': 'valorant',
            'tournament_type': 'online',
            'tournament_visibility': 'public',
            'tournament_access': 'individual',
            'entry_type': 'Free',
            'bracket_type': 'single_elimination',
            'start_date_and_time': '2030-01-01T00:00',
            'end_date_and_time': '2030-01-02T00:00',
            'prize_type': 'no_prize',
            'is_draft': '0',
            # Exactly what CreateTournamentComponent.js appends: JSON arrays.
            'sponsor_names': json.dumps(['Acme Corp', 'Globex']),
            'sponsor_types': json.dumps(['individual', 'individual']),
            'sponsor_usernames': json.dumps(['', '']),
        }
        payload.update(overrides)
        return payload

    def test_create_with_sponsors_then_view_serializes_them(self):
        org = make_user(0)
        Games.objects.get_or_create(game_title='Valorant')[0]

        resp = client_for(org).post(
            '/tournament/create-tournament/', self._create_payload(), format='multipart')
        self.assertEqual(resp.status_code, 201, resp.content)

        t = Tournament.objects.get(tournament_title='Sponsored Cup')
        # Both name-only sponsors ('individual') must be persisted + linked.
        self.assertEqual(t.sponsors.count(), 2)
        self.assertEqual({s.name for s in t.sponsors.all()}, {'Acme Corp', 'Globex'})

        # view_tournament used to 500 on sponsor.id (Sponsors PK is sponsor_id).
        view = client_for(org).get(f'/tournament/view-tournament/{t.tournament_id}/')
        self.assertEqual(view.status_code, 200, view.content)
        sponsors = view.json()['data']['sponsors']
        self.assertEqual({s['name'] for s in sponsors}, {'Acme Corp', 'Globex'})
        self.assertTrue(all(s.get('id') for s in sponsors))

    def test_unresolvable_entity_sponsor_does_not_break_creation(self):
        org = make_user(0)
        Games.objects.get_or_create(game_title='Valorant')[0]
        # A 'user' type whose username does not exist must not 500 the whole create.
        payload = self._create_payload(
            sponsor_names=json.dumps(['Ghost Sponsor']),
            sponsor_types=json.dumps(['user']),
            sponsor_usernames=json.dumps(['nonexistent_user_xyz']),
        )
        resp = client_for(org).post(
            '/tournament/create-tournament/', payload, format='multipart')
        self.assertEqual(resp.status_code, 201, resp.content)
        t = Tournament.objects.get(tournament_title='Sponsored Cup')
        self.assertEqual(t.sponsors.count(), 1)
        self.assertIsNone(t.sponsors.first().sponsor)  # stored name-only


class SponsorLogoTests(CreateTournamentWithSponsorsEndpointTests):
    """Sponsor logos have never saved.

    The backend has always read request.FILES.getlist('sponsor_logos') and
    matched them by index. The wizard read each file into a base64 data URL, put
    that in formData, and never appended a single file - so the name and the
    type went up and the picture was dropped, every time.

    Positional matching means an empty slot has to be sent as something, or the
    second sponsor's logo lands on the first. A zero-length blob holds the place
    and must not be stored as a file.

    Inherits the payload from the class above, which is the shape the real wizard
    sends. Writing a second one by hand is how a test comes to pass against a
    request that nothing actually makes.
    """

    def _files(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        png = SimpleUploadedFile(
            'logo.png',
            b'\x89PNG\r\n\x1a\n' + b'0' * 40,
            content_type='image/png',
        )
        blank = SimpleUploadedFile(
            'blank', b'', content_type='application/octet-stream')
        return [png, blank]

    def _post(self):
        org = make_user(9412)
        Games.objects.get_or_create(game_title='Valorant')
        payload = self._create_payload()
        payload['sponsor_logos'] = self._files()
        return client_for(org).post(
            '/tournament/create-tournament/', payload, format='multipart')

    def test_a_sponsor_logo_is_stored(self):
        res = self._post()
        self.assertIn(res.status_code, (200, 201), res.content)
        t = Tournament.objects.get(tournament_title='Sponsored Cup')
        sponsors = list(t.sponsors.all().order_by('sponsor_id'))
        self.assertEqual(len(sponsors), 2)
        self.assertTrue(sponsors[0].logo, 'the first sponsor lost its logo')

    def test_an_empty_slot_is_not_stored_as_a_file(self):
        """It holds the place so the indexes line up. It is not a picture."""
        self._post()
        t = Tournament.objects.get(tournament_title='Sponsored Cup')
        sponsors = list(t.sponsors.all().order_by('sponsor_id'))
        self.assertFalse(sponsors[1].logo, 'an empty slot was stored as a file')


class MissingGameTests(TestCase):
    """A tournament with no game answered 500 with "'NoneType' object has no
    attribute 'title'", which tells the organiser nothing and whoever reads the
    log almost as little."""

    def setUp(self):
        self.client_ = client_for(make_user(9411))

    def test_no_game_is_a_400_that_says_so(self):
        res = self.client_.post('/tournament/create-tournament/', {
            'tournament_title': 'Gameless', 'tournament_type': 'online',
        }, format='multipart')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'GAME_REQUIRED')

    def test_an_unknown_game_is_a_400_that_names_it(self):
        res = self.client_.post('/tournament/create-tournament/', {
            'tournament_title': 'Gameless', 'game': 'Not A Real Game',
            'tournament_type': 'online',
        }, format='multipart')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'GAME_NOT_FOUND')
