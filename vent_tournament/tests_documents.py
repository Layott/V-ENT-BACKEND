"""Entry codes as a file, in the five shapes the spec asks for.

CEO's spec: codes "Downloadable as txt, docx, pdf and xls". The document
formats are premium; txt and csv are not, because a list of codes somebody has
to send out is not a feature to sell.

The tests that matter here are the ones about the BYTES. A file whose contents
do not match its extension makes Excel and Word warn the person who was sent
it, and that person is the organiser's customer.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users
from .models import Tournament, TournamentInvite


def a_user(name, **extra):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('tok-%s' % name)[:16], **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class InviteDownloadTests(TestCase):
    def setUp(self):
        self.owner, self.auth = a_user('dl_owner')
        self.paying, self.paying_auth = a_user('dl_paying', is_premium=True)
        game = Games.objects.get_or_create(game_title='Download Probe')[0]
        now = timezone.now()
        self.free = Tournament.objects.create(
            tournament_title='Download Probe Cup', tournament_creator=self.owner,
            tournament_game=game, tournament_type='online',
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            bracket_type='single_elimination',
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=3), is_draft=False)
        self.paid = Tournament.objects.create(
            tournament_title='Paid Probe Cup', tournament_creator=self.paying,
            tournament_game=game, tournament_type='online',
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            bracket_type='single_elimination',
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=3), is_draft=False)
        for t in (self.free, self.paid):
            for n in range(3):
                TournamentInvite.objects.create(
                    tournament=t, code='CODE%s%d' % (t.pk, n), label='Seat %d' % n)

    def get(self, tournament, kind, auth):
        return self.client.get(
            '/tournament/%s/invites/download/?as=%s' % (tournament.pk, kind),
            **auth)

    # ------------------------------------------------------------------ free

    def test_txt_is_one_code_a_line_and_nothing_else(self):
        """The usual next step is pasting them into a message, so a header
        would be something everybody has to delete."""
        res = self.get(self.free, 'txt', self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        lines = res.content.decode().strip().split('\n')
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(line.startswith('CODE') for line in lines))

    def test_csv_is_free_and_carries_the_columns(self):
        res = self.get(self.free, 'csv', self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn('text/csv', res['Content-Type'])
        self.assertIn(b'Uses allowed', res.content)

    # --------------------------------------------------------------- premium

    def test_a_document_format_is_refused_without_premium(self):
        for kind in ('xlsx', 'docx', 'pdf'):
            res = self.get(self.free, kind, self.auth)
            self.assertEqual(res.status_code, 402, kind)
            self.assertEqual(res.json()['code'], 'PREMIUM_REQUIRED', kind)

    def test_the_refusal_names_the_feature_rather_than_the_price(self):
        res = self.get(self.free, 'pdf', self.auth)
        self.assertEqual(res.json()['data']['feature'], 'ticket_codes')

    # ------------------------------------------------- the bytes, which matter

    def test_xlsx_is_really_a_spreadsheet(self):
        """Not a CSV named .xls. Excel warns loudly when the contents do not
        match the extension, and the person seeing that warning is the
        organiser's customer."""
        res = self.get(self.paid, 'xlsx', self.paying_auth)
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertEqual(res.content[:2], b'PK')          # a zip, as xlsx is
        self.assertIn('spreadsheetml', res['Content-Type'])

    def test_docx_is_really_a_document(self):
        res = self.get(self.paid, 'docx', self.paying_auth)
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertEqual(res.content[:2], b'PK')
        self.assertIn('wordprocessingml', res['Content-Type'])

    def test_pdf_is_really_a_pdf(self):
        res = self.get(self.paid, 'pdf', self.paying_auth)
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertEqual(res.content[:4], b'%PDF')
        self.assertEqual(res['Content-Type'], 'application/pdf')

    def test_xls_means_xlsx_because_that_is_what_people_type(self):
        res = self.get(self.paid, 'xls', self.paying_auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content[:2], b'PK')

    # ------------------------------------------------------------- the edges

    def test_a_format_nobody_can_write_is_refused_by_name(self):
        res = self.get(self.paid, 'wingdings', self.paying_auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'UNKNOWN_FORMAT')

    def test_the_filename_carries_the_slug_not_the_id(self):
        res = self.get(self.paid, 'pdf', self.paying_auth)
        self.assertIn('invite-codes.pdf', res['Content-Disposition'])

    def test_a_stranger_gets_nothing(self):
        other, other_auth = a_user('dl_stranger')
        res = self.get(self.paid, 'csv', other_auth)
        self.assertIn(res.status_code, (401, 403), res.content)

    def test_a_tournament_with_no_codes_still_makes_a_file(self):
        """An empty table is a document that says so, never a 500."""
        TournamentInvite.objects.filter(tournament=self.paid).delete()
        res = self.get(self.paid, 'pdf', self.paying_auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content[:4], b'%PDF')
