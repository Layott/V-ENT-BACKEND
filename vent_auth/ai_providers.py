"""The AI services an organiser may connect their own key for (inbox 555).

CEO, 25 October 2026, on Settings > Connected keys: "it shouldnt be only google
ai studio for gemeini, but any api key from any ai. they must be able to
connect." So the reader is not tied to one company. Each provider here says
how its keys look, how to check one for free before it is kept, where a person
gets one, which model reads a screenshot by default, and how to ask it to read.

OpenRouter is on the list because one key there reaches hundreds of models
from every company, which is how "any AI" is honoured without V-ENT having to
know each of them. The organiser may also name the model their key should use.

    PROVIDERS[name]                         label, key_hint, docs_url, default_model
    guess(key)                              the provider a key's prefix belongs to, or ''
    check_shape(provider, key)              raises KeyCheck('KEY_SHAPE_WRONG' | 'KEY_WRONG_PROVIDER')
    verify(provider, key)                   raises KeyCheck('KEY_REJECTED' | 'KEY_CHECK_UNAVAILABLE')
    read_json(provider, key, model, images, prompt) -> the model's answer, parsed

Owner rules held here: R74, the prompt is a constant and only images are sent
with it; the answer is parsed as JSON and checked by the caller field by field.
R55, a key never leaves the server and is sent only to its own provider.
"""
import base64
import json
import logging
import re

logger = logging.getLogger(__name__)

TIMEOUT = 90
CHECK_TIMEOUT = 8

# OpenAI's chat completions shape, which most providers also speak.
OPENAI_STYLE = 'openai'

PROVIDERS = {
    'gemini': {
        'label': 'Google Gemini',
        'key_hint': 'Starts with AIza',
        'docs_url': 'https://aistudio.google.com/app/apikey',
        'default_model': 'gemini-2.5-flash',
        'shape': r'^AIza[0-9A-Za-z_\-]{35}$',
    },
    'openai': {
        'label': 'OpenAI',
        'key_hint': 'Starts with sk-',
        'docs_url': 'https://platform.openai.com/api-keys',
        'default_model': 'gpt-4o-mini',
        'shape': r'^sk-(?!ant-|or-)[A-Za-z0-9_\-]{20,}$',
        'style': OPENAI_STYLE,
        'base': 'https://api.openai.com/v1',
    },
    'anthropic': {
        'label': 'Anthropic Claude',
        'key_hint': 'Starts with sk-ant-',
        'docs_url': 'https://console.anthropic.com/settings/keys',
        'default_model': 'claude-opus-5-5',
        'shape': r'^sk-ant-[A-Za-z0-9_\-]{20,}$',
    },
    'openrouter': {
        'label': 'OpenRouter (any model)',
        'key_hint': 'Starts with sk-or-',
        'docs_url': 'https://openrouter.ai/keys',
        'default_model': 'openai/gpt-4o-mini',
        'shape': r'^sk-or-[A-Za-z0-9_\-]{20,}$',
        'style': OPENAI_STYLE,
        'base': 'https://openrouter.ai/api/v1',
    },
    'mistral': {
        'label': 'Mistral',
        'key_hint': '32 letters and digits',
        'docs_url': 'https://console.mistral.ai/api-keys',
        'default_model': 'pixtral-12b-latest',
        'shape': r'^[A-Za-z0-9]{32}$',
        'style': OPENAI_STYLE,
        'base': 'https://api.mistral.ai/v1',
    },
    'groq': {
        'label': 'Groq',
        'key_hint': 'Starts with gsk_',
        'docs_url': 'https://console.groq.com/keys',
        'default_model': 'meta-llama/llama-4-scout-17b-16e-instruct',
        'shape': r'^gsk_[A-Za-z0-9]{20,}$',
        'style': OPENAI_STYLE,
        'base': 'https://api.groq.com/openai/v1',
    },
    'xai': {
        'label': 'xAI Grok',
        'key_hint': 'Starts with xai-',
        'docs_url': 'https://console.x.ai',
        'default_model': 'grok-2-vision-latest',
        'shape': r'^xai-[A-Za-z0-9]{20,}$',
        'style': OPENAI_STYLE,
        'base': 'https://api.x.ai/v1',
    },
}

# A model name the organiser types: letters, digits and the punctuation model
# names use. Never a URL, never anything that could become part of a host.
MODEL_SHAPE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,79}$')

