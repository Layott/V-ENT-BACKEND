"""The battle royale doors, as every role that reaches them.

Signed out, a stranger, a seated player, a scorekeeper, the organiser and an
admin each get exactly what `access.py` says: the page for everybody, room
codes for the lobby and staff, results for anybody who may record them, the
shape of the stage for the organiser.
"""
import io
import os
import shutil
import tempfile
from unittest import mock

from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from . import br_engine, br_ocr, stage_settings
from .models import BRMap, BRNameAlias, BROcrImage, BRResult, TournamentStaff
from .tests import client_for, make_user
from .tests_stage_engine import add_stage, draw, field


_counter = [0]


def _n():
    _counter[0] += 1
    return _counter[0]


def png(size=(40, 30)):
    buf = io.BytesIO()
    Image.new('RGB', size, (20, 30, 40)).save(buf, format='PNG')
    return buf.getvalue()


class DoorsBase(TestCase):
    def setUp(self):
        self.t, self.org, self.regs = field(4, 'battle_royale')
        self.stage = br_engine.ensure_stage(self.t)
        self.stage.settings = stage_settings.clean('battle_royale',
                                                   {'lobby_size': 2, 'maps': 2})
        self.stage.save(update_fields=['settings'])
        draw(self.stage, self.org)
        self.lobby1 = self.stage.br_lobbies.get(number=1)
        self.m1 = self.lobby1.maps.get(number=1)
        self.m1.room_code = 'ROOM-77'
        self.m1.room_password = 'pw9'
        self.m1.save(update_fields=['room_code', 'room_password'])
        self.seated = [s.registration for s in self.lobby1.seats.all()]
        self.other = next(r for r in self.regs if r not in self.seated)
        self.keeper = make_user(9_900_001 + 10 * _n())
        TournamentStaff.objects.create(tournament=self.t, user=self.keeper)
        self.stranger = make_user(9_900_002 + 10 * _n())
        self.admin = make_user(9_900_003 + 10 * _n(), staff=True)
        self.base = '/tournament/%s/br/' % self.t.tournament_id

    def rows(self):
        a, b = self.seated
        return {'rows': [{'registration_id': a.id, 'placement': 1, 'kills': 3},
                         {'registration_id': b.id, 'placement': 2, 'kills': 1}]}


class ReadingTests(DoorsBase):
    def lobby(self, data):
        return next(l for l in data['current']['lobbies'] if l['number'] == 1)

    def test_signed_out_reads_the_page_without_room_codes(self):
        resp = APIClient().get(self.base)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        m = self.lobby(data)['maps'][0]
        self.assertIsNone(m['room_code'])
        self.assertTrue(m['has_room'])
        self.assertFalse(data['current']['can_record'])
        self.assertIsNone(self.lobby(data)['seats'][0]['roster'])

    def test_a_seated_player_sees_their_lobby_room_and_not_another(self):
        data = client_for(self.seated[0].user).get(self.base).json()['data']
        self.assertEqual(self.lobby(data)['maps'][0]['room_code'], 'ROOM-77')
        self.assertTrue(self.lobby(data)['seated_here'])
        data = client_for(self.other.user).get(self.base).json()['data']
        self.assertIsNone(self.lobby(data)['maps'][0]['room_code'])

    def test_staff_see_rooms_and_rosters(self):
        for who in (self.org, self.keeper):
            data = client_for(who).get(self.base).json()['data']
            self.assertEqual(self.lobby(data)['maps'][0]['room_code'], 'ROOM-77')
            self.assertIsNotNone(self.lobby(data)['seats'][0]['roster'])

    def test_a_one_format_tournament_before_its_stage_exists(self):
        t, org, regs = field(3, 'battle_royale')
        data = APIClient().get('/tournament/%s/br/' % t.tournament_id).json()['data']
        self.assertTrue(data['is_battle_royale'])
        self.assertEqual(data['settings']['lobby_size'], 12)
        self.assertEqual(t.stages.count(), 0)


