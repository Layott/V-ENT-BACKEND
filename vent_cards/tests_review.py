# -*- coding: utf-8 -*-
"""Submitting a squad, and the organiser accepting or rejecting it.

CEO, 3 September 2026: "there should be a place where the players can like
select the cards they want to submit and submit it, then a place for admins to
accept or reject etc. also a place for admins to set rules for the squads that
the players are submitting to use if not they wont be able to submit."

The last clause is the one with teeth, and it has its own test: with no rules
set, nobody can submit.
"""

from vent_cards import squad_rules as rules_engine
from vent_cards.models import GameCard, Lineup, SquadRules
from vent_cards.tests_lineups import LineupTestCase, a_card


class SubmittingTests(LineupTestCase):

    def rules(self, **fields):
        return self.client.post('/tournament/%s/squad-rules/' % self.ref,
                                data=fields, content_type='application/json',
                                **self.org_auth)

    def submit(self, auth=None):
        return self.client.post('/tournament/%s/lineup/submit/' % self.ref,
                                **(auth or self.auth))

    def review(self, decision, note='', username='ln_player', auth=None):
        return self.client.post(
            '/tournament/%s/lineups/%s/review/' % (self.ref, username),
            data={'decision': decision, 'note': note},
            content_type='application/json', **(auth or self.org_auth))

    # ------------------------------------------------- the rules have teeth

    def test_with_no_rules_set_nobody_can_submit(self):
        """The CEO's own clause: "if not they wont be able to submit"."""
        self.save()
        res = self.submit()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'NO_RULES_SET')
        self.assertEqual(
            Lineup.objects.get(user=self.player).status, Lineup.DRAFT)

    def test_once_the_organiser_sets_rules_a_good_squad_goes_through(self):
        self.rules(max_budget_coins=0)
        self.save()
        res = self.submit()
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['lineup']['status'], 'submitted')

    def test_saving_is_not_submitting(self):
        """A draft is not an answer, and the difference is the whole point."""
        self.rules()
        self.save()
        self.assertEqual(Lineup.objects.get(user=self.player).status, 'draft')

    def test_a_squad_short_of_eleven_cannot_be_submitted(self):
        self.rules()
        self.save([{'slot_index': 0, 'card_id': self.cards[0].id}])
        res = self.submit()
        self.assertEqual(res.json()['code'], 'NOT_ELEVEN')
        self.assertEqual(res.json()['data']['violations'][0]['have'], 1)

    def test_a_squad_over_budget_is_refused_with_the_numbers(self):
        for i, card in enumerate(self.cards[:11]):
            card.price_coins = 100_000
            card.save()
        self.rules(max_budget_coins=500_000)
        self.save()
        res = self.submit()
        self.assertEqual(res.json()['code'], 'OVER_BUDGET')
        violation = res.json()['data']['violations'][0]
        self.assertEqual(violation['spent'], 1_100_000)
        self.assertEqual(violation['over'], 600_000)

    def test_a_nation_quota_is_counted(self):
        for card in self.cards[:3]:
            card.nation = 'Nigeria'
            card.save()
        self.rules(required_nation='Nigeria', min_from_nation=5)
        self.save()
        res = self.submit()
        self.assertEqual(res.json()['code'], 'NOT_ENOUGH_FROM_NATION')
        self.assertEqual(res.json()['data']['violations'][0]['have'], 3)

    def test_a_banned_item_type_is_refused_and_named(self):
        self.cards[4].item_type = 'icon'
        self.cards[4].save()
        self.rules(banned_item_types=['icon'])
        self.save()
        res = self.submit()
        self.assertEqual(res.json()['code'], 'BANNED_ITEM_TYPE')
        self.assertEqual(res.json()['data']['violations'][0]['kinds'], ['icon'])

    def test_a_card_over_the_ceiling_is_named(self):
        self.cards[2].rating = 97
        self.cards[2].name = 'Too Good'
        self.cards[2].save()
        self.rules(max_card_rating=90)
        self.save()
        res = self.submit()
        self.assertEqual(res.json()['code'], 'CARD_TOO_HIGH')
        self.assertIn('Too Good', res.json()['data']['violations'][0]['cards'])

    def test_an_item_type_nobody_has_is_refused_when_setting_the_rules(self):
        res = self.rules(banned_item_types=['unicorn'])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'UNKNOWN_ITEM_TYPE')

    def test_the_player_sees_the_rules_and_where_they_stand(self):
        self.rules(max_budget_coins=500_000, notes='Keep it cheap.')
        self.save()
        body = self.client.get('/tournament/%s/lineup/' % self.ref,
                               **self.auth).json()['data']
        self.assertEqual(body['squad_rules']['max_budget_coins'], 500_000)
        self.assertEqual(body['squad_rules']['notes'], 'Keep it cheap.')
        self.assertIn('spend', body)
        self.assertIsInstance(body['violations'], list)

    def test_anybody_can_read_the_squad_rules(self):
        """A rule nobody can read until they are refused is a trap."""
        self.rules(max_budget_coins=400_000)
        res = self.client.get('/tournament/%s/squad-rules/' % self.ref)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['squad_rules']['max_budget_coins'],
                         400_000)

    def test_only_the_organiser_sets_the_squad_rules(self):
        res = self.client.post('/tournament/%s/squad-rules/' % self.ref,
                               data={'max_budget_coins': 1},
                               content_type='application/json',
                               **self.other_auth)
        self.assertEqual(res.status_code, 403)

    def test_signed_out_cannot_submit(self):
        res = self.client.post('/tournament/%s/lineup/submit/' % self.ref)
        self.assertEqual(res.status_code, 401)

    def test_submitting_after_the_deadline_is_refused(self):
        from datetime import timedelta
        from django.utils import timezone
        self.rules()
        self.save()
        self.client.post('/tournament/%s/lineup-rules/' % self.ref,
                         data={'closes_at': (timezone.now() - timedelta(hours=1)).isoformat()},
                         content_type='application/json', **self.org_auth)
        res = self.submit()
        self.assertEqual(res.json()['code'], 'LINEUPS_CLOSED')

    # ------------------------------------------------------------- reviewing

    def test_the_organiser_accepts_a_squad(self):
        self.rules()
        self.save()
        self.submit()
        res = self.review('accept', 'Looks right.')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['lineup']['status'], 'accepted')

    def test_a_rejection_must_carry_a_reason(self):
        """"No" with nothing after it is a message nobody can act on."""
        self.rules()
        self.save()
        self.submit()
        res = self.review('reject')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'REASON_REQUIRED')

    def test_a_rejection_with_a_reason_goes_through_and_keeps_it(self):
        self.rules()
        self.save()
        self.submit()
        res = self.review('reject', 'Two keepers.')
        self.assertEqual(res.json()['data']['lineup']['status'], 'rejected')
        self.assertEqual(Lineup.objects.get(user=self.player).review_note,
                         'Two keepers.')

    def test_a_draft_cannot_be_reviewed(self):
        self.rules()
        self.save()
        res = self.review('accept')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'NOT_SUBMITTED')

    def test_editing_an_accepted_squad_sends_it_back_to_draft(self):
        """An organiser who accepted eleven cards did not accept these."""
        self.rules()
        self.save()
        self.submit()
        self.review('accept', 'Fine.')
        self.assertEqual(Lineup.objects.get(user=self.player).status, 'accepted')

        self.save([{'slot_index': 0, 'card_id': self.cards[7].id}])
        row = Lineup.objects.get(user=self.player)
        self.assertEqual(row.status, 'draft')
        self.assertIsNone(row.reviewed_by)
        self.assertEqual(row.review_note, '')

    def test_a_stranger_cannot_review_anybody(self):
        self.rules()
        self.save()
        self.submit()
        res = self.review('accept', 'Fine.', auth=self.other_auth)
        self.assertEqual(res.status_code, 403)

    def test_signed_out_cannot_review(self):
        res = self.client.post(
            '/tournament/%s/lineups/ln_player/review/' % self.ref,
            data={'decision': 'accept'}, content_type='application/json')
        self.assertEqual(res.status_code, 401)

    def test_reviewing_somebody_with_no_squad_is_a_clean_404(self):
        res = self.review('accept', 'Fine.', username='ln_other')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'NO_LINEUP')

    def test_a_decision_that_is_neither_is_refused(self):
        self.rules()
        self.save()
        self.submit()
        res = self.review('maybe', 'Hmm.')
        self.assertEqual(res.status_code, 400)

    def test_the_organiser_list_shows_who_has_submitted_and_what_was_decided(self):
        self.rules()
        self.save()
        self.submit()
        self.review('accept', 'Fine.')
        body = self.client.get('/tournament/%s/lineups/' % self.ref).json()['data']
        statuses = [l['status'] for l in body['lineups']]
        self.assertIn('accepted', statuses)


