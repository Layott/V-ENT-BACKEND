"""Character battles: nominating, voting, and the arithmetic behind the winner.

The arithmetic is the part worth testing hardest, because it is the part a
reader will argue with. Every number in the payload is checked against a hand
worked example rather than against the function that produced it.
"""
import uuid

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import UserWallet, Users

from . import battles
from .models import AttributeVote, Battle, BattleCharacter


def make_user(i, *, admin_role=None):
    user = Users.objects.create(
        username='ab%s' % i, email='ab%s@test.co' % i,
        login_session_token='abt%s' % str(i).zfill(11),
        login_session_created_at=timezone.now(), is_active=True,
        is_staff=admin_role is not None, admin_role=admin_role,
        role='admin' if admin_role else 'user')
    if admin_role:
        user.login_session_2fa_at = timezone.now()
        user.save(update_fields=['login_session_2fa_at'])
    UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=user,
                              wallet_balance=0)
    return user


def client_for(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)
    return c


@override_settings(ANIME_ENABLED=True)
class NominatingTests(TestCase):

    def setUp(self):
        self.admin = make_user(1, admin_role='super_admin')
        self.fan = make_user(2)
        self.battle = Battle.objects.create(title='Strongest swordsman',
                                            state='nominating')

    def test_anybody_can_nominate(self):
        res = client_for(self.fan).post(
            '/anime/battles/%s/nominate/' % self.battle.slug,
            {'name': 'Zoro', 'source': 'One Piece'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertFalse(BattleCharacter.objects.get().is_approved)

    def test_a_nomination_nobody_approved_is_not_in_the_battle(self):
        client_for(self.fan).post(
            '/anime/battles/%s/nominate/' % self.battle.slug,
            {'name': 'Zoro'}, format='json')
        res = client_for(self.fan).get('/anime/battles/%s/' % self.battle.slug)
        self.assertEqual(res.data['data']['characters'], [])

    def test_an_admin_decides(self):
        client_for(self.fan).post(
            '/anime/battles/%s/nominate/' % self.battle.slug,
            {'name': 'Zoro'}, format='json')
        res = client_for(self.admin).post(
            '/anime/battles/%s/approve/' % self.battle.slug,
            {'name': 'Zoro', 'approved': True}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(BattleCharacter.objects.get().is_approved)

    def test_the_admin_is_sent_what_is_waiting_and_a_fan_is_not(self):
        client_for(self.fan).post(
            '/anime/battles/%s/nominate/' % self.battle.slug,
            {'name': 'Zoro', 'source': 'One Piece'}, format='json')
        mine = client_for(self.admin).get('/anime/battles/%s/' % self.battle.slug)
        self.assertTrue(mine.data['data']['may_run'])
        self.assertEqual(
            [(p['name'], p['source'], p['nominated_by'])
             for p in mine.data['data']['pending']],
            [('Zoro', 'One Piece', self.fan.username)])
        theirs = client_for(self.fan).get('/anime/battles/%s/' % self.battle.slug)
        self.assertFalse(theirs.data['data']['may_run'])
        self.assertNotIn('pending', theirs.data['data'])

    def test_taking_a_character_out_puts_it_back_on_the_waiting_list(self):
        client_for(self.fan).post(
            '/anime/battles/%s/nominate/' % self.battle.slug,
            {'name': 'Zoro'}, format='json')
        client_for(self.admin).post(
            '/anime/battles/%s/approve/' % self.battle.slug,
            {'name': 'Zoro', 'approved': True}, format='json')
        client_for(self.admin).post(
            '/anime/battles/%s/approve/' % self.battle.slug,
            {'name': 'Zoro', 'approved': False}, format='json')
        # Out of the battle is not deleted: the name goes back to waiting,
        # so a decision made in error can be made again the other way.
        res = client_for(self.admin).get('/anime/battles/%s/' % self.battle.slug)
        self.assertEqual([p['name'] for p in res.data['data']['pending']], ['Zoro'])

    def test_a_fan_cannot_approve(self):
        client_for(self.fan).post(
            '/anime/battles/%s/nominate/' % self.battle.slug,
            {'name': 'Zoro'}, format='json')
        res = client_for(self.fan).post(
            '/anime/battles/%s/approve/' % self.battle.slug,
            {'name': 'Zoro'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_the_same_character_cannot_be_nominated_twice(self):
        client_for(self.fan).post(
            '/anime/battles/%s/nominate/' % self.battle.slug,
            {'name': 'Zoro'}, format='json')
        res = client_for(make_user(3)).post(
            '/anime/battles/%s/nominate/' % self.battle.slug,
            {'name': 'zoro'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'ALREADY_NOMINATED')

    def test_nominations_close_with_the_state(self):
        self.battle.state = 'voting'
        self.battle.save(update_fields=['state'])
        res = client_for(self.fan).post(
            '/anime/battles/%s/nominate/' % self.battle.slug,
            {'name': 'Mihawk'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'NOMINATIONS_CLOSED')

    def test_only_an_admin_creates_a_battle(self):
        res = client_for(self.fan).post('/anime/battles/',
                                        {'title': 'Mine'}, format='json')
        self.assertEqual(res.status_code, 403)
        res = client_for(self.admin).post('/anime/battles/',
                                          {'title': 'Theirs'}, format='json')
        self.assertEqual(res.status_code, 201)


@override_settings(ANIME_ENABLED=True)
class VotingTests(TestCase):

    def setUp(self):
        self.admin = make_user(4, admin_role='super_admin')
        self.fan = make_user(5)
        self.battle = Battle.objects.create(title='Fastest', state='voting')
        self.a = BattleCharacter.objects.create(battle=self.battle,
                                                name='Killua', is_approved=True)
        self.b = BattleCharacter.objects.create(battle=self.battle,
                                                name='Minato', is_approved=True)

    def vote(self, user, character, **scores):
        return client_for(user).post(
            '/anime/battles/%s/vote/' % self.battle.slug,
            {'character': character.name, 'scores': scores}, format='json')

    def test_a_vote_is_recorded_per_attribute(self):
        res = self.vote(self.fan, self.a, speed=9, strength=6)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(AttributeVote.objects.count(), 2)

    def test_a_vote_can_be_changed_until_voting_closes(self):
        self.vote(self.fan, self.a, speed=9)
        self.vote(self.fan, self.a, speed=4)
        self.assertEqual(AttributeVote.objects.count(), 1)
        self.assertEqual(AttributeVote.objects.get().score, 4)

    def test_a_score_outside_the_scale_is_refused(self):
        res = self.vote(self.fan, self.a, speed=11)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'BAD_SCORE')

    def test_an_attribute_that_is_not_in_the_catalogue_is_refused(self):
        res = self.vote(self.fan, self.a, charisma=9)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'BAD_ATTRIBUTE')

    def test_voting_on_a_closed_battle_is_refused(self):
        self.battle.state = 'closed'
        self.battle.save(update_fields=['state'])
        res = self.vote(self.fan, self.a, speed=9)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'VOTING_CLOSED')

    def test_a_stranger_cannot_vote(self):
        res = APIClient().post('/anime/battles/%s/vote/' % self.battle.slug,
                               {'character': self.a.name,
                                'scores': {'speed': 9}}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_a_character_not_in_the_battle_is_a_404(self):
        res = client_for(self.fan).post(
            '/anime/battles/%s/vote/' % self.battle.slug,
            {'character': 'Nobody', 'scores': {'speed': 5}}, format='json')
        self.assertEqual(res.status_code, 404)


@override_settings(ANIME_ENABLED=True)
class TheArithmeticTests(TestCase):
    """Hand worked, so the test disagrees with the code rather than echoing it."""

    def setUp(self):
        self.battle = Battle.objects.create(title='Maths', state='voting')
        self.a = BattleCharacter.objects.create(battle=self.battle, name='A',
                                                is_approved=True)
        self.b = BattleCharacter.objects.create(battle=self.battle, name='B',
                                                is_approved=True)
        self.voters = [make_user(10 + i) for i in range(3)]

    def cast(self, character, attribute, scores):
        for user, score in zip(self.voters, scores):
            AttributeVote.objects.create(character=character, user=user,
                                         attribute=attribute, score=score)

    def test_an_attribute_is_the_mean_of_its_votes(self):
        self.cast(self.a, 'speed', [9, 8, 7])       # mean 8.0
        row = battles.score_character(self.a)
        speed = next(x for x in row['attributes'] if x['key'] == 'speed')
        self.assertEqual(speed['score'], 8.0)
        self.assertEqual(speed['votes'], 3)

    def test_the_mean_is_rounded_to_one_decimal(self):
        self.cast(self.a, 'speed', [9, 8, 8])       # 8.333...
        speed = next(x for x in battles.score_character(self.a)['attributes']
                     if x['key'] == 'speed')
        self.assertEqual(speed['score'], 8.3)

    def test_an_unvoted_attribute_is_zero_and_says_so(self):
        self.cast(self.a, 'speed', [9])
        row = battles.score_character(self.a)
        strength = next(x for x in row['attributes'] if x['key'] == 'strength')
        self.assertEqual(strength['score'], 0.0)
        self.assertEqual(strength['votes'], 0)

    def test_the_total_is_the_sum_of_the_five_means(self):
        self.cast(self.a, 'speed', [10, 10, 10])        # 10
        self.cast(self.a, 'strength', [5, 5, 5])        # 5
        self.assertEqual(battles.score_character(self.a)['total'], 15.0)

    def test_being_voted_on_more_does_not_win_by_itself(self):
        """The sum of votes would reward popularity. The mean does not."""
        for i, user in enumerate(self.voters):
            AttributeVote.objects.create(character=self.a, user=user,
                                         attribute='speed', score=9)
        AttributeVote.objects.create(character=self.b, user=self.voters[0],
                                     attribute='speed', score=10)
        result = battles.decide(self.battle)
        self.assertEqual(result['winner']['name'], 'B')

    def test_equal_totals_is_a_tie_and_a_tie_is_an_answer(self):
        self.cast(self.a, 'speed', [8])
        self.cast(self.b, 'speed', [8])
        result = battles.decide(self.battle)
        self.assertIsNone(result['winner'])
        self.assertEqual(sorted(result['tied']), ['A', 'B'])

    def test_with_no_votes_at_all_there_is_no_winner(self):
        result = battles.decide(self.battle)
        self.assertIsNone(result['winner'])
        self.assertEqual(result['tied'], [])

    def test_the_rule_travels_with_the_numbers(self):
        """So a reader can check the arithmetic rather than trust it."""
        self.assertEqual(battles.decide(self.battle)['rule'],
                         'mean_per_attribute_summed')

    def test_an_unapproved_character_is_not_in_the_result(self):
        BattleCharacter.objects.create(battle=self.battle, name='Ghost',
                                       is_approved=False)
        names = [c['name'] for c in battles.decide(self.battle)['characters']]
        self.assertNotIn('Ghost', names)

    def test_the_result_is_readable_by_anybody(self):
        self.cast(self.a, 'speed', [9])
        res = APIClient().get('/anime/battles/%s/' % self.battle.slug)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['characters'][0]['name'], 'A')


@override_settings(ANIME_ENABLED=True)
class TalkingAboutItTests(TestCase):

    def setUp(self):
        self.fan = make_user(20)
        self.battle = Battle.objects.create(title='Argue', state='closed')

    def test_a_comment_is_attached_to_the_battle(self):
        res = client_for(self.fan).post(
            '/anime/battles/%s/comments/' % self.battle.slug,
            {'body': 'that result is wrong'}, format='json')
        self.assertEqual(res.status_code, 201)
        read = APIClient().get(
            '/anime/battles/%s/comments/' % self.battle.slug)
        self.assertEqual(len(read.data['data']['comments']), 1)

    def test_an_empty_comment_is_refused(self):
        res = client_for(self.fan).post(
            '/anime/battles/%s/comments/' % self.battle.slug, {'body': '  '},
            format='json')
        self.assertEqual(res.status_code, 400)

    def test_a_stranger_can_read_but_not_write(self):
        res = APIClient().post('/anime/battles/%s/comments/' % self.battle.slug,
                               {'body': 'hi'}, format='json')
        self.assertEqual(res.status_code, 401)
        self.assertEqual(
            APIClient().get(
                '/anime/battles/%s/comments/' % self.battle.slug).status_code,
            200)
