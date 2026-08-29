"""How you get into a tournament that runs inside an event.

CEO: "if a tournament will be linked to an event an organizer can decide if the
players in the tournament will have to buy tickets to pay or the tournament will
have its own registeration fee, or if them getting to like the finals gets the
players that got there automatic tickets or not and what level of tickets."

Three arrangements, and they are genuinely different rather than shades of one:

  ticket    the event ticket IS the entry, and somebody without one cannot
            enter at all
  own_fee   the tournament charges its own entry; the ticket is separate
  free      neither costs anything

Plus the reward: reaching a round earns a ticket, at a named tier. Awarded on
ARRIVAL in the round rather than on winning it, because "everyone who makes the
semi-finals gets a pass" means the four who got there.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users, UserWallet
from vent_tournament.models import (BracketMatch, Tournament,
                                    TournamentRegistration)

from .models import Event, EventTournamentLink, Ticket, TicketTier


def a_user(name, balance=0):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('e-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('ew%s' % name)[:10], user=user, wallet_balance=balance,
        pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class TournamentEntryRulesTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('te_org')
        self.player, self.player_auth = a_user('te_player', balance=100000)
        game = Games.objects.create(game_title='EA FC TE')
        now = timezone.now()

        self.event = Event.objects.create(
            name='Festival', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=8),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))
        self.ga = TicketTier.objects.create(
            event=self.event, name='General', price=Decimal('0'), quantity=200)
        self.player_pass = TicketTier.objects.create(
            event=self.event, name='Competitor pass', price=Decimal('0'),
            quantity=32)

        self.tournament = Tournament.objects.create(
            tournament_title='Festival Cup', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=6),
            bracket_type='single_elimination', is_draft=False,
            tournament_access='individual',
            entry_fee='Free', entry_fee_price=0,
        )
        self.link = EventTournamentLink.objects.create(
            event=self.event, tournament=self.tournament, linked_by=self.organiser)

    def register(self, auth=None):
        return self.client.post(
            '/tournament/register-tournament/',
            data={'tournament_id': self.tournament.tournament_id},
            content_type='application/json', **(auth or self.player_auth))

    def give_ticket(self, tier):
        return Ticket.objects.create(
            event=self.event, tier=tier, user=self.player, code='T-%s' % tier.pk,
            price_vc=0, price_ngn=0, attendee_email=self.player.email)

    # ------------------------------------------------------------ free entry

    def test_free_is_the_default_and_lets_anybody_in(self):
        self.assertEqual(self.link.entry_mode, EventTournamentLink.ENTRY_FREE)
        self.assertIn(self.register().status_code, (200, 201))

    # ---------------------------------------------------------- ticket entry

    def test_without_a_ticket_you_cannot_enter(self):
        self.link.entry_mode = EventTournamentLink.ENTRY_TICKET
        self.link.save()
        res = self.register()
        self.assertEqual(res.status_code, 402, res.json())
        self.assertEqual(res.json()['code'], 'EVENT_TICKET_REQUIRED')
        self.assertFalse(TournamentRegistration.objects.filter(
            tournament=self.tournament, user=self.player).exists())

    def test_with_a_ticket_you_can(self):
        self.link.entry_mode = EventTournamentLink.ENTRY_TICKET
        self.link.save()
        self.give_ticket(self.ga)
        self.assertIn(self.register().status_code, (200, 201))

    def test_the_named_tier_is_the_one_that_admits(self):
        # A general admission ticket does not get you into the competition when
        # the organiser said the competitor pass does.
        self.link.entry_mode = EventTournamentLink.ENTRY_TICKET
        self.link.entry_tier = self.player_pass
        self.link.save()
        self.give_ticket(self.ga)
        self.assertEqual(self.register().status_code, 402)

        self.give_ticket(self.player_pass)
        self.assertIn(self.register().status_code, (200, 201))

    def test_the_refusal_says_where_to_get_one(self):
        self.link.entry_mode = EventTournamentLink.ENTRY_TICKET
        self.link.entry_tier = self.player_pass
        self.link.save()
        body = self.register().json()
        self.assertIn('Competitor pass', body['message'])
        self.assertEqual(body['data']['event_id'], self.event.event_id)
        self.assertEqual(body['data']['tier_id'], self.player_pass.pk)

    def test_a_cancelled_ticket_does_not_admit(self):
        self.link.entry_mode = EventTournamentLink.ENTRY_TICKET
        self.link.save()
        ticket = self.give_ticket(self.ga)
        ticket.status = 'cancelled'
        ticket.save()
        self.assertEqual(self.register().status_code, 402)

    # -------------------------------------------------------- its own fee

    def test_own_fee_does_not_ask_for_a_ticket(self):
        self.link.entry_mode = EventTournamentLink.ENTRY_OWN_FEE
        self.link.save()
        self.assertIn(self.register().status_code, (200, 201))

    # ------------------------------------------------------ the organiser

    def test_the_organiser_sets_the_arrangement(self):
        res = self.client.post(
            '/event/%s/tournament/%s/ticketing/' % (
                self.event.event_id, self.tournament.tournament_id),
            data={'shared_ticketing': True, 'entry_mode': 'ticket',
                  'entry_tier': self.player_pass.pk},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        self.link.refresh_from_db()
        self.assertEqual(self.link.entry_mode, 'ticket')
        self.assertEqual(self.link.entry_tier_id, self.player_pass.pk)

    def test_an_unknown_arrangement_is_refused(self):
        res = self.client.post(
            '/event/%s/tournament/%s/ticketing/' % (
                self.event.event_id, self.tournament.tournament_id),
            data={'shared_ticketing': False, 'entry_mode': 'vibes'},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)

    def test_a_tier_from_another_event_is_refused(self):
        now = timezone.now()
        other = Event.objects.create(
            name='Elsewhere', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0, start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))
        theirs = TicketTier.objects.create(
            event=other, name='Theirs', price=Decimal('0'), quantity=5)
        res = self.client.post(
            '/event/%s/tournament/%s/ticketing/' % (
                self.event.event_id, self.tournament.tournament_id),
            data={'shared_ticketing': False, 'entry_tier': theirs.pk},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 404)

    def test_a_reward_with_no_ticket_behind_it_is_refused(self):
        # Promising something that cannot be handed over.
        res = self.client.post(
            '/event/%s/tournament/%s/ticketing/' % (
                self.event.event_id, self.tournament.tournament_id),
            data={'shared_ticketing': False, 'reward_from_round': 3},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)

    # --------------------------------------------------------- the reward

    def test_reaching_the_round_earns_a_ticket(self):
        self.link.reward_from_round = 2
        self.link.reward_tier = self.player_pass
        self.link.save()

        reg = TournamentRegistration.objects.create(
            tournament=self.tournament, user=self.player, status='confirmed')
        other = TournamentRegistration.objects.create(
            tournament=self.tournament, user=a_user('te_rival')[0],
            status='confirmed')
        match = BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=reg, participant_2=other,
            winner=reg, status='completed')

        from vent_tournament.services import advance
        advance.cascade(match)

        self.assertTrue(Ticket.objects.filter(
            event=self.event, user=self.player, tier=self.player_pass).exists())

    def test_the_ticket_is_not_awarded_twice(self):
        # cascade can run more than once for the same match, and two tickets
        # for one achievement is two people through one door.
        self.link.reward_from_round = 2
        self.link.reward_tier = self.player_pass
        self.link.save()
        reg = TournamentRegistration.objects.create(
            tournament=self.tournament, user=self.player, status='confirmed')
        match = BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=reg, winner=reg, status='completed')

        from vent_tournament.services import advance
        advance.cascade(match)
        advance.cascade(match)

        self.assertEqual(Ticket.objects.filter(
            event=self.event, user=self.player, tier=self.player_pass).count(), 1)

    def test_nothing_is_awarded_before_the_round(self):
        self.link.reward_from_round = 4
        self.link.reward_tier = self.player_pass
        self.link.save()
        reg = TournamentRegistration.objects.create(
            tournament=self.tournament, user=self.player, status='confirmed')
        match = BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=reg, winner=reg, status='completed')

        from vent_tournament.services import advance
        advance.cascade(match)
        self.assertFalse(Ticket.objects.filter(
            event=self.event, user=self.player).exists())

    def test_no_reward_configured_awards_nothing(self):
        reg = TournamentRegistration.objects.create(
            tournament=self.tournament, user=self.player, status='confirmed')
        match = BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=reg, winner=reg, status='completed')
        from vent_tournament.services import advance
        advance.cascade(match)
        self.assertFalse(Ticket.objects.filter(event=self.event).exists())
