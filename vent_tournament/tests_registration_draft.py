"""A tournament registration saved as it goes (inbox 436).

Pinned here: the draft is the signed-in person's own and nobody else's, it is
saved at every step and read back on any device, it says whether the event
ticket is the entry and whether they already hold it, it lasts thirty days
from the last save, a team in it must be one of theirs, and a registration
that goes through removes it.
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.hashers import make_password
from django.db.models.query import QuerySet
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, TeamMembers, Teams, Users, UserWallet
from vent_event.models import Event, EventTournamentLink, Ticket, TicketTier

from .models import Tournament, TournamentRegistrationDraft


def a_user(name, balance=0):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('d-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('dw%s' % name)[:10], user=user, wallet_balance=balance,
        pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class RegistrationDraftTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('rd_org')
        self.player, self.player_auth = a_user('rd_player', balance=100)
        self.other, self.other_auth = a_user('rd_other')
        self.game = Games.objects.create(game_title='EA FC RD')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Draft Fest', creator=self.organiser, event_type='physical', desc='x',
            entry_fee=0, start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=8),
            reg_start_date=now - timedelta(days=1), reg_end_date=now + timedelta(days=4))
        self.pass_tier = TicketTier.objects.create(
            event=self.event, name='Competitor pass', price=Decimal('0'), quantity=32)
        self.tournament = Tournament.objects.create(
            tournament_title='Draft Cup', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=6),
            bracket_type='single_elimination', is_draft=False,
            tournament_access='team_and_individual', entry_fee='Free', entry_fee_price=0)
        self.link = EventTournamentLink.objects.create(
            event=self.event, tournament=self.tournament, linked_by=self.organiser,
            entry_mode=EventTournamentLink.ENTRY_TICKET, entry_tier=self.pass_tier)
        self.team = Teams.objects.create(
            team_name='Draft Squad', game=self.game, description='x',
            team_creator=self.player, team_owner=self.player,
            penalty_points=0, number_of_members=1)
        TeamMembers.objects.create(team=self.team, user=self.player)
        self.theirs = Teams.objects.create(
            team_name='Not Mine', game=self.game, description='x',
            team_creator=self.other, team_owner=self.other,
            penalty_points=0, number_of_members=1)

    def url(self, ref=None):
        return '/tournament/%s/registration-draft/' % (ref or self.tournament.slug)

    def save(self, body, auth=None):
        return self.client.put(self.url(), body, content_type='application/json',
                               **(self.player_auth if auth is None else auth))

    def read(self, auth=None):
        return self.client.get(self.url(), **(self.player_auth if auth is None else auth))

    def test_nobody_signed_out_has_a_draft(self):
        self.assertEqual(self.read(auth={}).status_code, 401)
        self.assertEqual(self.save({'step': 'mode'}, auth={}).status_code, 401)
        self.assertEqual(self.client.get('/tournament/registration-drafts/mine/').status_code, 401)

    def test_saved_at_each_step_and_read_back(self):
        self.assertIsNone(self.read().json()['data']['draft'])
        self.assertEqual(self.save({'mode': 'team', 'step': 'team'}).status_code, 200)
        res = self.save({'team_id': self.team.team_id, 'step': 'ticket'})
        self.assertEqual(res.status_code, 200, res.content)
        draft = self.read().json()['data']['draft']
        self.assertEqual((draft['mode'], draft['step'], draft['team']['name']),
                         ('team', 'ticket', 'Draft Squad'))
        self.assertEqual(TournamentRegistrationDraft.objects.count(), 1)
        # Somebody else reading the same tournament sees their own (none).
        self.assertIsNone(self.read(auth=self.other_auth).json()['data']['draft'])

    def test_it_says_the_ticket_is_the_entry_and_whether_they_hold_it(self):
        entry = self.read().json()['data']['entry']
        self.assertEqual((entry['needs_ticket'], entry['holds_ticket']), (True, False))
        self.assertEqual(entry['tier'], {'id': self.pass_tier.id, 'name': 'Competitor pass'})
        self.assertEqual(entry['event']['slug'], self.event.slug)
        Ticket.objects.create(event=self.event, tier=self.pass_tier, user=self.player,
                              code='RD-1', price_vc=0, price_ngn=0,
                              attendee_email=self.player.email)
        self.assertTrue(self.read().json()['data']['entry']['holds_ticket'])

    def test_a_team_must_be_one_of_theirs(self):
        res = self.save({'mode': 'team', 'team_id': self.theirs.team_id, 'step': 'review'})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'TEAM_NOT_YOURS')
        missing = self.save({'mode': 'team', 'team_id': 999999, 'step': 'review'})
        self.assertEqual(missing.json()['code'], 'TEAM_NOT_YOURS')

    def test_two_saves_add_to_each_other_in_either_order(self):
        # Choosing solo saved the way in and the ticket step as two requests; on
        # a phone they landed together, the second answered 500 and the draft
        # was left at "solo or team" (walk, 13 October 2026). Each save changes
        # only what it names, so the order cannot matter.
        for first, second in (({'step': 'ticket'}, {'mode': 'individual', 'team_id': None}),
                              ({'mode': 'individual', 'team_id': None}, {'step': 'ticket'})):
            TournamentRegistrationDraft.objects.all().delete()
            self.assertEqual(self.save(first).status_code, 200)
            self.assertEqual(self.save(second).status_code, 200)
            draft = self.read().json()['data']['draft']
            self.assertEqual((draft['mode'], draft['step']), ('individual', 'ticket'), (first, second))
        self.assertEqual(TournamentRegistrationDraft.objects.count(), 1)

    def test_a_save_racing_the_first_save_merges_instead_of_failing(self):
        # The other request's row lands between this one looking and writing,
        # as it did on the phone: the INSERT used to fail on the one-per-person
        # constraint and answer 500, losing the step.
        real_first = QuerySet.first
        raced = []

        def the_other_request_lands_after_this_one_looks(queryset):
            found = real_first(queryset)
            if queryset.model is TournamentRegistrationDraft and found is None and not raced:
                raced.append(True)
                TournamentRegistrationDraft.objects.bulk_create([TournamentRegistrationDraft(
                    user=self.player, tournament=self.tournament, mode='individual', step='mode')])
            return found

        with mock.patch.object(QuerySet, 'first', autospec=True,
                               side_effect=the_other_request_lands_after_this_one_looks):
            res = self.save({'step': 'ticket'})
        self.assertEqual(raced, [True])
        self.assertEqual(res.status_code, 200, res.content[:300])
        draft = self.read().json()['data']['draft']
        self.assertEqual((draft['mode'], draft['step']), ('individual', 'ticket'))
        self.assertEqual(TournamentRegistrationDraft.objects.count(), 1)

    def test_a_save_naming_only_the_step_keeps_the_team(self):
        self.save({'mode': 'team', 'team_id': self.team.team_id, 'step': 'review'})
        self.save({'step': 'ticket'})
        draft = self.read().json()['data']['draft']
        self.assertEqual((draft['mode'], draft['step'], draft['team']['name']),
                         ('team', 'ticket', 'Draft Squad'))
        # Going solo drops the team, whatever the save left unnamed.
        self.save({'mode': 'individual'})
        self.assertIsNone(self.read().json()['data']['draft']['team'])

    def test_a_bad_step_is_refused(self):
        self.assertEqual(self.save({'step': 'pay-later'}).status_code, 400)

    def test_thirty_days_from_the_last_save(self):
        self.save({'mode': 'individual', 'step': 'ticket'})
        TournamentRegistrationDraft.objects.update(
            updated_at=timezone.now() - timedelta(days=31))
        self.assertIsNone(self.read().json()['data']['draft'])
        self.assertFalse(TournamentRegistrationDraft.objects.exists())

    def test_my_drafts_lists_them_and_start_again_removes_one(self):
        self.save({'mode': 'individual', 'step': 'ticket'})
        mine = self.client.get('/tournament/registration-drafts/mine/', **self.player_auth)
        self.assertEqual([d['tournament']['slug'] for d in mine.json()['data']['drafts']],
                         [self.tournament.slug])
        self.assertEqual(self.client.delete(self.url(), **self.player_auth).status_code, 200)
        self.assertIsNone(self.read().json()['data']['draft'])

    def test_registering_removes_the_draft(self):
        self.save({'mode': 'individual', 'step': 'ticket'})
        Ticket.objects.create(event=self.event, tier=self.pass_tier, user=self.player,
                              code='RD-2', price_vc=0, price_ngn=0,
                              attendee_email=self.player.email)
        res = self.client.post('/tournament/register-tournament/',
                               {'tournament_id': self.tournament.tournament_id, 'mode': 'individual'},
                               content_type='application/json', **self.player_auth)
        self.assertIn(res.status_code, (200, 201), res.content)
        self.assertFalse(TournamentRegistrationDraft.objects.exists())
        # And a registered person has nothing to resume or save.
        self.assertTrue(self.read().json()['data']['registered'])
        self.assertEqual(self.save({'step': 'mode'}).json()['code'], 'ALREADY_REGISTERED')
