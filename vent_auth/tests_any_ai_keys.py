"""Any AI's key, not only Gemini's (inbox 555, CEO 25 October 2026).

"it shouldnt be only google ai studio for gemeini, but any api key from any
ai. they must be able to connect." Every provider's key is checked with that
provider before it is kept; the battle royale reader reads with whichever key
the tournament's organiser connected last; nothing is ever sent anywhere but
the key's own provider.
"""
import itertools
import json
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase

from vent_tournament import br_ocr
from vent_tournament.tests import client_for, make_tournament, make_user

from . import ai_providers
from .connected_keys import chosen_for_tournament
from .models import ConnectedKey

_N = itertools.count(5100000)
OPENAI = 'sk-proj-' + 'a' * 40
CLAUDE = 'sk-ant-api03-' + 'b' * 40
ROUTER = 'sk-or-v1-' + 'c' * 40
GEMINI = 'AIza' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r'
ANSWER = {'placements': [{'placement': 1, 'players': [{'name': 'Kaze', 'kills': 4}]}]}


def person():
    return make_user(next(_N))


def answers(status_code, payload=None):
    return mock.Mock(status_code=status_code, json=mock.Mock(return_value=payload or {}))


class MirrorTests(TestCase):
    def test_the_model_choices_mirror_the_provider_list(self):
        self.assertEqual(tuple(ConnectedKey.PROVIDERS), ai_providers.choices())

    def test_every_provider_says_where_to_get_a_key_and_a_default_model(self):
        for row in ai_providers.public_list():
            self.assertTrue(row['docs_url'].startswith('https://'), row)
            self.assertTrue(row['default_model'], row)
            self.assertNotIn('shape', row)


class ConnectAnyProviderTests(TestCase):
    def setUp(self):
        self.me = person()
        self.c = client_for(self.me)

    def connect(self, provider, key, **extra):
        return self.c.post('/auth/connected-keys/', dict(provider=provider, key=key, **extra),
                           format='json')

    def test_an_openai_key_is_checked_with_openai_and_kept(self):
        with mock.patch('requests.get', return_value=answers(200)) as got:
            res = self.connect('openai', OPENAI)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(got.call_args.args[0], 'https://api.openai.com/v1/models')
        self.assertEqual(got.call_args.kwargs['headers']['Authorization'], 'Bearer %s' % OPENAI)
        self.assertNotIn(OPENAI, res.content.decode())
        self.assertEqual(ConnectedKey.objects.get(user=self.me).provider, 'openai')

    def test_an_openrouter_key_is_checked_against_its_own_record(self):
        with mock.patch('requests.get', return_value=answers(200)) as got:
            self.connect('openrouter', ROUTER)
        self.assertEqual(got.call_args.args[0], 'https://openrouter.ai/api/v1/key')

    def test_a_claude_key_is_checked_with_anthropic(self):
        import anthropic
        fake = mock.Mock()
        with mock.patch.object(anthropic, 'Anthropic', return_value=fake) as made:
            res = self.connect('anthropic', CLAUDE)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(made.call_args.kwargs['api_key'], CLAUDE)
        fake.models.list.assert_called_once()

    def test_a_refused_key_names_the_provider_and_is_not_kept(self):
        with mock.patch('requests.get', return_value=answers(401)):
            res = self.connect('openai', OPENAI)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'KEY_REJECTED')
        self.assertIn('OpenAI', res.json()['message'])
        self.assertFalse(ConnectedKey.objects.exists())

    def test_a_key_of_another_provider_says_which_to_choose(self):
        with mock.patch('requests.get') as called:
            res = self.connect('openai', CLAUDE)
        body = res.json()
        self.assertEqual(body['code'], 'KEY_WRONG_PROVIDER')
        self.assertEqual(body['data']['guessed_key'], 'anthropic')
        called.assert_not_called()

    def test_a_model_name_is_kept_and_a_bad_one_refused(self):
        with mock.patch('requests.get', return_value=answers(200)):
            ok = self.connect('openrouter', ROUTER, model='anthropic/claude-opus-5-5')
            bad = self.connect('openai', OPENAI, model='https://evil.example/x')
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()['data']['key']['model'], 'anthropic/claude-opus-5-5')
        self.assertEqual(bad.json()['code'], 'MODEL_NAME_WRONG')

    def test_the_list_offers_every_provider(self):
        listed = self.c.get('/auth/connected-keys/').json()['data']['providers']
        self.assertEqual([p['key'] for p in listed], [k for k, _ in ai_providers.choices()])


