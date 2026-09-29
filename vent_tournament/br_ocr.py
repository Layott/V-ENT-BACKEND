"""Reading a battle royale result screen into rows somebody can check.

CEO, 28 September 2026: "Everything including uploading results and using
OCR." AFC does this, and how it does it was read the same day:

  1. The organiser uploads the end-of-match screenshots for one match.
  2. A vision model reads them into placements, each with its players' in-game
     names and kills. That takes 12 to 26 seconds, so it runs in the
     background and the page asks how it is going.
  3. Each name is matched to a registered player: a name somebody already
     corrected once (an alias) matches exactly; anything else is matched
     fuzzily, keeping the three closest so a correction is one tap.
  4. A placement whose players mostly belong to one squad is that squad; a
     player whose match belongs to a different squad is flagged, because that
     is usually a misread rather than a transfer.
  5. The organiser corrects and commits. The commit goes through the SAME
     entry path as typing (`br_engine.enter_results`), and every confirmed
     name is remembered, so the next read of the same lobby is better.

Rules this file keeps (owner rules, September 2026):

  * R74: the prompt is a constant. Nothing a user typed goes into it; the
    only thing sent is the images. What comes back is data, parsed as JSON and
    checked field by field, never run or rendered as markup.
  * R75: a per-person daily cap, from the admin dashboard, checked BEFORE the
    call is made.
  * R70: uploads are sniffed, capped in size and count, renamed, and stored
    where nothing is served or executed.
  * R55: the key is read from the environment on the server and never leaves
    it. With no key the door says so and typing results still works.
"""
import base64
import difflib
import json
import logging
import os
import re
import threading
import unicodedata

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_IMAGES = 6
MAX_BYTES = 8 * 1024 * 1024
ALLOWED_TYPES = ('image/png', 'image/jpeg', 'image/webp')
MODEL = 'gemini-2.5-flash'
TIMEOUT_SECONDS = 90

HIGH = 0.85
MEDIUM = 0.75

# The whole instruction, fixed. Owner rule R74: user input never reaches it.
PROMPT = (
    'These images are the end-of-match results screen of one battle royale '
    'match (Free Fire, PUBG Mobile or similar). Read every squad shown. For '
    'each squad give its finishing placement (1 is the winner) and each '
    'player in it with the in-game name exactly as written and that '
    "player's kills. Include damage and assists only where the screen shows "
    'them. If the same squad appears on more than one image, list it once. '
    'Answer with JSON only.'
)

RESPONSE_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'placements': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    'placement': {'type': 'INTEGER'},
                    'players': {
                        'type': 'ARRAY',
                        'items': {
                            'type': 'OBJECT',
                            'properties': {
                                'name': {'type': 'STRING'},
                                'kills': {'type': 'INTEGER'},
                                'damage': {'type': 'INTEGER'},
                                'assists': {'type': 'INTEGER'},
                            },
                            'required': ['name', 'kills'],
                        },
                    },
                },
                'required': ['placement', 'players'],
            },
        },
    },
    'required': ['placements'],
}


class OcrError(ValueError):
    def __init__(self, code, field=None, **extra):
        super().__init__(code)
        self.code = code
        self.field = field
        self.extra = extra


# ---------------------------------------------------------------------------
# Which reader
# ---------------------------------------------------------------------------

def engine_name():
    """'gemini' when a key is set; 'local_test' only on a DEBUG machine that
    asked for it (so the flow can be walked without a paid key); else ''."""
    if os.environ.get('GEMINI_API_KEY', '').strip():
        return 'gemini'
    if settings.DEBUG and os.environ.get('BR_OCR_ENGINE') == 'local_test':
        return 'local_test'
    return ''


def available():
    return bool(engine_name())


def daily_cap():
    try:
        from vent_auth.models import AdminSetting, DEFAULT_ADMIN_SETTINGS
        stored = (AdminSetting.load().merged().get('ocr') or {}).get('reads_per_person_per_day')
        value = DEFAULT_ADMIN_SETTINGS['ocr']['reads_per_person_per_day'] if stored is None else stored
        return max(0, int(value))
    except Exception:
        # A setting nobody can read is a cap of nothing rather than no cap:
        # the paid call is the side to fail closed on.
        return 0


def used_today(user):
    from .models import BROcrJob
    since = timezone.now() - timezone.timedelta(hours=24)
    return BROcrJob.objects.filter(created_by=user, created_at__gte=since).count()


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------

def sniff(head):
    """The real type from the first bytes, whatever the upload claimed."""
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    return None


