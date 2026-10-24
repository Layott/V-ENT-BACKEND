"""A draft is readable by its creator on every read, and by nobody else.

The tournament itself answered the creator, but the participants and bracket
answers kept a flat draft check of their own and refused the creator too, so
the console's bracket tools read "could not be loaded" on every draft (live
walk, 24 October 2026).
"""
from django.test import TestCase
from rest_framework.test import APIClient

from .tests import client_for, make_tournament, make_user

READS = (
    '/tournament/view-tournament/%s/',
    '/tournament/get-tournament-participants/%s/',
    '/tournament/get-tournament-brackets/%s/',
)


class DraftReadsTests(TestCase):
    def setUp(self):
        self.creator = make_user(7100001)
        self.stranger = make_user(7100002)
        self.tournament = make_tournament(self.creator)
        self.tournament.is_draft = True
        self.tournament.save(update_fields=['is_draft'])
        self.ref = self.tournament.slug or self.tournament.tournament_id

    def test_the_creator_reads_every_part_of_a_draft(self):
        for path in READS:
            with self.subTest(path=path):
                res = client_for(self.creator).get(path % self.ref)
                self.assertEqual(res.status_code, 200, res.content[:200])

    def test_a_stranger_and_a_visitor_are_told_it_does_not_exist(self):
        for path in READS:
            for client in (client_for(self.stranger), APIClient()):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path % self.ref).status_code, 404)

    def test_once_published_everybody_reads_it(self):
        self.tournament.is_draft = False
        self.tournament.save(update_fields=['is_draft'])
        for path in READS:
            with self.subTest(path=path):
                self.assertEqual(APIClient().get(path % self.ref).status_code, 200)
