"""One list of what happened, sent outside too (design section 14b).

"One event list feeds overlays, the run of show and studios ... P1 publishes
it inside V-ENT, P6 sends it outside." `vent_tournament.happenings.record` is
the one writer; each fact it records reaches the studio games the tournament
runs through, queued in the same transaction and sent after it commits. A
studio's trouble must never cost anybody the fact being recorded.
"""
import json
from unittest import mock

from django.db import transaction
from django.test import TestCase, override_settings

from vent_partners.models import StudioDelivery, StudioTournamentLink
from vent_partners.tests_studio_delivery import POST, PUBLIC, answer, make_game, make_tournament
from vent_tournament import happenings
from vent_tournament.models import Happening


@override_settings(STUDIO_DELIVERY_INLINE=True)
class HappeningsReachStudiosTests(TestCase):
    def setUp(self):
        self.game, self.secret = make_game()
        self.tournament = make_tournament('Studio Cup')

    def connect(self):
        return StudioTournamentLink.objects.create(tournament=self.tournament, game=self.game)

    def test_a_recorded_fact_is_sent_to_the_connected_game(self):
        self.connect()
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(200)) as post:
            with self.captureOnCommitCallbacks(execute=True):
                happenings.record('tournament_won', self.tournament.pk, champion='Side One')
        delivery = StudioDelivery.objects.get(game=self.game, event='tournament_won')
        self.assertEqual(delivery.status, 'delivered')
        sent = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(sent['event'], 'tournament_won')
        self.assertEqual(sent['data']['champion'], 'Side One')
        self.assertEqual(sent['data']['tournament'],
                         {'slug': self.tournament.slug, 'title': 'Studio Cup'})

    def test_a_tournament_with_no_studio_sends_nothing(self):
        with mock.patch(POST) as post:
            happenings.record('tournament_won', self.tournament.pk, champion='Side One')
        post.assert_not_called()
        self.assertFalse(StudioDelivery.objects.exists())
        self.assertTrue(Happening.objects.filter(kind='tournament_won').exists())

    def test_nothing_is_sent_before_the_fact_commits(self):
        self.connect()
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(200)) as post:
            happenings.record('stage_closed', self.tournament.pk)
            post.assert_not_called()
        self.assertEqual(StudioDelivery.objects.get(event='stage_closed').status, 'pending')

    def test_a_fact_rolled_back_takes_its_delivery_with_it(self):
        self.connect()

        class Undo(Exception):
            pass

        with self.assertRaises(Undo):
            with transaction.atomic():
                happenings.record('stage_closed', self.tournament.pk)
                raise Undo()
        self.assertFalse(Happening.objects.exists())
        self.assertFalse(StudioDelivery.objects.exists())

    def test_a_broken_publish_never_stops_the_fact(self):
        self.connect()
        with mock.patch('vent_partners.studio_events.studio_delivery.queue',
                        side_effect=RuntimeError('studio down')):
            row = happenings.record('stage_closed', self.tournament.pk)
        self.assertTrue(Happening.objects.filter(pk=row.pk).exists())
        self.assertFalse(StudioDelivery.objects.exists())

    def test_a_publish_that_raises_anyway_never_stops_the_fact(self):
        self.connect()
        with mock.patch('vent_partners.studio_events.publish', side_effect=RuntimeError('studio down')):
            row = happenings.record('stage_closed', self.tournament.pk)
        self.assertTrue(Happening.objects.filter(pk=row.pk).exists())
