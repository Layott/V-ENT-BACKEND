"""Approving and turning down a stall.

"I want to approve each stall before it opens" was a checkbox with no door: a
buyer paid, the stall sat `pending`, and nothing could move it. Written on the
consequence: after approval the buyer can put a product in the stall; after a
rejection they have their coins and the pitch is back on sale.
"""
from decimal import Decimal

from vent_auth.models import Notification, Transaction
from vent_event.models import Vendor, VendorSlot, VendorSlotPurchase

from .tests_vendor_slots import PIN, SlotBase, a_user, auth


class StallReviewTests(SlotBase):
    def setUp(self):
        super().setUp()
        self.slot = VendorSlot.objects.create(
            event=self.event, name='Food stall', price_ngn=Decimal('25000'),
            quantity=2, rules='No open flames.', requires_approval=True)

    def buy(self, buyer=None):
        buyer = buyer or self.buyer
        res = self.client.post(
            '/event/%s/slots/%s/buy/' % (self.event.slug, self.slot.id),
            {'accept_rules': True, 'pin': PIN, 'stall_name': 'Suya Corner'},
            format='json', **auth(buyer))
        self.assertEqual(res.status_code, 201, res.content)
        return Vendor.objects.filter(owner=buyer, event=self.event).order_by('-id').first()

    def decide(self, stall, decision, who=None, **extra):
        return self.client.post(
            '/event/%s/stall/%s/decide/' % (self.event.slug, stall.slug),
            {'decision': decision, **extra}, format='json', **auth(who or self.organiser))

    def test_a_bought_pitch_waits_for_the_organiser(self):
        """Pending means not on the event page and not selling. Stock can
        be prepared meanwhile, which is the point of waiting."""
        stall = self.buy()
        self.assertEqual(stall.status, 'pending')
        res = self.client.post('/event/vendor/%s/products/' % stall.slug,
                               {'name': 'Suya', 'price': 1000, 'stock': 5},
                               format='json', **auth(self.buyer))
        self.assertEqual(res.status_code, 201, res.content)
        product = res.json()['data']['product']['id']
        listed = self.client.get('/event/%s/vendors/' % self.event.slug).json()['data']
        self.assertEqual([v['name'] for v in listed['vendors']], [])
        shopper = a_user('shopper', coins=100)
        for path in ('quote', 'order'):
            res = self.client.post('/event/vendor/%s/%s/' % (stall.slug, path),
                                   {'items': [{'product_id': product, 'quantity': 1}], 'pin': PIN},
                                   format='json', **auth(shopper))
            self.assertEqual((path, res.status_code), (path, 409), res.content)
            self.assertEqual(res.json()['code'], 'VENDOR_PENDING')
        # Approved, it is on the page and the quote goes through.
        self.decide(stall, 'approve')
        listed = self.client.get('/event/%s/vendors/' % self.event.slug).json()['data']
        self.assertEqual([v['name'] for v in listed['vendors']], ['Suya Corner'])
        res = self.client.post('/event/vendor/%s/quote/' % stall.slug,
                               {'items': [{'product_id': product, 'quantity': 1}]},
                               format='json', **auth(shopper))
        self.assertEqual(res.status_code, 200, res.content)

    def test_the_organiser_sees_every_stall_and_how_many_wait(self):
        self.buy()
        Vendor.objects.create(event=self.event, name='By hand', status='approved')
        res = self.client.get('/event/%s/stalls/manage/' % self.event.slug,
                              **auth(self.organiser))
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()['data']
        self.assertEqual(body['pending'], 1)
        names = {r['name']: r for r in body['stalls']}
        self.assertIn('Suya Corner', names)
        self.assertEqual(names['Suya Corner']['purchase']['price_vc'], 25)
        self.assertEqual(names['Suya Corner']['owner'], self.buyer.username)
        self.assertIsNone(names['By hand']['purchase'])

    def test_a_stranger_cannot_see_the_list_or_decide(self):
        stall = self.buy()
        res = self.client.get('/event/%s/stalls/manage/' % self.event.slug,
                              **auth(self.buyer))
        self.assertEqual(res.status_code, 403)
        res = self.decide(stall, 'approve', who=self.buyer)
        self.assertEqual(res.status_code, 403)
        stall.refresh_from_db()
        self.assertEqual(stall.status, 'pending')

    def test_approval_opens_the_stall(self):
        stall = self.buy()
        res = self.decide(stall, 'approve', booth='B-14')
        self.assertEqual(res.status_code, 200, res.content)
        stall.refresh_from_db()
        self.assertEqual((stall.status, stall.booth), ('approved', 'B-14'))
        res = self.client.post('/event/vendor/%s/products/' % stall.slug,
                               {'name': 'Suya', 'price': 1000, 'stock': 5},
                               format='json', **auth(self.buyer))
        self.assertEqual(res.status_code, 201, res.content)
        note = Notification.objects.filter(user=self.buyer).order_by('-id').first()
        self.assertIsNotNone(note)
        self.assertIn('approved', note.title)
        self.assertEqual(note.link, '/my-stalls/%s' % stall.slug)

    def test_rejection_gives_the_coins_back_and_the_pitch_returns(self):
        stall = self.buy()
        self.buyer.wallet.refresh_from_db()
        self.assertEqual(self.buyer.wallet.wallet_balance, 5000 - 25)
        self.organiser.wallet.refresh_from_db()
        self.assertEqual(self.organiser.wallet.wallet_balance, 25)

        res = self.decide(stall, 'reject')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['refunded_vc'], 25)
        self.buyer.wallet.refresh_from_db()
        self.organiser.wallet.refresh_from_db()
        self.assertEqual(self.buyer.wallet.wallet_balance, 5000)
        self.assertEqual(self.organiser.wallet.wallet_balance, 0)
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.sold, 0)
        stall.refresh_from_db()
        self.assertEqual(stall.status, 'closed')
        self.assertEqual(Transaction.objects.filter(type='refund').count(), 2)
        # And the organiser cannot reopen a stall whose coins went back.
        res = self.decide(stall, 'reopen')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'STALL_REFUNDED')
        # They can buy again: the rejection did not leave a ghost claim, and
        # the page no longer says they already have one.
        listed = self.client.get('/event/%s/slots/' % self.event.slug, **auth(self.buyer)).json()['data']
        self.assertFalse(listed['slots'][0]['already_mine'])
        again = self.buy()
        self.assertEqual(again.status, 'pending')
        self.assertEqual(VendorSlotPurchase.objects.filter(buyer=self.buyer).count(), 2)

    def test_rejection_is_refused_when_the_organiser_has_spent_the_money(self):
        stall = self.buy()
        self.organiser.wallet.refresh_from_db()
        self.organiser.wallet.wallet_balance = 3
        self.organiser.wallet.save(update_fields=['wallet_balance'])
        res = self.decide(stall, 'reject')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'CANNOT_REFUND_STALL')
        stall.refresh_from_db()
        self.assertEqual(stall.status, 'pending')
        self.buyer.wallet.refresh_from_db()
        self.assertEqual(self.buyer.wallet.wallet_balance, 5000 - 25)

    def test_a_decision_the_stall_cannot_take_is_refused(self):
        stall = self.buy()
        res = self.decide(stall, 'close')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'WRONG_STATUS')
        res = self.decide(stall, 'sell')
        self.assertEqual(res.status_code, 400)

    def test_close_and_reopen_a_trading_stall_moves_no_money(self):
        stall = self.buy()
        self.decide(stall, 'approve')
        before = Transaction.objects.count()
        res = self.decide(stall, 'close')
        self.assertEqual(res.status_code, 200, res.content)
        stall.refresh_from_db()
        self.assertEqual(stall.status, 'closed')
        res = self.decide(stall, 'reopen')
        self.assertEqual(res.status_code, 200, res.content)
        stall.refresh_from_db()
        self.assertEqual(stall.status, 'approved')
        self.assertEqual(Transaction.objects.count(), before)

    def test_a_stall_added_by_hand_is_turned_down_with_nothing_to_refund(self):
        stall = Vendor.objects.create(event=self.event, name='By hand', status='pending',
                                      owner=a_user('hand'))
        res = self.decide(stall, 'reject')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['refunded_vc'], 0)
