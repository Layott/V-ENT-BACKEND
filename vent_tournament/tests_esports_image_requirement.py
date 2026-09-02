"""An organiser can require an esports picture, and it is refused without one.

CEO, 2 September 2026, on the entry-requirements picker: "has an esport image
should be part of this and if they have not uploaded it should not allow them."

The distinction that matters, and the reason this is not `profile_image`:

- a **profile picture** sits on the profile and goes no further
- an **esports picture** is one the person has RELEASED for organisers to use
  on event and tournament pages

An organiser asking for this wants a picture they may actually put on a bracket,
a player card or a broadcast overlay. So an esports picture with no release
satisfies nothing: the release is the entire point of the requirement, and a
row without `released_at` was never released whatever its kind says.
"""
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import UserGallery, Users
from vent_tournament.requirements import KINDS, PER_MEMBER, check_automatic


def a_user(name):
    return Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True)


def picture(user, kind, released):
    return UserGallery.objects.create(
        user=user, image='gallery/whatever.jpg', kind=kind,
        released_at=timezone.now() if released else None,
        release_terms_version=UserGallery.RELEASE_TERMS_VERSION if released else '')


class EsportsImageRequirementTests(TestCase):
    def setUp(self):
        self.user = a_user('espMe')
        self.req = {'kind': 'esports_image', 'config': {}}

    def check(self, user=None):
        return check_automatic(self.req, user or self.user)

    # ---------------------------------------------------------- the catalogue

    def test_it_is_offered_to_organisers(self):
        """If it is not in KINDS the picker cannot show it, which is exactly
        what the CEO reported."""
        self.assertIn('esports_image', KINDS)
        self.assertEqual(KINDS['esports_image']['check'], 'automatic')

    def test_it_is_checked_per_member_for_a_team(self):
        """It is about a person, so a team satisfies it once per member rather
        than once for whoever pressed the button."""
        self.assertIn('esports_image', PER_MEMBER)

    # ------------------------------------------------------------- refusals

    def test_somebody_with_no_pictures_at_all_is_refused(self):
        ok, message, detail = self.check()
        self.assertFalse(ok)
        self.assertEqual(detail['code'], 'esports_image')
        self.assertIn('esports', message.lower())

    def test_a_personal_picture_does_not_count(self):
        """A profile picture is not a licence to use somebody's face on a
        broadcast graphic."""
        picture(self.user, UserGallery.KIND_PERSONAL, released=False)
        self.assertFalse(self.check()[0])

    def test_an_esports_picture_that_was_never_released_does_not_count(self):
        """The release is the whole point. Without it the organiser has been
        granted nothing, so the requirement is not met."""
        picture(self.user, UserGallery.KIND_ESPORTS, released=False)
        ok, _message, detail = self.check()
        self.assertFalse(ok)
        self.assertEqual(detail['code'], 'esports_image')

    def test_a_released_personal_picture_does_not_count_either(self):
        """Guards the pair being read as an either/or. `is_released` needs
        both halves and so does this."""
        picture(self.user, UserGallery.KIND_PERSONAL, released=True)
        self.assertFalse(self.check()[0])

    # -------------------------------------------------------------- accepted

    def test_a_released_esports_picture_is_accepted(self):
        picture(self.user, UserGallery.KIND_ESPORTS, released=True)
        ok, message, detail = self.check()
        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertIsNone(detail)

    def test_one_released_picture_is_enough(self):
        picture(self.user, UserGallery.KIND_PERSONAL, released=False)
        picture(self.user, UserGallery.KIND_ESPORTS, released=False)
        picture(self.user, UserGallery.KIND_ESPORTS, released=True)
        self.assertTrue(self.check()[0])

    def test_one_persons_picture_does_not_admit_another(self):
        picture(self.user, UserGallery.KIND_ESPORTS, released=True)
        other = a_user('espOther')
        self.assertFalse(self.check(other)[0])
