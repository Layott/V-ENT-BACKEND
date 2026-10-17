"""Duplicating a tournament (inbox 446).

CEO, 12 October 2026: "users should be able to duplicate their own
tournaments."
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from .models import (LeagueRules, Tournament, TournamentPrizeDistribution,
                     TournamentRegistration, TournamentStage)
from .tests import client_for, make_tournament, make_user, register


class DuplicateTests(TestCase):
    def setUp(self):
        self.owner = make_user(7001)
        self.stranger = make_user(7002)
        self.t = make_tournament(self.owner, bracket_type='league', prize_type='distributed')
        self.t.prize_currency = 'NGN'
        self.t.options = {'legs': 1, 'best_of': 3}
        self.t.save(update_fields=['prize_currency', 'options'])
        TournamentPrizeDistribution.objects.create(
            tournament=self.t, position=1, prize=Decimal('50'),
            amount_original=Decimal('50000'), currency='NGN')
        LeagueRules.objects.create(tournament=self.t, points_win=3, points_draw=1, points_loss=0)
        TournamentStage.objects.create(tournament=self.t, order=0, label='Groups',
                                       format='league', status='completed',
                                       completed_at=timezone.now())
        register(self.t, make_user(7003))

    def press(self, who):
        return client_for(who).post('/tournament/%s/duplicate/' % self.t.slug)

    def test_the_owner_gets_a_new_draft_set_up_the_same(self):
        res = self.press(self.owner)
        self.assertEqual(res.status_code, 201, res.content[:300])
        copy = Tournament.objects.get(slug=res.json()['data']['slug'])
        self.assertNotEqual(copy.pk, self.t.pk)
        self.assertNotEqual(copy.slug, self.t.slug)
        self.assertTrue(copy.tournament_title.endswith('(copy)'))
        self.assertTrue(copy.is_draft)
        self.assertEqual(copy.status, 'draft')
        self.assertEqual(copy.tournament_creator_id, self.owner.user_id)
        self.assertEqual(copy.bracket_type, 'league')
        self.assertEqual(copy.options.get('legs'), 1)
        self.assertEqual(copy.prize_currency, 'NGN')
        row = copy.prize_distributions.get()
        self.assertEqual((row.amount_original, row.currency, row.prize),
                         (Decimal('50000.00'), 'NGN', Decimal('50.00')))
        self.assertEqual(copy.league_rules.points_win, 3)

    def test_what_happened_is_not_copied(self):
        copy = Tournament.objects.get(slug=self.press(self.owner).json()['data']['slug'])
        self.assertFalse(TournamentRegistration.objects.filter(tournament=copy).exists())
        stage = copy.stages.get()
        self.assertEqual(stage.label, 'Groups')
        self.assertNotEqual(stage.status, 'completed')
        self.assertIsNone(stage.completed_at)

    def test_somebody_elses_tournament_is_refused(self):
        res = self.press(self.stranger)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'ONLY_TOURNAMENT_ORGANIZER_CAN')
        self.assertEqual(Tournament.objects.count(), 1)

    def test_signed_out_is_refused(self):
        res = self.client.post('/tournament/%s/duplicate/' % self.t.slug)
        self.assertIn(res.status_code, (401, 403))
        self.assertEqual(Tournament.objects.count(), 1)

    def test_a_long_name_still_fits(self):
        self.t.tournament_title = 'X' * Tournament._meta.get_field('tournament_title').max_length
        self.t.save(update_fields=['tournament_title'])
        copy = Tournament.objects.get(slug=self.press(self.owner).json()['data']['slug'])
        self.assertLessEqual(len(copy.tournament_title),
                             Tournament._meta.get_field('tournament_title').max_length)

    def test_stage_lines_are_copied(self):
        # A copy that kept the stages and lost the lines between them would
        # run groups and a playoff with nothing sending anybody across (P7: a
        # blueprint's editions must "play out exactly like the Blueprint").
        from .models import Progression
        groups = self.t.stages.get()
        playoff = TournamentStage.objects.create(tournament=self.t, order=1, label='Playoff',
                                                 format='single_elimination')
        Progression.objects.create(source=groups, target=playoff, place_from=1, place_to=2,
                                   side='winners', sent=[5, 6], fired_at=timezone.now())
        copy = Tournament.objects.get(slug=self.press(self.owner).json()['data']['slug'])
        line = Progression.objects.get(source__tournament=copy)
        self.assertEqual((line.source.label, line.target.label), ('Groups', 'Playoff'))
        self.assertEqual(line.target.tournament_id, copy.pk)
        self.assertEqual((line.place_from, line.place_to, line.side), (1, 2, 'winners'))
        # Who it sent belongs to the tournament that sent them.
        self.assertEqual(line.sent, [])
        self.assertIsNone(line.fired_at)
        self.assertEqual(Progression.objects.filter(source__tournament=self.t).count(), 1)

    def test_a_copy_is_not_an_edition(self):
        from .models import TournamentBlueprint
        blueprint = TournamentBlueprint.objects.create(owner=self.owner, tournament=self.t, name='Cup')
        Tournament.objects.filter(pk=self.t.pk).update(blueprint=blueprint, blueprint_edition=3)
        copy = Tournament.objects.get(slug=self.press(self.owner).json()['data']['slug'])
        self.assertIsNone(copy.blueprint_id)
        self.assertIsNone(copy.blueprint_edition)
        self.assertTrue(blueprint.token.startswith('bp_'))
