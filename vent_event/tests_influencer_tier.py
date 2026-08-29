"""A ticket type only an influencer's audience can buy.

CEO: "there should also be an option where a ticket is locked behind an
influencers link or if the influencer will have codes attributed to them and so
only those who have those codes, can use them to redeem a ticket."

The tier points at the referral rather than copying its code, so rotating an
influencer's code takes effect everywhere at once. The test that matters most is
the last one: a hidden type that anybody can buy by guessing its id is not
hidden, and there are three separate purchase paths to hold to that.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users, UserWallet

from .models import Event, EventReferral, Ticket, TicketTier


def a_user(name, balance=0):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('i-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('iw%s' % name)[:10], user=user, wallet_balance=balance,
        pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class InfluencerTierTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('inf_org')
        self.buyer, self.buyer_auth = a_user('inf_buyer', balance=100000)
        now = timezone.now()
        self.event = Event.objects.create(
            name='Creator Night', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))

        self.open_tier = TicketTier.objects.create(
            event=self.event, name='General', price=Decimal('0'), quantity=100)
        self.creator = EventReferral.objects.create(
            event=self.event, name='KON10DR', code='KON10DR')
        self.locked = TicketTier.objects.create(
            event=self.event, name='Creator seats', price=Decimal('0'),
            quantity=20, unlocked_by=self.creator)

    def listing(self, code=None):
        url = '/event/%s/ticket-types/' % self.event.event_id
        if code:
            url += '?code=%s' % code
        return self.client.get(url)

    # ------------------------------------------------------------ the lock

    def test_a_locked_tier_is_hidden(self):
        self.assertTrue(self.locked.is_hidden)
        self.assertFalse(self.open_tier.is_hidden)

    def test_it_is_not_on_the_public_list(self):
        # A presale that appears in the public list is not a presale.
        names = [t['name'] for t in self.listing().json()['data']['tiers']]
        self.assertIn('General', names)
        self.assertNotIn('Creator seats', names)

    def test_the_influencer_code_reveals_it(self):
        body = self.listing('KON10DR').json()['data']
        names = [t['name'] for t in body['tiers']]
        self.assertIn('Creator seats', names)
        self.assertIn('Creator seats', body['unlocked'])

    def test_the_code_is_not_case_sensitive(self):
        names = [t['name'] for t in self.listing('kon10dr').json()['data']['tiers']]
        self.assertIn('Creator seats', names)

    def test_somebody_elses_code_does_not_open_it(self):
        other = EventReferral.objects.create(
            event=self.event, name='Someone else', code='OTHER')
        names = [t['name'] for t in self.listing(other.code).json()['data']['tiers']]
        self.assertNotIn('Creator seats', names)

    def test_rotating_the_code_takes_effect_at_once(self):
        # The tier points at the referral rather than copying its code, so the
        # old code stops working the moment it is rotated.
        self.creator.code = 'KON10DR-S2'
        self.creator.save()
        self.assertNotIn('Creator seats',
                         [t['name'] for t in self.listing('KON10DR').json()['data']['tiers']])
        self.assertIn('Creator seats',
                      [t['name'] for t in self.listing('KON10DR-S2').json()['data']['tiers']])

    def test_deactivating_the_influencer_closes_the_tier(self):
        self.creator.is_active = False
        self.creator.save()
        self.assertNotIn('Creator seats',
                         [t['name'] for t in self.listing('KON10DR').json()['data']['tiers']])

    def test_their_code_is_never_published(self):
        # The influencer's name may appear; their code is the key.
        body = self.listing('KON10DR').json()['data']
        row = next(t for t in body['tiers'] if t['name'] == 'Creator seats')
        self.assertEqual(row['unlocked_by']['name'], 'KON10DR')
        self.assertNotIn('code', row)
        self.assertNotIn('access_code', row)

    # -------------------------------------------- every way in is held to it

    def test_a_signed_in_buyer_without_the_code_is_refused(self):
        res = self.client.post(
            '/event/%s/buy-ticket/' % self.event.event_id,
            data={'tier_id': self.locked.id, 'quantity': 1, 'pin': '1234'},
            content_type='application/json', **self.buyer_auth)
        self.assertEqual(res.status_code, 403, res.json())
        self.assertEqual(res.json()['code'], 'CODE_REQUIRED')

    def test_a_signed_in_buyer_with_the_code_gets_in(self):
        res = self.client.post(
            '/event/%s/buy-ticket/' % self.event.event_id,
            data={'tier_id': self.locked.id, 'quantity': 1, 'pin': '1234',
                  'code': 'KON10DR'},
            content_type='application/json', **self.buyer_auth)
        self.assertEqual(res.status_code, 201, res.json())

    def test_a_guest_without_the_code_is_refused(self):
        # The third way in. A hidden type anybody can buy by guessing its id is
        # not hidden, and the guest checkout is a separate gate.
        res = self.client.post(
            '/event/%s/guest-buy/' % self.event.event_id,
            data={'tier_id': self.locked.id, 'quantity': 1,
                  'email': 'ada@example.test'},
            content_type='application/json')
        self.assertEqual(res.status_code, 403, res.json())

    def test_a_guest_with_the_code_gets_in(self):
        res = self.client.post(
            '/event/%s/guest-buy/' % self.event.event_id,
            data={'tier_id': self.locked.id, 'quantity': 1,
                  'email': 'ada@example.test', 'code': 'KON10DR'},
            content_type='application/json')
        self.assertEqual(res.status_code, 201, res.json())
        self.assertTrue(Ticket.objects.filter(tier=self.locked).exists())

    # ------------------------------------------------------- the organiser

    def test_the_organiser_attaches_a_tier_to_an_influencer(self):
        res = self.client.patch(
            '/event/%s/tiers/%s/' % (self.event.event_id, self.open_tier.id),
            data={'unlocked_by': self.creator.pk},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        self.open_tier.refresh_from_db()
        self.assertEqual(self.open_tier.unlocked_by_id, self.creator.pk)

    def test_the_organiser_takes_it_back_off_them(self):
        res = self.client.patch(
            '/event/%s/tiers/%s/' % (self.event.event_id, self.locked.id),
            data={'unlocked_by': ''},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        self.locked.refresh_from_db()
        self.assertIsNone(self.locked.unlocked_by_id)

    def test_an_influencer_from_another_event_is_refused(self):
        now = timezone.now()
        other_event = Event.objects.create(
            name='Elsewhere', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0, start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))
        theirs = EventReferral.objects.create(
            event=other_event, name='Not ours', code='NOPE')
        res = self.client.patch(
            '/event/%s/tiers/%s/' % (self.event.event_id, self.open_tier.id),
            data={'unlocked_by': theirs.pk},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 404)
        self.open_tier.refresh_from_db()
        self.assertIsNone(self.open_tier.unlocked_by_id)
