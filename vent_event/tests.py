from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users

from .models import Event


class EventAddressTests(TestCase):
    """Every event carries its readable address wherever it is serialized.

    Every event has had a slug in the database since the slug migration and no
    serializer sent it, so the public listing linked by primary key while
    `my-events` linked by name. Half the site obeying the rule is the version
    that is hardest to notice.
    """

    def setUp(self):
        self.user = Users.objects.create(
            username='addr_owner', email='addr@vent.test')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Lagos Anime Con', creator=self.user, event_type='physical',
            desc='A convention.', entry_fee=0,
            start_date=now + timedelta(days=10),
            end_date=now + timedelta(days=11),
            reg_start_date=now, reg_end_date=now + timedelta(days=9),
        )

    def test_the_listing_sends_the_slug(self):
        res = self.client.get('/event/get-all-events/')
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()['data']['events']
        mine = [r for r in rows if r.get('name') == 'Lagos Anime Con']
        self.assertTrue(mine, 'the event was not in the listing at all')
        self.assertEqual(mine[0]['slug'], 'lagos-anime-con')

    def test_the_detail_sends_the_slug(self):
        res = self.client.get('/event/view-event/lagos-anime-con/')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()['data']
        event = body.get('event', body)
        self.assertEqual(event['slug'], 'lagos-anime-con')

    def test_a_rename_moves_the_address_and_keeps_the_old_one(self):
        self.event.name = 'Lagos Anime Convention'
        self.event.save()
        self.event.refresh_from_db()
        self.assertEqual(self.event.slug, 'lagos-anime-convention')

        # The link somebody shared last month still opens the right page.
        res = self.client.get('/event/view-event/lagos-anime-con/')
        self.assertIn(res.status_code, (200, 301, 302), res.content)
        if res.status_code == 200:
            body = res.json()
            if body.get('status') == 'moved':
                self.assertIn('lagos-anime-convention', body['data']['url'])
            else:
                event = body['data'].get('event', body['data'])
                self.assertEqual(event['slug'], 'lagos-anime-convention')
