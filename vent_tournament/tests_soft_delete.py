"""Deleting a tournament without destroying it.

CEO, 8 September 2026: "there should be a way for peopl to delete events, of
course it is a soft delete thata dmins should be able to restore or still
check".

The cases worth writing down are the ones where a soft delete is usually got
wrong: the row that is still findable by an ordinary query, the delete that
takes somebody's paid seat with it, and the restore that anybody can press.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_auth import softdelete

from .models import Tournament, TournamentRegistration


def a_user(name, **extra):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('tok-%s' % name)[:16], **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class SoftDeleteTournamentTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('sd_owner')
        self.stranger, self.stranger_auth = a_user('sd_stranger')
        self.admin, self.admin_auth = a_user('sd_admin', is_staff=True,
                                             admin_role='super_admin')
        self.game = Games.objects.get_or_create(game_title='Soft Delete Probe')[0]
        self.tournament = self.make()

    def make(self, title='Soft Delete Cup', fee=0):
        now = timezone.now()
        return Tournament.objects.create(
            tournament_title=title, tournament_creator=self.owner,
            tournament_game=self.game, tournament_type='online',
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Paid' if fee else 'Free', entry_fee_price=fee,
            prize_type='no_prize', bracket_type='single_elimination',
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=3),
            is_draft=False,
        )

    def url(self, suffix='delete/', tournament=None):
        return '/tournament/%s/%s' % (
            (tournament or self.tournament).tournament_id, suffix)

    # ------------------------------------------------------------ the delete

    def test_the_owner_can_delete_their_own_tournament(self):
        res = self.client.post(self.url(), content_type='application/json',
                               **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        row = Tournament.all_objects.get(pk=self.tournament.pk)
        self.assertIsNotNone(row.deleted_at)
        self.assertEqual(row.deleted_by_id, self.owner.user_id)

    def test_the_row_survives_and_carries_who_deleted_it(self):
        self.client.post(self.url(), data={'reason': 'wrong game'},
                         content_type='application/json', **self.owner_auth)
        row = Tournament.all_objects.get(pk=self.tournament.pk)
        self.assertEqual(row.deleted_reason, 'wrong game')
        self.assertEqual(row.tournament_title, 'Soft Delete Cup')

    def test_an_ordinary_query_cannot_see_it_at_all(self):
        """The manager is what makes this hold at all 60 query sites. A filter
        written at each read site is a rule that holds at 59 of them."""
        self.client.post(self.url(), content_type='application/json',
                         **self.owner_auth)
        self.assertFalse(
            Tournament.objects.filter(pk=self.tournament.pk).exists())
        self.assertTrue(
            Tournament.all_objects.filter(pk=self.tournament.pk).exists())

    def test_its_own_address_stops_answering(self):
        self.client.post(self.url(), content_type='application/json',
                         **self.owner_auth)
        res = self.client.get('/tournament/%s/rules/' % self.tournament.tournament_id)
        self.assertEqual(res.status_code, 404, res.content)

    def test_a_stranger_cannot_delete_it(self):
        res = self.client.post(self.url(), content_type='application/json',
                               **self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)
        self.assertIsNone(
            Tournament.all_objects.get(pk=self.tournament.pk).deleted_at)

    def test_an_admin_can_delete_somebody_elses(self):
        res = self.client.post(self.url(), content_type='application/json',
                               **self.admin_auth)
        self.assertEqual(res.status_code, 200, res.content)

    # ------------------------------------------------- money and seats first

    def test_a_paid_tournament_with_entrants_is_refused_outright(self):
        """A confirmation box is not consent from the person who paid."""
        paid = self.make(title='Paid Cup', fee=500)
        TournamentRegistration.objects.create(tournament=paid, user=self.stranger,
                                              status='confirmed')
        res = self.client.post(self.url(tournament=paid),
                               data={'confirm': True},
                               content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'PAID_ENTRANTS')
        self.assertIsNone(Tournament.all_objects.get(pk=paid.pk).deleted_at)

    def test_a_free_tournament_with_entrants_asks_a_second_time(self):
        TournamentRegistration.objects.create(tournament=self.tournament,
                                              user=self.stranger, status='confirmed')
        res = self.client.post(self.url(), content_type='application/json',
                               **self.owner_auth)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'CONFIRM_REQUIRED')
        self.assertEqual(res.json()['data']['unpaid'], 1)

        again = self.client.post(self.url(), data={'confirm': True},
                                 content_type='application/json', **self.owner_auth)
        self.assertEqual(again.status_code, 200, again.content)

    # ----------------------------------------------------------- the restore

    def test_an_admin_can_restore_it_and_it_comes_back_where_it_was(self):
        self.client.post(self.url(), content_type='application/json',
                         **self.owner_auth)
        res = self.client.post(self.url('restore/'), content_type='application/json',
                               **self.admin_auth)
        self.assertEqual(res.status_code, 200, res.content)
        row = Tournament.objects.get(pk=self.tournament.pk)
        self.assertIsNone(row.deleted_at)
        self.assertEqual(row.deleted_reason, '')

    def test_the_owner_cannot_restore_their_own(self):
        """Somebody who can delete and undelete at will can hide something and
        put it back with nothing recorded in between."""
        self.client.post(self.url(), content_type='application/json',
                         **self.owner_auth)
        res = self.client.post(self.url('restore/'), content_type='application/json',
                               **self.owner_auth)
        self.assertEqual(res.status_code, 403, res.content)

    def test_restoring_something_that_is_not_deleted_says_so(self):
        res = self.client.post(self.url('restore/'), content_type='application/json',
                               **self.admin_auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'NOT_DELETED')

    # ------------------------------------------------------------- the lists

    def test_it_leaves_the_public_listing(self):
        before = self.client.get('/tournament/get-all-tournaments/')
        self.assertEqual(before.status_code, 200, before.content)
        self.client.post(self.url(), content_type='application/json',
                         **self.owner_auth)
        after = self.client.get('/tournament/get-all-tournaments/')
        body = after.content.decode('utf-8', 'replace')
        self.assertNotIn('Soft Delete Cup', body)

    def test_the_admin_console_can_still_find_it(self):
        """Deleted has a bucket. A row belonging to no tab is a row that has
        vanished, which is how three cancelled tournaments were lost."""
        self.client.post(self.url(), content_type='application/json',
                         **self.owner_auth)
        res = self.client.get('/auth/admin/tournaments/?status=deleted',
                              **self.admin_auth)
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()['data']['results']
        self.assertEqual([r['name'] for r in rows], ['Soft Delete Cup'])
        self.assertEqual(rows[0]['status'], 'deleted')
        self.assertEqual(rows[0]['deleted_by'], 'sd_owner')

    def test_deleting_a_draft_no_longer_destroys_the_row(self):
        draft = self.make(title='Draft Cup')
        draft.is_draft = True
        draft.save(update_fields=['is_draft'])
        res = self.client.delete(
            '/tournament/delete-draft/%s/' % draft.tournament_id, **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(Tournament.all_objects.filter(pk=draft.pk).exists())
        self.assertIsNotNone(Tournament.all_objects.get(pk=draft.pk).deleted_at)


class DeletionGuardTests(TestCase):
    """The rule itself, in isolation, so both models cannot drift from it."""

    def test_paid_refuses_even_when_confirmed(self):
        self.assertEqual(softdelete.deletion_guard(3, 0, confirmed=True)[0],
                         'PAID_ENTRANTS')

    def test_unpaid_asks_once_and_then_allows(self):
        self.assertEqual(softdelete.deletion_guard(0, 5)[0], 'CONFIRM_REQUIRED')
        self.assertIsNone(softdelete.deletion_guard(0, 5, confirmed=True))

    def test_empty_goes_straight_through(self):
        self.assertIsNone(softdelete.deletion_guard(0, 0))
