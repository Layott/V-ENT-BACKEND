"""The three sheets, as documents somebody can send.

A spreadsheet is for working on and a document is for sending: to a sponsor, to
a venue, to a federation. The spec asks for both and the platform had only the
first.

These tests are about the BYTES. A file whose contents do not match its
extension makes Word or Excel warn the person who was sent it, and that person
is the organiser's customer rather than ours.
"""
from django.test import TestCase

from .tests import client_for, make_tournament, make_user, register


class ExportFormatTests(TestCase):

    def setUp(self):
        self.owner = make_user(300)
        self.tournament = make_tournament(self.owner)
        register(self.tournament, make_user(301))
        register(self.tournament, make_user(302))
        self.client = client_for(self.owner)
        self.ref = self.tournament.tournament_id

    def _get(self, **params):
        query = '&'.join('%s=%s' % (k, v) for k, v in params.items())
        return self.client.get('/tournament/%s/export/?%s' % (self.ref, query))

    def _premium(self):
        self.owner.is_premium = True
        self.owner.save(update_fields=['is_premium'])

    # ------------------------------------------------------------ free
    def test_csv_is_still_the_default_and_still_free(self):
        res = self._get(sheet='participants')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res['Content-Type'].startswith('text/csv'))
        body = res.content.decode()
        self.assertTrue(body.startswith('registration_id,'))
        # Not a quoted JSON string, which is what a DRF Response would have
        # made of it. There is a test in this repo for that exact fault.
        self.assertNotIn('\\r\\n', body)

    def test_the_filename_says_what_it_is(self):
        res = self._get(sheet='standings')
        self.assertIn('standings.csv', res['Content-Disposition'])

    # --------------------------------------------------------- premium
    def test_a_document_is_refused_without_premium(self):
        for wanted in ('pdf', 'docx', 'xlsx'):
            res = self._get(sheet='participants', **{'as': wanted})
            self.assertEqual(res.status_code, 402, wanted)
            self.assertEqual(res.data['code'], 'PREMIUM_REQUIRED')

    def test_pdf_starts_with_pdf(self):
        self._premium()
        res = self._get(sheet='participants', **{'as': 'pdf'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertTrue(res.content.startswith(b'%PDF'))

    def test_docx_and_xlsx_are_real_office_files(self):
        self._premium()
        for wanted, marker in (('docx', 'wordprocessingml'),
                               ('xlsx', 'spreadsheetml')):
            res = self._get(sheet='results', **{'as': wanted})
            self.assertEqual(res.status_code, 200, wanted)
            self.assertIn(marker, res['Content-Type'])
            # Both are zip containers, so both begin PK.
            self.assertTrue(res.content.startswith(b'PK'), wanted)

    def test_what_people_type_reaches_what_they_meant(self):
        self._premium()
        self.assertIn('spreadsheetml',
                      self._get(sheet='standings', **{'as': 'xls'})['Content-Type'])
        self.assertIn('wordprocessingml',
                      self._get(sheet='standings', **{'as': 'doc'})['Content-Type'])

    def test_every_sheet_can_be_a_document(self):
        self._premium()
        for sheet in ('participants', 'results', 'standings'):
            res = self._get(sheet=sheet, **{'as': 'pdf'})
            self.assertEqual(res.status_code, 200, sheet)
            self.assertTrue(res.content.startswith(b'%PDF'), sheet)
            self.assertIn('%s.pdf' % sheet, res['Content-Disposition'])

    # --------------------------------------------------------- refusals
    def test_a_format_that_does_not_exist_is_refused_by_name(self):
        res = self._get(sheet='participants', **{'as': 'rtf'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'UNKNOWN_FORMAT')

    def test_a_sheet_of_results_is_not_a_list_of_lines(self):
        """txt is a real format in `documents`, and the wrong one here."""
        res = self._get(sheet='results', **{'as': 'txt'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'UNKNOWN_FORMAT')

    def test_it_is_still_the_organisers_export_only(self):
        stranger = client_for(make_user(310))
        res = stranger.get('/tournament/%s/export/?sheet=participants' % self.ref)
        self.assertEqual(res.status_code, 403)