class WritingTests(DoorsBase):
    def url(self, suffix):
        return self.base + suffix

    def test_results_by_role(self):
        results = self.url('matches/%s/results/' % self.m1.id)
        self.assertEqual(APIClient().post(results, self.rows(), format='json').status_code, 401)
        self.assertEqual(client_for(self.stranger).post(results, self.rows(), format='json')
                         .status_code, 403)
        self.assertEqual(client_for(self.seated[0].user).post(results, self.rows(), format='json')
                         .status_code, 403)
        self.assertEqual(client_for(self.keeper).post(results, self.rows(), format='json')
                         .status_code, 200)
        resp = client_for(self.org).post(results, self.rows(), format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(BRResult.objects.filter(map=self.m1).count(), 2)

    def test_a_refusal_carries_a_code(self):
        a, b = self.seated
        resp = client_for(self.org).post(
            self.url('matches/%s/results/' % self.m1.id),
            {'rows': [{'registration_id': a.id, 'placement': 1},
                      {'registration_id': b.id, 'placement': 1}]}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 'PLACEMENT_TWICE')

    def test_the_scorekeeper_cannot_change_the_shape(self):
        for path, body, method in (
                ('%s/settings/' % self.stage.id, {'lobby_size': 2}, 'put'),
                ('%s/seats/move/' % self.stage.id, {'registration_id': self.seated[0].id, 'lobby': 2}, 'post'),
                ('%s/lobbies/%s/matches/' % (self.stage.id, self.lobby1.id), {}, 'post'),
                ('%s/finish/' % self.stage.id, {}, 'post')):
            resp = getattr(client_for(self.keeper), method)(self.url(path), body, format='json')
            self.assertEqual(resp.status_code, 403, path)

    def test_the_scorekeeper_may_set_the_room(self):
        resp = client_for(self.keeper).patch(self.url('matches/%s/' % self.m1.id),
                                             {'room_code': 'NEW1', 'map_name': 'Bermuda'},
                                             format='json')
        self.assertEqual(resp.status_code, 200)
        self.m1.refresh_from_db()
        self.assertEqual((self.m1.room_code, self.m1.map_name), ('NEW1', 'Bermuda'))

    def test_settings_lock_once_played_except_for_an_admin(self):
        def may_change(who):
            return client_for(who).get(self.base).json()['data']['current']['may_change_settings']

        # The screen is told the same answer the save gives (walk, 28 September 2026).
        self.assertTrue(may_change(self.org))
        self.assertFalse(may_change(self.keeper))
        self.assertFalse(may_change(self.stranger))
        client_for(self.org).post(self.url('matches/%s/results/' % self.m1.id), self.rows(),
                                  format='json')
        self.assertFalse(may_change(self.org))
        with mock.patch('vent_tournament.views_br.may_override', return_value=True), \
                mock.patch('vent_tournament.access.may_override', return_value=True):
            self.assertTrue(may_change(self.admin))
        body = {'placement_preset': 'pubg_mobile', 'lobby_size': 2, 'maps': 2}
        resp = client_for(self.org).put(self.url('%s/settings/' % self.stage.id), body,
                                        format='json')
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()['code'], 'RESULTS_ALREADY_RECORDED')
        with mock.patch('vent_tournament.views_br.may_override', return_value=True), \
                mock.patch('vent_tournament.access.may_override', return_value=True):
            resp = client_for(self.admin).put(self.url('%s/settings/' % self.stage.id), body,
                                              format='json')
        self.assertEqual(resp.status_code, 200, resp.json())
        self.assertEqual(resp.json()['data']['rescored'], 2)

    def test_the_organiser_adds_and_removes_a_match(self):
        resp = client_for(self.org).post(
            self.url('%s/lobbies/%s/matches/' % (self.stage.id, self.lobby1.id)))
        self.assertEqual(resp.status_code, 201)
        new = self.lobby1.maps.order_by('-number').first()
        self.assertEqual(new.number, 3)
        resp = client_for(self.org).delete(self.url('matches/%s/' % self.m1.id))
        self.assertEqual(resp.json()['code'], 'ONLY_THE_LAST_MATCH')
        resp = client_for(self.org).delete(self.url('matches/%s/' % new.id))
        self.assertEqual(resp.status_code, 200)

    def test_finish_writes_places(self):
        for m in BRMap.objects.filter(lobby__stage=self.stage):
            seated = [s.registration for s in m.lobby.seats.all()]
            br_engine.enter_results(m, [{'registration_id': r.id, 'placement': i + 1}
                                        for i, r in enumerate(seated)], self.org)
        resp = client_for(self.org).post(self.url('%s/finish/' % self.stage.id))
        self.assertEqual(resp.status_code, 200, resp.json())
        self.t.refresh_from_db()
        self.assertEqual(self.t.status, 'completed')
        again = client_for(self.org).post(self.url('%s/finish/' % self.stage.id))
        self.assertEqual(again.json()['code'], 'ALREADY_FINISHED')

    def test_another_tournaments_match_is_not_found(self):
        t2, org2, regs2 = field(2, 'battle_royale')
        resp = client_for(org2).post('/tournament/%s/br/matches/%s/results/'
                                     % (t2.tournament_id, self.m1.id), self.rows(), format='json')
        self.assertEqual(resp.status_code, 404)


