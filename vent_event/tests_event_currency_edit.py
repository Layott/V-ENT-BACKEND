"""An event's currency can change after it is created (CEO, 12 November 2026,
inbox 591: "yes the entire site is meant to be very flexible").

Pinned: the organiser changes it; the tier prices do not move (they are kept in
naira); an empty value is naira rather than a blank code (found on the walk of
12 November, when a picker sent "" and the server stored it); anything that is
not three letters is refused; a stranger may not change it.
"""
from decimal import Decimal

from .models import Event, TicketTier
from .tests_polls import PollBase


class EventCurrencyEditTests(PollBase):
    def edit(self, payload, auth):
        return self.client.put('/event/edit-event/%s/' % self.event.event_id, payload,
                               content_type='application/json', **auth)

    def test_the_organiser_changes_it_and_the_prices_stay(self):
        TicketTier.objects.filter(pk=self.tier.pk).update(price=Decimal('5000'))
        res = self.edit({'currency': 'ghs'}, self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.event.refresh_from_db()
        self.assertEqual(self.event.currency, 'GHS')
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.price, Decimal('5000'))

    def test_an_empty_value_is_naira_not_a_blank_code(self):
        Event.objects.filter(pk=self.event.pk).update(currency='GHS')
        res = self.edit({'currency': ''}, self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.event.refresh_from_db()
        self.assertEqual(self.event.currency, 'NGN')

    def test_not_three_letters_is_refused(self):
        for bad in ('NAIRA', 'N1', '12A'):
            res = self.edit({'currency': bad}, self.auth)
            self.assertEqual(res.status_code, 400, bad)
            self.assertEqual(res.json()['code'], 'INVALID_CURRENCY')

    def test_a_stranger_may_not_change_it(self):
        res = self.edit({'currency': 'GHS'}, self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.event.refresh_from_db()
        self.assertEqual(self.event.currency, 'NGN')
