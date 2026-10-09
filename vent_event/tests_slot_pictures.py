"""Pictures of a pitch or stall, and the venue layout (inbox 419).

CEO, 8 October 2026: "organizers should be able to upload pictures of how it'll
look and then users should be able to view it."

Checked on the consequence: what a buyer reading the public slot list sees, not
only the status of the upload.
"""
import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from vent_event.models import VendorSlot, VendorSlotPicture
from vent_event.tests_vendor_slots import SlotBase, a_user, auth

MEDIA = tempfile.mkdtemp(prefix='vent-slot-pictures-')


def png(name='pitch.png', colour=(200, 30, 40)):
    buf = io.BytesIO()
    Image.new('RGB', (40, 30), colour).save(buf, 'PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT=MEDIA)
class PicturesTests(SlotBase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def url(self, slot=None, tail='pictures/'):
        return '/event/%s/slots/%d/%s' % (self.event.slug, (slot or self.slot).id, tail)

    def add(self, user=None, file=None):
        return self.client.post(self.url(), {'image': file or png()}, format='multipart',
                                **auth(user or self.organiser))

    def public_slot(self):
        res = self.client.get('/event/%s/slots/' % self.event.slug)
        return res.data['data'], res.data['data']['slots'][0]

    def test_the_organiser_adds_one_and_a_stranger_sees_it(self):
        res = self.add()
        self.assertEqual(res.status_code, 200, res.data)
        _data, slot = self.public_slot()
        self.assertEqual(len(slot['pictures']), 1)
        self.assertIn('/media/slot_pictures/', slot['pictures'][0]['url'])
        # Stored under a random name, never the uploader's.
        self.assertNotIn('pitch', VendorSlotPicture.objects.get().image.name)

    def test_somebody_else_cannot_add_one(self):
        stranger = a_user('nosy')
        self.assertIn(self.add(user=stranger).status_code, (401, 403))
        self.client.credentials()
        res = self.client.post(self.url(), {'image': png()}, format='multipart')
        self.assertIn(res.status_code, (401, 403))
        self.assertEqual(VendorSlotPicture.objects.count(), 0)

    def test_a_file_that_is_not_a_picture_is_refused(self):
        fake = SimpleUploadedFile('pitch.png', b'<script>alert(1)</script>', content_type='image/png')
        res = self.add(file=fake)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'NOT_AN_IMAGE')
        self.assertEqual(VendorSlotPicture.objects.count(), 0)

    def test_six_at_most(self):
        for _ in range(VendorSlotPicture.MAX_PER_SLOT):
            self.assertEqual(self.add().status_code, 200)
        res = self.add()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'TOO_MANY_PICTURES')

    def test_reorder_makes_another_the_cover(self):
        self.add(file=png('a.png')); self.add(file=png('b.png'))
        first, second = list(self.slot.pictures.all())
        res = self.client.post(self.url(tail='pictures/order/'), {'order': [second.id, first.id]},
                               format='json', **auth(self.organiser))
        self.assertEqual(res.status_code, 200, res.data)
        _data, slot = self.public_slot()
        self.assertEqual([p['id'] for p in slot['pictures']], [second.id, first.id])

    def test_reorder_with_a_set_that_is_not_this_pitch_is_refused(self):
        self.add()
        res = self.client.post(self.url(tail='pictures/order/'), {'order': [999999]},
                               format='json', **auth(self.organiser))
        self.assertEqual(res.status_code, 409)

    def test_remove_one(self):
        self.add(file=png('a.png')); self.add(file=png('b.png'))
        first = self.slot.pictures.first()
        res = self.client.delete(self.url(tail='pictures/%d/' % first.id), **auth(self.organiser))
        self.assertEqual(res.status_code, 200)
        _data, slot = self.public_slot()
        self.assertEqual(len(slot['pictures']), 1)
        self.assertEqual(self.slot.pictures.get().position, 0)

    def test_a_picture_from_another_pitch_is_not_found_through_this_one(self):
        other = VendorSlot.objects.create(event=self.event, name='Drinks corner', price_ngn=0, quantity=1)
        self.client.post(self.url(slot=other), {'image': png()}, format='multipart', **auth(self.organiser))
        theirs = other.pictures.get()
        res = self.client.delete(self.url(tail='pictures/%d/' % theirs.id), **auth(self.organiser))
        self.assertEqual(res.status_code, 404)
        self.assertTrue(VendorSlotPicture.objects.filter(id=theirs.id).exists())

    def test_the_venue_layout_is_set_shown_and_removed(self):
        layout = '/event/%s/venue-layout/' % self.event.slug
        res = self.client.post(layout, {'image': png('map.png')}, format='multipart', **auth(self.organiser))
        self.assertEqual(res.status_code, 200, res.data)
        data, _slot = self.public_slot()
        self.assertIn('/media/venue_layouts/', data['venue_layout'])
        self.assertEqual(self.client.delete(layout, **auth(a_user('nosy2'))).status_code, 403)
        self.assertEqual(self.client.delete(layout, **auth(self.organiser)).status_code, 200)
        data, _slot = self.public_slot()
        self.assertIsNone(data['venue_layout'])