class ReadingScreenshotsTests(DoorsBase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        field_ = BROcrImage._meta.get_field('file')
        self._storage = field_.storage
        field_.storage = FileSystemStorage(location=self.tmp)

    def tearDown(self):
        BROcrImage._meta.get_field('file').storage = self._storage
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def upload(self, who, files=None):
        files = files if files is not None else [
            SimpleUploadedFile('shot.png', png(), content_type='image/png')]
        return client_for(who).post(self.base + 'matches/%s/read/' % self.m1.id,
                                    {'images': files}, format='multipart')

    def test_without_a_key_the_door_says_so(self):
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': '', 'BR_OCR_ENGINE': ''}):
            resp = self.upload(self.org)
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()['code'], 'OCR_NOT_CONFIGURED')

    @override_settings(DEBUG=True, BR_OCR_SYNC=True)
    def test_read_review_commit_and_learn(self):
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': '', 'BR_OCR_ENGINE': 'local_test'}):
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.upload(self.keeper)
            self.assertEqual(resp.status_code, 202, resp.json())
            job_id = resp.json()['data']['job']['id']
            job = client_for(self.keeper).get(self.base + 'reads/%s/' % job_id).json()['data']['job']
        self.assertEqual(job['status'], 'ready')
        self.assertEqual(job['engine'], 'local_test')
        rows = job['rows']
        self.assertEqual(len(rows), 2)
        # The local reader drops each name's first letter; the fuzzy match
        # still finds the squad, and each placement is given its squad.
        for row in rows:
            self.assertIn(row['registration_id'], {r.id for r in self.seated})
            self.assertIsNotNone(row['players'][0]['match'])
        commit = [{'registration_id': r['registration_id'], 'placement': r['placement'],
                   'players': [{'screen_name': p['screen_name'],
                                'user_id': p['match']['user_id'], 'kills': p['kills']}
                               for p in r['players']]} for r in rows]
        resp = client_for(self.keeper).post(self.base + 'reads/%s/commit/' % job_id,
                                            {'rows': commit}, format='json')
        self.assertEqual(resp.status_code, 200, resp.json())
        self.m1.refresh_from_db()
        self.assertEqual((self.m1.status, self.m1.entered_via), ('entered', 'ocr'))
        self.assertEqual(BRNameAlias.objects.filter(tournament=self.t).count(), 2)
        again = client_for(self.keeper).post(self.base + 'reads/%s/commit/' % job_id,
                                             {'rows': commit}, format='json')
        self.assertEqual(again.json()['code'], 'ALREADY_COMMITTED')
        # Next read of the same lobby matches by alias, exactly.
        placements = br_ocr.clean_read(br_ocr.read_for_local_test(self.m1))
        matched = br_ocr.match_rows(self.m1, placements)
        self.assertTrue(all(p['match']['how'] == 'alias'
                            for r in matched for p in r['players']))

    @override_settings(DEBUG=True, BR_OCR_SYNC=True)
    def test_a_read_not_yet_saved_is_offered_again(self):
        """Walk, 28 September 2026: a reload mid-review lost the read, and the
        day's count still said it was used."""
        def open_read(who):
            data = client_for(who).get(self.base).json()['data']['current']
            m = next(m for l in data['lobbies'] for m in l['maps'] if m['id'] == self.m1.id)
            return m.get('open_read')

        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': '', 'BR_OCR_ENGINE': 'local_test'}):
            with self.captureOnCommitCallbacks(execute=True):
                job_id = self.upload(self.keeper).json()['data']['job']['id']
        self.assertEqual(open_read(self.keeper), job_id)
        self.assertEqual(open_read(self.org), job_id)
        # Nobody who cannot record results is told a read exists.
        self.assertIsNone(APIClient().get(self.base).json()['data']['current']['lobbies'][0]
                          ['maps'][0].get('open_read'))
        # Typing the results in afterwards retires the read.
        a, b = self.seated
        resp = client_for(self.org).post(self.base + 'matches/%s/results/' % self.m1.id,
                                         self.rows(), format='json')
        self.assertEqual(resp.status_code, 200, resp.json())
        self.assertIsNone(open_read(self.org))

    @override_settings(DEBUG=True, BR_OCR_SYNC=True)
    def test_the_screenshot_is_for_staff_only(self):
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': '', 'BR_OCR_ENGINE': 'local_test'}):
            job_id = self.upload(self.org).json()['data']['job']['id']
        url = self.base + 'reads/%s/images/0/' % job_id
        self.assertEqual(client_for(self.org).get(url).status_code, 200)
        self.assertEqual(client_for(self.seated[0].user).get(url).status_code, 403)
        self.assertEqual(APIClient().get(url).status_code, 401)

    @override_settings(DEBUG=True, BR_OCR_SYNC=True)
    def test_uploads_are_checked(self):
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': '', 'BR_OCR_ENGINE': 'local_test'}):
            fake = SimpleUploadedFile('shot.png', b'<script>alert(1)</script>',
                                      content_type='image/png')
            self.assertEqual(self.upload(self.org, [fake]).json()['code'], 'NOT_AN_IMAGE')
            many = [SimpleUploadedFile('s%d.png' % i, png(), content_type='image/png')
                    for i in range(br_ocr.MAX_IMAGES + 1)]
            self.assertEqual(self.upload(self.org, many).json()['code'], 'TOO_MANY_IMAGES')
            self.assertEqual(self.upload(self.org, []).json()['code'], 'NO_IMAGES')

    @override_settings(DEBUG=True, BR_OCR_SYNC=True)
    def test_the_daily_cap_is_checked_before_the_call(self):
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': '', 'BR_OCR_ENGINE': 'local_test'}), \
                mock.patch('vent_tournament.br_ocr.daily_cap', return_value=1):
            self.assertEqual(self.upload(self.org).status_code, 202)
            resp = self.upload(self.org)
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()['code'], 'OCR_DAILY_LIMIT')

    def test_nobody_but_staff_may_upload(self):
        with mock.patch.dict(os.environ, {'BR_OCR_ENGINE': 'local_test'}):
            self.assertEqual(self.upload(self.stranger).status_code, 403)
            self.assertEqual(self.upload(self.seated[0].user).status_code, 403)

    def test_a_misread_player_in_the_wrong_squad_is_flagged(self):
        a, b = self.seated
        placements = [{'placement': 1, 'players': [
            {'screen_name': a.user.username, 'kills': 1, 'damage': 0, 'assists': 0},
            {'screen_name': a.user.username + 'x', 'kills': 0, 'damage': 0, 'assists': 0},
            {'screen_name': b.user.username, 'kills': 2, 'damage': 0, 'assists': 0}]}]
        rows = br_ocr.match_rows(self.m1, placements)
        self.assertEqual(rows[0]['registration_id'], a.id)
        flagged = [p['screen_name'] for p in rows[0]['players'] if p['wrong_team']]
        self.assertEqual(flagged, [b.user.username])

    def test_what_the_reader_says_is_checked_as_data(self):
        with self.assertRaises(br_ocr.OcrError):
            br_ocr.clean_read({'placements': 'drop table'})
        got = br_ocr.clean_read({'placements': [
            {'placement': '2', 'players': [{'name': 'A', 'kills': '-4'}, 'junk']},
            {'placement': 0, 'players': []},
            {'placement': 2, 'players': [{'name': 'A', 'kills': 1}, {'name': 'B', 'kills': 1}]}]})
        self.assertEqual(len(got), 1)
        self.assertEqual([p['screen_name'] for p in got[0]['players']], ['A', 'B'])

    def test_fold(self):
        self.assertEqual(br_ocr.fold('ＶＥＮＴ•Kazé'), br_ocr.fold('vent kaze'))

    def test_the_prompt_is_a_constant(self):
        import inspect
        source = inspect.getsource(br_ocr.read_with_gemini)
        self.assertIn("parts = [{'text': PROMPT}]", source)
