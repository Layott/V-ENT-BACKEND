"""The organiser is described the same way as every other person.

CEO, 2 September 2026: "my name does not show my image or badge".

Two faults, one on each side, and either alone was enough to produce it.

The API hand-built the creator as three keys - user_id, username, full_name -
while every other surface goes through `_person`, which also carries the avatar
and the founder mark. So the page had no picture to draw and nothing telling it
whether to show a badge.

The page then hand-rolled a circle with the first letter of the name in it and
passed `size={0}` to switch UserChip's avatar off, so even a perfect payload
would have changed nothing on screen.

`_person`'s own comment records the previous time this happened: the badge
"showed on a profile and nowhere else" because each surface described a person
in its own way. That is the rule these tests exist to hold - **one description
of a person, shared** - and a hand-built dict of the fields somebody happened
to need is how it gets broken again.
"""
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, UserProfile, Users
from vent_tournament.models import Tournament


class OrganiserShapeTests(TestCase):
    def setUp(self):
        self.organiser = Users.objects.create(
            username='Layott', email='layott@vent.test', full_name='Layott',
            is_active=True)
        UserProfile.objects.create(
            user=self.organiser,
            profile_picture='profile_pictures/layott.png')

        self.game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        self.tournament = Tournament.objects.create(
            tournament_title='Rivalvry Series S2',
            tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=timezone.now() + timezone.timedelta(days=3),
            end_date_and_time=timezone.now() + timezone.timedelta(days=4),
            is_draft=False,
        )

    def creator(self):
        res = self.client.get(
            '/tournament/view-tournament/%s/' % self.tournament.slug)
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        return (data.get('tournament') or data)['tournament_creator']

    # ------------------------------------------------------------ the avatar

    def test_the_organiser_carries_an_avatar(self):
        """The page cannot draw a picture it was never sent, and this key was
        simply absent."""
        creator = self.creator()
        self.assertIn('avatar', creator)
        self.assertTrue(creator['avatar'],
                        'the organiser has a profile picture and it must be reported')

    def test_the_avatar_is_a_url_the_page_can_load(self):
        """A bare storage path renders as a broken image, which looks like a
        missing picture rather than a missing host."""
        avatar = self.creator()['avatar']
        self.assertTrue(avatar.startswith('http'),
                        'expected an absolute URL, got %r' % avatar)

    def test_an_organiser_with_no_picture_is_not_an_error(self):
        other = Users.objects.create(username='NoPic', email='np@vent.test',
                                     full_name='No Pic', is_active=True)
        self.tournament.tournament_creator = other
        self.tournament.save(update_fields=['tournament_creator'])
        creator = self.creator()
        self.assertIn('avatar', creator)

    # ------------------------------------------------------------- the badge

    def test_the_organiser_carries_the_founder_mark(self):
        creator = self.creator()
        self.assertIn('founder_badge', creator)

    def test_a_founder_is_reported_as_one(self):
        self.organiser.is_founder = True
        self.organiser.show_founder_badge = True
        self.organiser.save(update_fields=['is_founder', 'show_founder_badge'])
        self.assertTrue(self.creator()['founder_badge'])

    def test_switching_the_badge_off_switches_it_off_here_too(self):
        """Turning it off in settings has to turn it off everywhere, not just
        on the profile. That was the previous version of this bug."""
        self.organiser.is_founder = True
        self.organiser.show_founder_badge = False
        self.organiser.save(update_fields=['is_founder', 'show_founder_badge'])
        self.assertFalse(self.creator()['founder_badge'])

    def test_somebody_who_is_not_a_founder_has_no_badge(self):
        self.assertFalse(self.creator()['founder_badge'])

    # ------------------------------------------------- one shape, not several

    def test_the_organiser_matches_the_shape_used_everywhere_else(self):
        """The actual rule. A hand-built dict with the fields somebody
        happened to need is how the avatar and the badge went missing, so what
        is asserted is that this IS the shared shape rather than a copy of it
        that has drifted."""
        from vent_auth.views_community import _person

        class FakeRequest:
            def build_absolute_uri(self, url):
                return 'http://testserver%s' % url

        expected = set(_person(FakeRequest(), self.organiser).keys())
        self.assertEqual(set(self.creator().keys()), expected)

    def test_the_keys_the_page_reads_are_all_present(self):
        """Named individually, so a future trim of `_person` cannot silently
        take one of them away."""
        creator = self.creator()
        for key in ('user_id', 'username', 'full_name', 'avatar', 'founder_badge'):
            self.assertIn(key, creator, '%s is read by the organiser card' % key)