class RulesEngineTests(LineupTestCase):
    """The rules themselves, before any HTTP."""

    def eleven_cards(self, **overrides):
        rows = []
        for i in range(11):
            row = {'slot_index': i, 'nation': 'France', 'item_type': 'gold',
                   'rating': 85, 'price_coins': 10_000, 'name': 'P%d' % i}
            row.update(overrides)
            rows.append(row)
        return rows

    def test_no_rules_means_no_submission(self):
        self.assertEqual(rules_engine.violations(self.eleven_cards(), None),
                         [{'code': 'NO_RULES_SET'}])

    def test_a_clean_squad_has_nothing_wrong_with_it(self):
        rules = SquadRules(max_budget_coins=0)
        self.assertEqual(rules_engine.violations(self.eleven_cards(), rules), [])

    def test_the_bench_does_not_count_against_the_budget(self):
        """A budget is on the eleven, not on everybody you own."""
        rules = SquadRules(max_budget_coins=200_000)
        slots = self.eleven_cards()
        slots.append({'slot_index': 11, 'price_coins': 5_000_000,
                      'nation': 'France', 'item_type': 'gold', 'rating': 99})
        self.assertEqual(rules_engine.violations(slots, rules), [])

    def test_every_broken_rule_is_reported_not_just_the_first(self):
        rules = SquadRules(max_budget_coins=1, required_nation='Nigeria',
                           min_from_nation=3, banned_item_types=['gold'],
                           max_card_rating=50)
        codes = {v['code'] for v in
                 rules_engine.violations(self.eleven_cards(), rules)}
        self.assertEqual(codes, {'OVER_BUDGET', 'NOT_ENOUGH_FROM_NATION',
                                 'BANNED_ITEM_TYPE', 'CARD_TOO_HIGH'})

    def test_a_missing_price_counts_as_nothing_rather_than_breaking(self):
        rules = SquadRules(max_budget_coins=1000)
        slots = self.eleven_cards(price_coins=None)
        self.assertEqual(rules_engine.violations(slots, rules), [])

    def test_the_nation_match_ignores_case_and_spacing(self):
        rules = SquadRules(required_nation='  nigeria ', min_from_nation=11)
        slots = self.eleven_cards(nation='Nigeria')
        self.assertEqual(rules_engine.violations(slots, rules), [])

    def test_spend_is_the_eleven_only(self):
        slots = self.eleven_cards()
        slots.append({'slot_index': 11, 'price_coins': 999})
        self.assertEqual(rules_engine.spend(slots), 110_000)
