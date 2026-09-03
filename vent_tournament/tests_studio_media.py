"""The studio's media library, and how a graphic arrives and leaves.

CEO, 3 September 2026: "i want to be able use player brolls on the site if
possible maybe the videos are uploaded to a place in the studio and then can be
called on whenever ... then when those things are needed, can be triggered into
a live overlay. Then a way to set options generally for the overlays like maybe
usong a trigger button, or setting the entry animations for specific elemnts or
if the bg of that overlay should not leave or load in and just be present etc
and still have the same options available for each individual overlay."

Three things are pinned here:

  1. An asset is uploaded once and is then addressable by its id, by a tag, by
     the team it is about, or by the player it is about.
  2. The feed resolves which asset a `media` graphic means, so a browser source
     never makes a second request to turn a word into a URL.
  3. Presentation is two levels: the broadcast's house style, and one graphic's
     own, with the graphic winning, and every value validated at the press that
     set it rather than discovered on air.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, TeamMembers, Teams, Users, UserWallet
from vent_event.models import Event

from . import presentation
from .models import (BroadcastElement, BroadcastSession, StudioAsset, Tournament,
                     TournamentRegistration)


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('m-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(user_wallet_id=('mw%s' % name)[:10], user=user,
                              wallet_balance=0)
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def a_clip(name='walkon.mp4', content=b'\x00\x00\x00\x18ftypmp42'):
    return SimpleUploadedFile(name, content, content_type='video/mp4')


def a_picture(name='crest.png'):
    return SimpleUploadedFile(name, b'\x89PNG\r\n\x1a\n', content_type='image/png')


class PresentationTests(TestCase):
    """The options themselves, before any HTTP."""

    def test_the_house_style_is_the_floor(self):
        self.assertEqual(presentation.resolve(None, None), presentation.DEFAULTS)

    def test_a_graphic_wins_over_the_broadcast(self):
        got = presentation.resolve({'entry': 'fade', 'hold': True},
                                   {'entry': 'slide_left'})
        self.assertEqual(got['entry'], 'slide_left')
        self.assertTrue(got['hold'])       # not overridden, so the house style
        self.assertEqual(got['exit'], presentation.DEFAULTS['exit'])

    def test_an_option_nobody_offers_is_refused_by_name(self):
        with self.assertRaises(presentation.PresentationError) as caught:
            presentation.clean({'entrance': 'rise'})
        self.assertEqual(caught.exception.field, 'entrance')

    def test_a_value_nobody_offers_is_refused_and_names_what_is_allowed(self):
        with self.assertRaises(presentation.PresentationError) as caught:
            presentation.clean({'entry': 'explode'})
        self.assertIn('rise', str(caught.exception))

    def test_a_duration_is_a_whole_number_within_reach(self):
        self.assertEqual(presentation.clean({'duration_ms': '4000'})['duration_ms'], 4000)
        for bad in ('soon', -1, 600001):
            with self.assertRaises(presentation.PresentationError):
                presentation.clean({'duration_ms': bad})

    def test_hold_is_a_yes_or_no(self):
        self.assertIs(presentation.clean({'hold': 1})['hold'], True)
        self.assertIs(presentation.clean({'hold': 0})['hold'], False)


@override_settings(FRONTEND_URL='https://v-ent.co')
class StudioMediaTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('med_org')
        self.stranger, self.stranger_auth = a_user('med_other')
        self.star, _ = a_user('med_star')
        game = Games.objects.create(game_title='EA FC MED')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Media Cup', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=4),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team',
            entry_fee='Free', is_draft=False, bracket_type='round_robin')
        team = Teams.objects.create(
            team_name='Media Alpha', game=game, team_creator=self.organiser,
            team_owner=self.organiser, description='', penalty_points=0,
            number_of_members=1)
        TeamMembers.objects.create(team=team, user=self.star)
        TournamentRegistration.objects.create(
            tournament=self.tournament, team=team, status='confirmed')
        self.ref = self.tournament.slug or self.tournament.tournament_id
        self.url = '/tournament/%s/studio/assets/' % self.ref

    def upload(self, **extra):
        body = {'file': extra.pop('file', a_clip()), 'name': 'Walk-on'}
        body.update(extra)
        return self.client.post(self.url, data=body, **self.auth)

    # -------------------------------------------------------------- upload

    def test_a_clip_is_kept_with_the_words_to_find_it_by(self):
        res = self.upload(tags='walkon, hype', team_tag='ALPHA',
                          player='med_star', duration_ms='4200')
        self.assertEqual(res.status_code, 200, res.content[:300])
        added = res.json()['data']['added']
        self.assertEqual(added['kind'], 'video')
        self.assertEqual(added['name'], 'Walk-on')
        self.assertEqual(added['tags'], ['walkon', 'hype'])
        self.assertEqual(added['team_tag'], 'ALPHA')
        self.assertEqual(added['player'], 'med_star')
        self.assertEqual(added['duration_ms'], 4200)
        self.assertTrue(added['url'])

    def test_a_picture_is_a_picture(self):
        res = self.upload(file=a_picture())
        self.assertEqual(res.json()['data']['added']['kind'], 'image')

    def test_a_file_a_browser_source_cannot_play_is_refused_plainly(self):
        res = self.upload(file=SimpleUploadedFile(
            'rules.pdf', b'%PDF-1.4', content_type='application/pdf'))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'UNSUPPORTED_FILE')
        self.assertIn('mp4', res.json()['message'])

    def test_a_stranger_may_not_upload_or_look(self):
        self.assertEqual(self.upload(**{}).status_code, 200)
        res = self.client.post(self.url, data={'file': a_clip()}, **self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.client.get(self.url, **self.stranger_auth).status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_an_unknown_player_is_refused_rather_than_dropped(self):
        res = self.upload(player='nobody_here')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'USER_NOT_FOUND')

    def test_the_library_reports_what_it_holds(self):
        self.upload()
        body = self.client.get(self.url, **self.auth).json()['data']
        self.assertEqual(len(body['assets']), 1)
        self.assertGreater(body['used_bytes'], 0)
        self.assertGreater(body['limit_bytes'], body['max_file_bytes'])
        self.assertIn('.mp4', body['accepts'])

    def test_removing_one_leaves_the_rest(self):
        self.upload(name='One')
        second = self.upload(name='Two').json()['data']['added']
        res = self.client.delete('%s%s/' % (self.url, second['id']), **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual([a['name'] for a in res.json()['data']['assets']], ['One'])

    # ------------------------------------------------------------ matching

    def test_an_asset_answers_to_its_tag_its_team_and_its_player(self):
        self.upload(tags='walkon', team_tag='ALPHA', player='med_star')
        asset = StudioAsset.objects.get()
        for word in ('walkon', 'WALKON', 'alpha', 'med_star'):
            self.assertTrue(asset.matches(word), word)
        for word in ('', None, 'nothing'):
            self.assertFalse(asset.matches(word), word)

    # ---------------------------------------------------------------- feed

    def start(self):
        return self.client.post('/tournament/%s/studio/sessions/' % self.ref,
                                data={'name': 'Day 1'},
                                content_type='application/json', **self.auth)

    def test_the_feed_resolves_the_clip_by_id_and_by_word(self):
        self.upload(tags='walkon', team_tag='ALPHA')
        asset = StudioAsset.objects.get()
        session = self.start().json()['data']['session']
        token = BroadcastSession.objects.get().token

        def element(payload):
            return self.client.post(
                '/tournament/%s/studio/sessions/%s/element/media/' % (self.ref, session['id']),
                data={'active': True, 'payload': payload},
                content_type='application/json', **self.auth)

        self.assertEqual(element({'asset_id': str(asset.id)}).status_code, 200)
        feed = self.client.get('/studio/%s/feed/' % token).json()['data']
        self.assertEqual(feed['elements']['media']['asset']['id'], asset.id)
        self.assertEqual(len(feed['assets']), 1)

        # By a word instead, which is what an operator reaches for.
        self.assertEqual(element({'asset_id': '', 'tag': 'ALPHA'}).status_code, 200)
        feed = self.client.get('/studio/%s/feed/' % token).json()['data']
        self.assertEqual(feed['elements']['media']['asset']['id'], asset.id)

        # A word nothing answers to leaves the graphic with nothing to draw,
        # rather than drawing somebody else's clip.
        self.assertEqual(element({'asset_id': '', 'tag': 'nothing'}).status_code, 200)
        feed = self.client.get('/studio/%s/feed/' % token).json()['data']
        self.assertIsNone(feed['elements']['media']['asset'])

    def test_media_is_a_graphic_on_both_kinds(self):
        self.assertIn('media', [k for k, _ in BroadcastElement.TOURNAMENT_KINDS])
        self.assertIn('media', [k for k, _ in BroadcastElement.EVENT_KINDS])

    # -------------------------------------------------------- presentation

    def test_the_feed_carries_what_each_graphic_does_already_resolved(self):
        session = self.start().json()['data']['session']
        self.assertEqual(session['defaults']['entry'], 'rise')
        self.assertIn('rise', session['presentation_options']['entrances'])

        # The house style for the whole broadcast.
        res = self.client.post(
            '/tournament/%s/studio/sessions/%s/' % (self.ref, session['id']),
            data={'defaults': {'entry': 'fade', 'hold': True}},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        after = res.json()['data']['session']
        self.assertEqual(after['defaults']['entry'], 'fade')
        self.assertTrue(after['elements']['scorebar']['presentation']['hold'])

        # And one graphic differing from it.
        res = self.client.post(
            '/tournament/%s/studio/sessions/%s/element/scorebar/' % (self.ref, session['id']),
            data={'payload': {'options': {'entry': 'slide_left'}}},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        elements = res.json()['data']['session']['elements']
        self.assertEqual(elements['scorebar']['presentation']['entry'], 'slide_left')
        self.assertTrue(elements['scorebar']['presentation']['hold'])
        self.assertEqual(elements['standings']['presentation']['entry'], 'fade')

    def test_an_option_nobody_offers_is_refused_at_the_press(self):
        session = self.start().json()['data']['session']
        res = self.client.post(
            '/tournament/%s/studio/sessions/%s/element/scorebar/' % (self.ref, session['id']),
            data={'payload': {'options': {'entry': 'explode'}}},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_PRESENTATION')

    # ------------------------------------------------------- named address

    def test_the_url_carries_the_name_and_keeps_the_old_one_working(self):
        session = self.start().json()['data']['session']
        token = BroadcastSession.objects.get().token
        url = session['urls']['scorebar']
        # slug, then the graphic, then the token, which is still the credential.
        self.assertEqual(url, 'https://v-ent.co/studio/%s/scorebar/%s'
                         % (self.tournament.slug, token))
        # The address it used to give out, kept for a source already pasted
        # into a machine at a venue.
        legacy = session['legacy_urls']['scorebar']
        self.assertEqual(legacy, 'https://v-ent.co/studio/%s/scorebar'
                         % BroadcastSession.objects.get().token)


@override_settings(FRONTEND_URL='https://v-ent.co')
class EventStudioMediaTests(TestCase):
    """The same library, on the other kind of thing V-ENT runs."""

    def setUp(self):
        self.organiser, self.auth = a_user('med_ev')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Media Con', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=Decimal('0'), reg_start_date=now,
            reg_end_date=now, event_date=now.date(), start_time=now.time(),
            end_time=now.time(), start_date=now, end_date=now + timedelta(hours=6))
        self.ref = self.event.slug or self.event.event_id

    def test_an_event_studio_keeps_its_own_media(self):
        url = '/event/%s/studio/assets/' % self.ref
        res = self.client.post(url, data={'file': a_picture(), 'name': 'Poster'}, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['added']['name'], 'Poster')
        asset = StudioAsset.objects.get()
        self.assertEqual(asset.event_id, self.event.event_id)
        self.assertIsNone(asset.tournament_id)
        self.assertEqual(asset.owner_kind, 'event')
