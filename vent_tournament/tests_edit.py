"""Editing a tournament after it exists.

CEO: "Even the edit tournament and dq dont do enough"

The endpoint has accepted 26 fields all along; the console's form showed seven,
so the money, the size and the shape of a tournament were uneditable from the
one screen whose purpose is correcting somebody else's mistake.

Widening the form means far more numbers reach integer and decimal columns, and
the loop used to setattr whatever arrived. So these are mostly about what
happens when a number is not one.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users

from .models import Tournament


def signed_in(username, **extra):
    user = Users.objects.create(
        username=username, email='%s@vent.test' % username,
        login_session_token=('e-%s' % username)[:16], is_active=True, **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class EditTournamentTests(TestCase):
    def setUp(self):
        self.owner, self.auth = signed_in('edit_owner')
        self.stranger, self.stranger_auth = signed_in('edit_stranger')
        game = Games.objects.get_or_create(game_title='Edit Probe')[0]
        now = timezone.now()
        self.t = Tournament.objects.create(
            tournament_title='Edit Probe Cup', tournament_creator=self.owner,
            tournament_game=game, tournament_type='online',
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            bracket_type='single_elimination', team_size=1,
            min_number_of_teams=2, max_number_of_teams=16,
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=3), is_draft=False,
        )

    def edit(self, body, auth=None):
        return self.client.put(
            '/tournament/edit-tournament/%s/' % self.t.tournament_id,
            data=body, content_type='application/json',
            **(auth if auth is not None else self.auth))

    # -------------------------------------------------- the widened fields

    def test_the_entry_fee_can_be_corrected(self):
        """Uneditable until now, so an organiser who set it wrong had to be
        told to cancel and start again."""
        res = self.edit({'entry_fee': 'Paid', 'entry_fee_price': '500'})
        self.assertEqual(res.status_code, 200, res.content)
        self.t.refresh_from_db()
        self.assertEqual(self.t.entry_fee, 'Paid')
        self.assertEqual(int(self.t.entry_fee_price), 500)

    def test_the_caps_can_be_corrected(self):
        res = self.edit({'min_number_of_teams': '4', 'max_number_of_teams': '32'})
        self.assertEqual(res.status_code, 200, res.content)
        self.t.refresh_from_db()
        self.assertEqual(self.t.min_number_of_teams, 4)
        self.assertEqual(self.t.max_number_of_teams, 32)

    def test_the_shape_can_be_corrected(self):
        res = self.edit({'tournament_access': 'team', 'team_size': '5',
                         'prize_type': 'winner_takes_all', 'game_mode': 'Clash Squad'})
        self.assertEqual(res.status_code, 200, res.content)
        self.t.refresh_from_db()
        self.assertEqual(self.t.tournament_access, 'team')
        self.assertEqual(self.t.team_size, 5)
        self.assertEqual(self.t.prize_type, 'winner_takes_all')
        self.assertEqual(self.t.game_mode, 'Clash Squad')

    # ------------------------------------------------------- what is not a number

    def test_a_cap_that_is_not_a_number_is_a_400_naming_the_field(self):
        """It used to raise at save time: a 500 with no idea which field."""
        res = self.edit({'max_number_of_teams': 'fifty'})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'INVALID_NUMBER')
        self.assertEqual(res.json()['field'], 'max_number_of_teams')

    def test_a_fee_that_is_not_a_number_is_refused(self):
        res = self.edit({'entry_fee_price': 'five hundred'})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'entry_fee_price')

    def test_a_negative_number_is_refused(self):
        res = self.edit({'entry_fee_price': '-100'})
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_refused_edit_changes_nothing_at_all(self):
        """Not even the fields that came through before the bad one."""
        self.edit({'tournament_title': 'Renamed First',
                   'max_number_of_teams': 'fifty'})
        self.t.refresh_from_db()
        self.assertEqual(self.t.tournament_title, 'Edit Probe Cup')

    def test_an_empty_number_is_left_alone_rather_than_zeroed(self):
        """Clearing a cap is a different request from not touching it, and a
        form that sends every field would otherwise zero them."""
        res = self.edit({'max_number_of_teams': ''})
        self.assertEqual(res.status_code, 200, res.content)
        self.t.refresh_from_db()
        self.assertEqual(self.t.max_number_of_teams, 16)

    # -------------------------------------------------------------- ordering

    def test_ending_before_starting_is_refused(self):
        now = timezone.now()
        res = self.edit({
            'start_date_and_time': (now + timedelta(days=5)).isoformat(),
            'end_date_and_time': (now + timedelta(days=4)).isoformat(),
        })
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'END_BEFORE_START')

    def test_a_floor_above_the_ceiling_is_refused(self):
        """It can never be satisfied, so the tournament could never start."""
        res = self.edit({'min_number_of_teams': '40', 'max_number_of_teams': '16'})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'MIN_ABOVE_MAX')

    # ----------------------------------------------------------------- access

    def test_a_stranger_cannot_edit_it(self):
        res = self.edit({'tournament_title': 'Hijacked'}, auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)
        self.t.refresh_from_db()
        self.assertEqual(self.t.tournament_title, 'Edit Probe Cup')

    def test_an_admin_may_correct_somebody_elses(self):
        admin, admin_auth = signed_in('edit_admin', is_staff=True,
                                      admin_role='super_admin')
        res = self.edit({'tournament_location': 'Moved to Ikeja'}, auth=admin_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.t.refresh_from_db()
        self.assertEqual(self.t.tournament_location, 'Moved to Ikeja')
