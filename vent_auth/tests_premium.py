"""Whether an account may use a premium feature.

CEO, 9 September 2026: "A flag admins set, sold later." So the question has one
answer, in one place, and everything that gates on premium asks it. Today the
field is set by an admin; tomorrow a subscription writes the same field, and
these tests should not change when it does. That is the point of them.
"""
from django.test import TestCase
from django.utils import timezone

from vent_auth import premium
from vent_auth.models import Games, Organization, Users
from vent_tournament.models import Tournament


def a_user(name, **extra):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('tok-%s' % name)[:16], **extra)
    user.login_session_created_at = timezone.now()
    user.save()
    return user


class PremiumAnswerTests(TestCase):
    def setUp(self):
        self.plain = a_user('pr_plain')
        self.paying = a_user('pr_paying', is_premium=True)
        self.game = Games.objects.get_or_create(game_title='Premium Probe')[0]

    def a_tournament(self, owner, org=None):
        now = timezone.now()
        from datetime import timedelta
        return Tournament.objects.create(
            tournament_title='Premium Probe Cup %s' % owner.username,
            tournament_creator=owner, tournament_organization=org,
            tournament_game=self.game, tournament_type='online',
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            bracket_type='single_elimination',
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=3), is_draft=False)

    # ------------------------------------------------------------- the answer

    def test_a_plain_account_is_not_premium(self):
        self.assertFalse(premium.has_premium(self.plain))

    def test_a_granted_account_is(self):
        self.assertTrue(premium.has_premium(self.paying))

    def test_a_tournament_answers_for_its_owner(self):
        """Callers hold a tournament, not a user. Making each of them work out
        the owner is how the answer starts differing between screens."""
        self.assertFalse(premium.has_premium(self.a_tournament(self.plain)))
        self.assertTrue(premium.has_premium(self.a_tournament(self.paying)))

    def test_the_organisation_wins_over_the_person(self):
        """Somebody running a tournament for an org that pays is not refused
        because their own account does not."""
        org = Organization.objects.create(
            org_name='Premium Org', org_creator=self.plain, org_owner=self.plain,
            is_premium=True)
        self.assertTrue(premium.has_premium(self.a_tournament(self.plain, org=org)))

    def test_a_plain_org_does_not_take_premium_away_from_its_owner(self):
        """The org is a way to HAVE it, never a way to lose it."""
        org = Organization.objects.create(
            org_name='Plain Org', org_creator=self.paying, org_owner=self.paying)
        self.assertTrue(premium.has_premium(self.a_tournament(self.paying, org=org)))

    def test_an_organisation_asked_about_directly(self):
        org = Organization.objects.create(
            org_name='Direct Org', org_creator=self.plain, org_owner=self.plain,
            is_premium=True)
        self.assertTrue(premium.has_premium(org))

    def test_nothing_is_not_premium(self):
        self.assertFalse(premium.has_premium(None))

    # ------------------------------------------------------------ the refusal

    def test_a_refusal_carries_a_code_not_a_sentence(self):
        body = premium.refuse('ticket_codes')
        self.assertEqual(body['code'], 'PREMIUM_REQUIRED')
        self.assertEqual(body['data']['feature'], 'ticket_codes')

    def test_an_unknown_feature_raises_rather_than_refusing_quietly(self):
        """A typo must not turn a paid feature off for everybody, silently."""
        with self.assertRaises(KeyError):
            premium.refuse('tikcet_codes')

    def test_every_named_feature_has_a_sentence(self):
        for key, text in premium.FEATURES.items():
            self.assertTrue(text.strip(), '%s has no description' % key)
