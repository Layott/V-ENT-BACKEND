"""The picture beside a name on the leaderboard.

CEO, 7 September 2026: "Even the logos and banners of organizations dont load
up there."

The rankings endpoint built its organisation rows with `None` where the crest
belonged, so the whole organisations tab drew blank circles while the SAME
organisations showed their crest on `/organization/list/` and on their own
profile. One concept, two answers, which is the fault this codebase keeps
producing.

Measured on production while this was written: `/ranking/` returned
`avatar: null` for CADE ESPORTS while `/organization/list/` returned
`https://api.v-ent.co/media/org_logos/1cade_esport.png` for the same row, and
that file answers 200.

So the tests here are not "does the field exist". They are:

1. An organisation WITH a logo carries a URL a browser can load, absolute, so
   it still works when the page is served from a different host than the API.
2. An organisation WITHOUT one carries `None` - the shape `Avatar` turns into
   initials. Not `''`, which is falsy but is also a same-document URL, and not
   `/media/` with nothing after it.
3. The rankings row and the organisations list AGREE. This is the assertion
   that would have failed on the reported bug, and it is the one that keeps
   failing if a future change teaches only one of the two screens about a new
   picture.
4. The same three things for teams and for people, because a leaderboard has
   three tabs and only one of them was reported.
"""
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Games, Organization, Teams, UserProfile, Users

# Uploads go to a throwaway directory, not to the real one.
#
# `media/org_logos/` on this machine holds 216 one-pixel PNGs, every one of them
# left behind by a test run that used the live MEDIA_ROOT. They are never
# deleted because nothing knows which rows still point at them, and they make it
# genuinely hard to tell a real crest from the litter when you are trying to
# work out why a picture will not draw.
MEDIA_FOR_THESE_TESTS = tempfile.mkdtemp(prefix='vent-rankings-media-')


def a_png():
    """The smallest valid PNG, so ImageField's verification passes."""
    return (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00'
            b'\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc'
            b'\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')


def an_upload(name):
    return SimpleUploadedFile(name, a_png(), content_type='image/png')