class ReadTests(TestCase):
    IMAGES = [(b'\x89PNG fake', 'image/png')]

    def test_an_openai_style_read_sends_the_constant_prompt_and_the_image(self):
        reply = answers(200, {'choices': [{'message': {
            'content': 'Here you go:\n```json\n%s\n```' % json.dumps(ANSWER)}}]})
        with mock.patch('requests.post', return_value=reply) as sent:
            got = ai_providers.read_json('openrouter', ROUTER, 'openai/gpt-4o-mini',
                                         self.IMAGES, br_ocr.PROMPT)
        self.assertEqual(got, ANSWER)
        self.assertEqual(sent.call_args.args[0], 'https://openrouter.ai/api/v1/chat/completions')
        body = json.loads(sent.call_args.kwargs['data'])
        self.assertEqual(body['messages'][0]['content'][0]['text'], br_ocr.PROMPT)
        self.assertTrue(body['messages'][0]['content'][1]['image_url']['url']
                        .startswith('data:image/png;base64,'))

    def test_a_claude_read_uses_the_sdk_with_the_fallback_and_checks_refusals(self):
        import anthropic
        message = SimpleNamespace(stop_reason='end_turn',
                                  content=[SimpleNamespace(type='text', text=json.dumps(ANSWER))])
        fake = mock.Mock()
        fake.beta.messages.create.return_value = message
        with mock.patch.object(anthropic, 'Anthropic', return_value=fake):
            got = ai_providers.read_json('anthropic', CLAUDE, '', self.IMAGES, br_ocr.PROMPT)
        self.assertEqual(got, ANSWER)
        sent = fake.beta.messages.create.call_args.kwargs
        self.assertEqual(sent['model'], 'claude-opus-5-5')
        self.assertEqual(sent['fallbacks'], 'default')
        self.assertEqual(sent['messages'][0]['content'][0]['type'], 'image')
        message.stop_reason = 'refusal'
        with mock.patch.object(anthropic, 'Anthropic', return_value=fake):
            with self.assertRaises(ai_providers.ReadError) as caught:
                ai_providers.read_json('anthropic', CLAUDE, '', self.IMAGES, br_ocr.PROMPT)
        self.assertEqual(caught.exception.code, 'OCR_REFUSED')

    def test_a_gemini_read_still_works_with_its_schema(self):
        reply = answers(200, {'candidates': [{'content': {'parts': [{'text': json.dumps(ANSWER)}]}}]})
        with mock.patch('requests.post', return_value=reply) as sent:
            got = ai_providers.read_json('gemini', GEMINI, '', self.IMAGES, br_ocr.PROMPT,
                                         gemini_schema=br_ocr.RESPONSE_SCHEMA)
        self.assertEqual(got, ANSWER)
        self.assertIn('gemini-2.5-flash', sent.call_args.args[0])

    def test_a_refused_key_at_read_time_has_its_own_code(self):
        with mock.patch('requests.post', return_value=answers(401)):
            with self.assertRaises(ai_providers.ReadError) as caught:
                ai_providers.read_json('openai', OPENAI, '', self.IMAGES, br_ocr.PROMPT)
        self.assertEqual(caught.exception.code, 'OCR_KEY_REJECTED')

    def test_an_answer_with_no_json_is_unreadable(self):
        with self.assertRaises(ai_providers.ReadError):
            ai_providers.parse_json_answer('I cannot see any results.')


class WhichKeyTests(TestCase):
    def test_the_most_recently_connected_key_reads(self):
        creator = person()
        t = make_tournament(creator)
        with mock.patch('requests.get', return_value=answers(200)):
            client_for(creator).post('/auth/connected-keys/', {'provider': 'gemini', 'key': GEMINI},
                                     format='json')
            client_for(creator).post('/auth/connected-keys/', {'provider': 'openai', 'key': OPENAI,
                                                                'model': 'gpt-4o'}, format='json')
        chosen = chosen_for_tournament(t)
        self.assertEqual((chosen.provider, chosen.model), ('openai', 'gpt-4o'))
        self.assertEqual(br_ocr.engine_name(t), 'openai')
        self.assertTrue(br_ocr.available(t))

    def test_somebody_elses_key_is_never_used(self):
        t = make_tournament(person())
        with mock.patch('requests.get', return_value=answers(200)):
            client_for(person()).post('/auth/connected-keys/', {'provider': 'openai', 'key': OPENAI},
                                      format='json')
        self.assertIsNone(chosen_for_tournament(t))
        self.assertFalse(br_ocr.available(t))