def check_uploads(files):
    """[(bytes, content_type)] for acceptable screenshots, or OcrError."""
    if not files:
        raise OcrError('NO_IMAGES', 'images')
    if len(files) > MAX_IMAGES:
        raise OcrError('TOO_MANY_IMAGES', 'images', limit=MAX_IMAGES)
    out = []
    for f in files:
        if f.size > MAX_BYTES:
            raise OcrError('IMAGE_TOO_LARGE', 'images', limit_mb=MAX_BYTES // (1024 * 1024))
        data = f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise OcrError('IMAGE_TOO_LARGE', 'images', limit_mb=MAX_BYTES // (1024 * 1024))
        kind = sniff(data[:16])
        if kind not in ALLOWED_TYPES:
            raise OcrError('NOT_AN_IMAGE', 'images')
        try:
            from io import BytesIO
            from PIL import Image
            Image.open(BytesIO(data)).verify()
        except Exception:
            raise OcrError('NOT_AN_IMAGE', 'images')
        out.append((data, kind))
    return out


def create_job(br_map, user, uploads):
    """Store the screenshots and queue a read. Runs inside a transaction; the
    read starts once it commits."""
    from django.core.files.base import ContentFile
    from .models import BROcrImage, BROcrJob

    job = BROcrJob.objects.create(map=br_map, created_by=user, engine=engine_name())
    for order, (data, kind) in enumerate(uploads):
        image = BROcrImage(job=job, content_type=kind, size=len(data), order=order)
        image.file.save('upload', ContentFile(data), save=False)
        image.save()
    transaction.on_commit(lambda: start(job.pk))
    return job


# ---------------------------------------------------------------------------
# Running a read
# ---------------------------------------------------------------------------

def start(job_id):
    if getattr(settings, 'BR_OCR_SYNC', False):
        run(job_id)
        return
    threading.Thread(target=_run_in_thread, args=(job_id,), daemon=True).start()


def _run_in_thread(job_id):
    try:
        run(job_id)
    finally:
        close_old_connections()


def run(job_id):
    from .models import BROcrJob

    job = BROcrJob.objects.select_related('map__lobby__stage__tournament').get(pk=job_id)
    if job.status not in ('queued',):
        return job
    job.status = 'reading'
    job.save(update_fields=['status'])
    try:
        images = [(img.file.open('rb').read(), img.content_type) for img in job.images.all()]
        engine = job.engine or engine_name()
        if engine == 'gemini':
            raw = read_with_gemini(images)
        elif engine == 'local_test':
            raw = read_for_local_test(job.map)
        else:
            raise OcrError('OCR_NOT_CONFIGURED')
        placements = clean_read(raw)
        job.rows = match_rows(job.map, placements)
        job.status = 'ready'
        job.error_code = ''
    except OcrError as exc:
        job.status = 'failed'
        job.error_code = exc.code
    except Exception:
        logger.exception('battle royale read %s failed', job_id)
        job.status = 'failed'
        job.error_code = 'OCR_FAILED'
    job.finished_at = timezone.now()
    job.save(update_fields=['status', 'rows', 'error_code', 'finished_at'])
    return job


def read_with_gemini(images):
    import requests

    key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not key:
        raise OcrError('OCR_NOT_CONFIGURED')
    parts = [{'text': PROMPT}]
    for data, kind in images:
        parts.append({'inline_data': {'mime_type': kind,
                                      'data': base64.b64encode(data).decode('ascii')}})
    body = {
        'contents': [{'role': 'user', 'parts': parts}],
        'generationConfig': {'response_mime_type': 'application/json',
                             'response_schema': RESPONSE_SCHEMA,
                             'temperature': 0},
    }
    try:
        resp = requests.post(
            'https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent' % MODEL,
            headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
            data=json.dumps(body), timeout=TIMEOUT_SECONDS)
    except requests.RequestException:
        raise OcrError('OCR_UNREACHABLE')
    if resp.status_code == 429:
        raise OcrError('OCR_BUSY')
    if resp.status_code != 200:
        logger.warning('gemini answered %s', resp.status_code)
        raise OcrError('OCR_FAILED')
    try:
        text = resp.json()['candidates'][0]['content']['parts'][0]['text']
        return json.loads(text)
    except (KeyError, IndexError, ValueError, TypeError):
        raise OcrError('OCR_UNREADABLE')


def read_for_local_test(br_map):
    """A stand-in reader for walking the flow on a DEBUG machine with no key.

    It does not look at the pictures. It returns the lobby's own squads in
    seat order, one player each, with the first letter of each name dropped
    so the fuzzy match and the review step have something real to do. Never
    reachable outside DEBUG with BR_OCR_ENGINE=local_test.
    """
    placements = []
    for i, seat in enumerate(br_map.lobby.seats.select_related('registration'), start=1):
        people = seat.registration.people
        players = [{'name': (p.username or '')[1:] or p.username, 'kills': (i * 3) % 7}
                   for p in people[:4]]
        placements.append({'placement': i, 'players': players})
    return {'placements': placements}


def _int(value, low=0, high=100000):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(low, min(high, n))


def clean_read(raw):
    """What the reader said, checked field by field. It is data from outside:
    anything the wrong shape is dropped rather than trusted."""
    if not isinstance(raw, dict) or not isinstance(raw.get('placements'), list):
        raise OcrError('OCR_UNREADABLE')
    by_place = {}
    for item in raw['placements'][:100]:
        if not isinstance(item, dict):
            continue
        place = _int(item.get('placement'), 0, 100)
        if not place:
            continue
        players = []
        for p in (item.get('players') or [])[:8]:
            if not isinstance(p, dict):
                continue
            name = str(p.get('name') or '').strip()[:60]
            if not name:
                continue
            players.append({'screen_name': name, 'kills': _int(p.get('kills'), 0, 200),
                            'damage': _int(p.get('damage'), 0, 100000),
                            'assists': _int(p.get('assists'), 0, 200)})
        # A squad read twice (it appears on two screenshots) is kept once.
        if place not in by_place or len(players) > len(by_place[place]['players']):
            by_place[place] = {'placement': place, 'players': players}
    if not by_place:
        raise OcrError('OCR_FOUND_NOTHING')
    return [by_place[k] for k in sorted(by_place)]


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def fold(name):
    """A name reduced to what survives a misread: no accents, no case, no
    decoration. 'ＶＥＮＴ•Kaze' and 'vent kaze' fold the same."""
    text = unicodedata.normalize('NFKD', str(name or ''))
    text = ''.join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r'[^0-9a-zЀ-ӿ؀-ۿ一-鿿]+', '', text)[:60]


def _similar(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    # A clan tag in front ("vxkaze" for "kaze") or behind is the commonest
    # difference between an account name and the name in the game.
    if len(b) >= 4 and (a.endswith(b) or a.startswith(b)):
        ratio = max(ratio, 0.9)
    if len(a) >= 4 and (b.endswith(a) or b.startswith(a)):
        ratio = max(ratio, 0.88)
    return round(ratio, 3)


def roster_names(br_map):
    """[(folded name, user, registration_id)] for everybody seated in the
    lobby: usernames, full names, and their saved in-game names."""
    from vent_auth.models import GameAccount

    out = []
    tournament = br_map.lobby.stage.tournament
    for seat in br_map.lobby.seats.select_related('registration'):
        rid = seat.registration_id
        for user in seat.registration.people:
            names = {user.username, getattr(user, 'full_name', '') or ''}
            accounts = GameAccount.objects.filter(user=user)
            if tournament.tournament_game_id:
                accounts = accounts.filter(game_id=tournament.tournament_game_id)
            names.update(a.game_username for a in accounts if a.game_username)
            for n in names:
                folded = fold(n)
                if folded:
                    out.append((folded, user, rid))
    return out


def match_rows(br_map, placements):
    """The reader's placements, with each name matched and each placement
    given the squad most of its players belong to."""
    from .models import BRNameAlias

    tournament = br_map.lobby.stage.tournament
    seated = set(br_map.lobby.seats.values_list('registration_id', flat=True))
    aliases = {a.screen_name: a for a in BRNameAlias.objects.filter(
        tournament=tournament, registration_id__in=seated).select_related('user')}
    roster = roster_names(br_map)
    rows = []
    for item in placements:
        players = []
        votes = {}
        for p in item['players']:
            folded = fold(p['screen_name'])
            match = None
            candidates = []
            alias = aliases.get(folded)
            if alias is not None:
                match = {'user_id': alias.user_id, 'registration_id': alias.registration_id,
                         'username': alias.user.username if alias.user_id else None,
                         'confidence': 1.0, 'how': 'alias'}
            else:
                best = {}
                for name, user, rid in roster:
                    score = _similar(folded, name)
                    key = (user.user_id, rid)
                    if score > best.get(key, (0,))[0]:
                        best[key] = (score, user, rid)
                ranked = sorted(best.values(), key=lambda x: -x[0])[:3]
                candidates = [{'user_id': u.user_id, 'username': u.username,
                               'registration_id': rid, 'score': s}
                              for s, u, rid in ranked if s >= 0.5]
                if ranked and ranked[0][0] >= MEDIUM:
                    s, u, rid = ranked[0]
                    match = {'user_id': u.user_id, 'registration_id': rid,
                             'username': u.username, 'confidence': s,
                             'how': 'fuzzy'}
            if match:
                votes[match['registration_id']] = votes.get(match['registration_id'], 0) + 1
            players.append(dict(p, match=match, candidates=candidates,
                                confidence=(match['confidence'] if match else 0.0),
                                wrong_team=False))
        squad = max(votes, key=lambda r: (votes[r], -r)) if votes else None
        for p in players:
            if squad and p['match'] and p['match']['registration_id'] != squad:
                p['wrong_team'] = True
        rows.append({'placement': item['placement'], 'registration_id': squad,
                     'squad_votes': votes.get(squad, 0) if squad else 0,
                     'players': players})
    return rows


def learn_aliases(br_map, rows):
    """Remember every name the organiser confirmed, so the next read of it
    matches exactly. `rows` is what was committed."""
    from .models import BRNameAlias

    tournament = br_map.lobby.stage.tournament
    learned = 0
    for row in rows:
        rid = row.get('registration_id')
        for p in row.get('players') or []:
            folded = fold(p.get('screen_name'))
            if not folded or not rid:
                continue
            user_id = p.get('user_id') or None
            BRNameAlias.objects.update_or_create(
                tournament=tournament, screen_name=folded,
                defaults={'registration_id': rid, 'user_id': user_id})
            learned += 1
    return learned