@override_settings(MEDIA_ROOT=MEDIA_FOR_THESE_TESTS)
class RankingsMediaTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_FOR_THESE_TESTS, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client = APIClient()

        self.owner = Users.objects.create(
            username='rkmedia_owner', email='rkmedia_owner@vent.test',
            country='Nigeria', is_active=True)

        # An organisation with a crest, and one without. Both are real cases:
        # of the three organisations on production, one has a logo and two
        # have never uploaded anything.
        self.with_logo = Organization.objects.create(
            org_name='Rankings Media With Logo', org_creator=self.owner,
            org_owner=self.owner, logo=an_upload('rkm-logo.png'),
            banner=an_upload('rkm-banner.png'))
        self.without_logo = Organization.objects.create(
            org_name='Rankings Media Without Logo', org_creator=self.owner,
            org_owner=self.owner)

        game = Games.objects.create(game_title='Rankings Media Game')
        self.team_with_logo = Teams.objects.create(
            team_name='Rankings Media Crested', game=game,
            team_creator=self.owner, team_owner=self.owner,
            team_logo=an_upload('rkm-team.png'))
        self.team_without_logo = Teams.objects.create(
            team_name='Rankings Media Bare', game=game,
            team_creator=self.owner, team_owner=self.owner)

        self.person_with_picture = Users.objects.create(
            username='rkmedia_face', email='rkmedia_face@vent.test',
            country='Nigeria', is_active=True)
        UserProfile.objects.create(
            user=self.person_with_picture,
            profile_picture=an_upload('rkm-face.png'))
        self.person_without_picture = Users.objects.create(
            username='rkmedia_nofacce', email='rkmedia_noface@vent.test',
            country='Nigeria', is_active=True)

    # -- helpers ----------------------------------------------------------

    def rows(self, tab):
        res = self.client.get('/ranking/')
        self.assertEqual(res.status_code, 200, res.content[:300])
        return {r['name']: r for r in res.json()['data'][tab]}

    def assertLoadable(self, value, who):
        """A value a browser can actually fetch.

        `''` and `None` are both falsy in Python and mean opposite things to a
        browser: React drops a null `src` and draws nothing, but an empty
        string is resolved against the current document, so the page requests
        ITSELF and Chrome draws the torn-picture glyph. Anything relative has
        the same problem once the page and the API are on different hosts,
        which they are in production.
        """
        self.assertIsNotNone(value, '%s carries no picture at all' % who)
        self.assertTrue(value, '%s carries an empty string, not a URL' % who)
        self.assertTrue(
            value.startswith('http://') or value.startswith('https://'),
            '%s carries a relative path (%r), which resolves against the page '
            'host rather than the API host' % (who, value))
        self.assertNotIn(' ', value, '%s carries a URL with a space in it' % who)
        self.assertTrue(value.rsplit('/', 1)[-1],
                        '%s carries a directory, not a file' % who)

    # -- organisations ----------------------------------------------------

    def test_an_organisation_with_a_logo_carries_a_loadable_url(self):
        row = self.rows('organizations')['Rankings Media With Logo']
        self.assertLoadable(row['avatar'], 'An organisation with a logo')
        self.assertIn('/media/org_logos/', row['avatar'])

    def test_an_organisation_with_no_logo_carries_the_fallback_shape(self):
        """None, so `Avatar` draws initials.

        Not `''`. An empty src makes the browser refetch the document and draw
        a broken image, which is the one outcome worse than showing nothing.
        """
        row = self.rows('organizations')['Rankings Media Without Logo']
        self.assertIsNone(row['avatar'])

    def test_the_leaderboard_and_the_organisations_list_agree(self):
        """The reported bug, as one assertion.

        `/ranking/` said null and `/organization/list/` said a URL, for the
        same organisation, on the same day. Whichever way a future change goes,
        these two must move together.
        """
        listed = {o['name']: o for o in
                  self.client.get('/organization/list/').json()['data']['organizations']}

        for name in ('Rankings Media With Logo', 'Rankings Media Without Logo'):
            ranked = self.rows('organizations')[name]
            self.assertEqual(
                ranked['avatar'], listed[name]['logo'],
                'The leaderboard and the organisations list disagree about '
                'the crest for %s' % name)

    # -- teams and people, the tabs nobody reported ------------------------

    def test_a_team_with_a_crest_carries_a_loadable_url(self):
        row = self.rows('teams')['Rankings Media Crested']
        self.assertLoadable(row['avatar'], 'A team with a crest')
        self.assertIn('/media/', row['avatar'])

    def test_a_team_with_no_crest_carries_the_fallback_shape(self):
        self.assertIsNone(self.rows('teams')['Rankings Media Bare']['avatar'])

    def test_somebody_with_a_picture_carries_a_loadable_url(self):
        row = self.rows('players')['rkmedia_face']
        self.assertLoadable(row['avatar'], 'A player with a picture')
        self.assertIn('/media/profile_pictures/', row['avatar'])

    def test_somebody_with_no_picture_carries_the_fallback_shape(self):
        self.assertIsNone(self.rows('players')['rkmedia_nofacce']['avatar'])

    def test_no_row_on_any_tab_carries_an_empty_string(self):
        """The shape that draws a broken glyph, swept across all three tabs.

        A per-row check catches the organisation that was reported. This
        catches the next one, on whichever tab it appears, including rows
        added by a future change.
        """
        res = self.client.get('/ranking/')
        data = res.json()['data']
        for tab in ('players', 'teams', 'organizations'):
            for row in data[tab]:
                self.assertNotEqual(
                    row['avatar'], '',
                    'A %s row (%s) carries an empty picture URL, which makes '
                    'the browser refetch the page as an image'
                    % (tab, row['name']))
                if row['avatar'] is not None:
                    self.assertLoadable(row['avatar'],
                                        'A %s row (%s)' % (tab, row['name']))