# Prefix to provider, longest first, for telling somebody they chose the wrong one.
_PREFIXES = (('sk-ant-', 'anthropic'), ('sk-or-', 'openrouter'), ('gsk_', 'groq'),
             ('xai-', 'xai'), ('AIza', 'gemini'), ('sk-', 'openai'))


class KeyCheck(Exception):
    def __init__(self, code, **extra):
        super().__init__(code)
        self.code = code
        self.extra = extra


class ReadError(Exception):
    """A read that did not produce an answer, with the code the screen shows."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def choices():
    return tuple((name, spec['label']) for name, spec in PROVIDERS.items())


def public_list():
    """What a screen needs to offer the choice: never anything secret."""
    return [{'key': name, 'label': spec['label'], 'key_hint': spec['key_hint'],
             'docs_url': spec['docs_url'], 'default_model': spec['default_model']}
            for name, spec in PROVIDERS.items()]


def guess(key):
    for prefix, provider in _PREFIXES:
        if (key or '').startswith(prefix):
            return provider
    return ''


def check_shape(provider, key):
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise KeyCheck('KEY_PROVIDER_UNKNOWN')
    if re.match(spec['shape'], key or ''):
        return
    other = guess(key)
    if other and other != provider:
        raise KeyCheck('KEY_WRONG_PROVIDER', guessed=other,
                       guessed_label=PROVIDERS[other]['label'])
    raise KeyCheck('KEY_SHAPE_WRONG', hint=spec['key_hint'])


def check_model(model):
    # A name like "openai/gpt-4o-mini", never an address: no scheme, no "..".
    if model and (not MODEL_SHAPE.match(model) or '..' in model or '://' in model
                  or '//' in model):
        raise KeyCheck('MODEL_NAME_WRONG')


def _status_to_check(code):
    if code in (400, 401, 403):
        raise KeyCheck('KEY_REJECTED')
    if code == 429 or code >= 500:
        raise KeyCheck('KEY_CHECK_UNAVAILABLE')


def verify(provider, key):
    """Ask the provider, with a call that costs nothing, whether the key works.
    A key is never kept without an answer."""
    import requests

    spec = PROVIDERS.get(provider)
    if spec is None:
        raise KeyCheck('KEY_PROVIDER_UNKNOWN')
    if provider == 'anthropic':
        return _verify_anthropic(key)
    try:
        if provider == 'gemini':
            resp = requests.get('https://generativelanguage.googleapis.com/v1beta/models',
                                params={'key': key, 'pageSize': 1}, timeout=CHECK_TIMEOUT)
        elif provider == 'openrouter':
            # OpenRouter's model list is public, so it proves nothing about a
            # key; the key's own record answers only to a real key.
            resp = requests.get('https://openrouter.ai/api/v1/key',
                                headers={'Authorization': 'Bearer %s' % key}, timeout=CHECK_TIMEOUT)
        else:
            resp = requests.get('%s/models' % spec['base'],
                                headers={'Authorization': 'Bearer %s' % key}, timeout=CHECK_TIMEOUT)
    except requests.RequestException:
        raise KeyCheck('KEY_CHECK_UNAVAILABLE')
    _status_to_check(resp.status_code)
    if resp.status_code == 404:
        raise KeyCheck('KEY_CHECK_UNAVAILABLE')
    return True


def _verify_anthropic(key):
    import anthropic

    client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=CHECK_TIMEOUT)
    try:
        client.models.list(limit=1)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise KeyCheck('KEY_REJECTED')
    except (anthropic.RateLimitError, anthropic.APIConnectionError):
        raise KeyCheck('KEY_CHECK_UNAVAILABLE')
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            raise KeyCheck('KEY_CHECK_UNAVAILABLE')
        raise KeyCheck('KEY_REJECTED')
    return True


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _b64(data):
    return base64.b64encode(data).decode('ascii')


def parse_json_answer(text):
    """The JSON object in a model's answer. Some models wrap it in a code fence
    or say a sentence first; the object itself is what is taken, and nothing
    else in the text is ever used."""
    if not isinstance(text, str):
        raise ReadError('OCR_UNREADABLE')
    text = text.strip()
    fenced = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        raise ReadError('OCR_UNREADABLE')
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        raise ReadError('OCR_UNREADABLE')
    if not isinstance(value, dict):
        raise ReadError('OCR_UNREADABLE')
    return value


def read_json(provider, key, model, images, prompt, gemini_schema=None):
    """Send the fixed prompt and the images to the provider; return the parsed
    JSON object it answered with. `images` is a list of (bytes, media type)."""
    spec = PROVIDERS.get(provider)
    if spec is None or not key:
        raise ReadError('OCR_KEY_REQUIRED')
    model = model or spec['default_model']
    if provider == 'gemini':
        return _read_gemini(key, model, images, prompt, gemini_schema)
    if provider == 'anthropic':
        return _read_anthropic(key, model, images, prompt)
    return _read_openai_style(spec, key, model, images, prompt)


def _read_gemini(key, model, images, prompt, schema):
    import requests

    parts = [{'text': prompt}]
    for data, kind in images:
        parts.append({'inline_data': {'mime_type': kind, 'data': _b64(data)}})
    config = {'response_mime_type': 'application/json', 'temperature': 0}
    if schema:
        config['response_schema'] = schema
    body = {'contents': [{'role': 'user', 'parts': parts}], 'generationConfig': config}
    try:
        resp = requests.post(
            'https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent' % model,
            headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
            data=json.dumps(body), timeout=TIMEOUT)
    except requests.RequestException:
        raise ReadError('OCR_UNREACHABLE')
    _read_status(resp.status_code, 'gemini')
    try:
        return parse_json_answer(resp.json()['candidates'][0]['content']['parts'][0]['text'])
    except (KeyError, IndexError, ValueError, TypeError):
        raise ReadError('OCR_UNREADABLE')


def _read_openai_style(spec, key, model, images, prompt):
    import requests

    content = [{'type': 'text', 'text': prompt}]
    for data, kind in images:
        content.append({'type': 'image_url',
                        'image_url': {'url': 'data:%s;base64,%s' % (kind, _b64(data))}})
    body = {'model': model, 'messages': [{'role': 'user', 'content': content}]}
    try:
        resp = requests.post('%s/chat/completions' % spec['base'],
                             headers={'Authorization': 'Bearer %s' % key,
                                      'Content-Type': 'application/json'},
                             data=json.dumps(body), timeout=TIMEOUT)
    except requests.RequestException:
        raise ReadError('OCR_UNREACHABLE')
    _read_status(resp.status_code, spec['label'])
    try:
        message = resp.json()['choices'][0]['message']['content']
    except (KeyError, IndexError, ValueError, TypeError):
        raise ReadError('OCR_UNREADABLE')
    if isinstance(message, list):  # some providers answer in parts
        message = ''.join(part.get('text', '') for part in message if isinstance(part, dict))
    return parse_json_answer(message)


# The current Claude models that accept the server-side fallback on refusal.
_FALLBACK_MODELS = ('claude-opus-5-5', 'claude-fable-5-1', 'claude-opus-5', 'claude-sonnet-5-5')


def _read_anthropic(key, model, images, prompt):
    import anthropic

    client = anthropic.Anthropic(api_key=key, max_retries=1, timeout=TIMEOUT)
    content = [{'type': 'image', 'source': {'type': 'base64', 'media_type': kind,
                                             'data': _b64(data)}}
               for data, kind in images]
    content.append({'type': 'text', 'text': prompt})
    extra = {}
    if model in _FALLBACK_MODELS:
        # A safety decline is re-run on another model inside the same call.
        extra = {'betas': ['server-side-fallback-2026-07-01'], 'fallbacks': 'default'}
    try:
        response = client.beta.messages.create(
            model=model, max_tokens=16000,
            messages=[{'role': 'user', 'content': content}], **extra)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise ReadError('OCR_KEY_REJECTED')
    except anthropic.NotFoundError:
        raise ReadError('OCR_MODEL_UNKNOWN')
    except anthropic.RateLimitError:
        raise ReadError('OCR_BUSY')
    except anthropic.APIConnectionError:
        raise ReadError('OCR_UNREACHABLE')
    except anthropic.APIStatusError as exc:
        logger.warning('anthropic answered %s', exc.status_code)
        raise ReadError('OCR_FAILED')
    if response.stop_reason == 'refusal':
        raise ReadError('OCR_REFUSED')
    text = ''.join(block.text for block in response.content if block.type == 'text')
    return parse_json_answer(text)


def _read_status(code, who):
    if code == 200:
        return
    if code in (401, 403):
        raise ReadError('OCR_KEY_REJECTED')
    if code == 404:
        raise ReadError('OCR_MODEL_UNKNOWN')
    if code == 429:
        raise ReadError('OCR_BUSY')
    logger.warning('%s answered %s', who, code)
    raise ReadError('OCR_FAILED')
