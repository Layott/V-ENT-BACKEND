"""The prompt, the runtime and the feed are one vocabulary.

An overlay works when three separately written things agree: the PROMPT tells a
designer which names exist, `static/overlay-runtime.js` resolves those names,
and the FEED sends the data they resolve against. Nothing connects them, and
every failure is silent - a name the feed does not send fills with an empty
string, which on air looks like a design that did not load rather than an error
anybody can see.

It happened. A first draft of the prompt listed `tournament_name`, `home_score`
and `away_score`. The feed sends `tournament.title` and a `teams` array. Every
name in that prompt would have resolved to nothing, and a designer following it
precisely would have produced an overlay that filled with blanks in front of an
audience. Nothing caught it: the upload succeeded, the tests were green, the
file was valid HTML.

So this file is the catcher. It mirrors what the runtime does with a name and
asserts that every name the prompt promises actually resolves against a real
feed, for tournaments and for events. A new field added to one of the three and
not the other two fails here rather than on air.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, TeamMembers, Teams, Users
from vent_event.models import Event, EventSession, Sponsor
from vent_tournament.models import Tournament, TournamentRegistration
from vent_tournament.views_overlays import (
    DESIGNER_PROMPT_EVENT, DESIGNER_PROMPT_TOURNAMENT,
    EVENT_NAMES, EVENT_REPEATS, TOURNAMENT_NAMES, TOURNAMENT_REPEATS)


def resolve(path, data):
    """What `read()` in static/overlay-runtime.js does at the top level.

    Kept deliberately short and deliberately a copy: the point is to state the
    resolution rule once here in a language the test can assert in. If the
    runtime's rule changes, this changes with it and the test says so.
    """
    parts = str(path).split('|')[0].strip().split('.')
    if len(parts) == 1:
        root = data
    elif parts[0] == 'tournament':
        root, parts = data.get('tournament'), parts[1:]
    elif parts[0] == 'team':
        teams = data.get('teams') or []
        root, parts = (teams[0] if teams else None), parts[1:]
    elif parts[0] == 'player':
        teams = data.get('teams') or []
        players = (teams[0].get('players') if teams else None) or []
        root, parts = (players[0] if players else None), parts[1:]
    elif parts[0] == 'asset':
        root, parts = data.get('asset'), parts[1:]
    else:
        root = data
    value = root
    for part in parts:
        if not isinstance(value, dict):
            return KeyError
        # `asset.<name>` is documented as a shape, not a key: the name half is
        # whatever the organiser typed in the studio, so the vocabulary can
        # only promise that the place to look it up exists.
        if part.startswith('<') and part.endswith('>'):
            return value
        if part not in value:
            return KeyError
        value = value[part]
    return value


class TournamentVocabularyTests(TestCase):
    def setUp(self):
        self.owner = Users.objects.create(
            username='vocabOwner', email='vo@vent.test', is_active=True)
        game = Games.objects.create(game_title='Vocabulary FC')
        self.tournament = Tournament.objects.create(
            tournament_title='Vocabulary Cup', tournament_game=game,
            tournament_creator=self.owner,
            start_date_and_time=timezone.now(),
            end_date_and_time=timezone.now(),
            is_draft=False)
        # A sponsor, so the `sponsors` repeat has a row to check against.
        from vent_tournament.models import Sponsors
        self.tournament.sponsors.add(
            Sponsors.objects.create(name='Vocabulary Telecom', website='https://vt.example'))
        # A clip in the studio, so the `assets` repeat has a row to check and
        # `asset.<slot>` has something behind it.
        from django.core.files.uploadedfile import SimpleUploadedFile
        from vent_tournament.models import StudioAsset
        StudioAsset.objects.create(
            tournament=self.tournament, kind='image', name='Hero shot',
            slot='hero', team_tag='VOC',
            file=SimpleUploadedFile('hero.png', b'a picture',
                                    content_type='image/png'))
        team = Teams.objects.create(
            team_name='Vocabulary XI', game=game, description='x',
            team_creator=self.owner, team_owner=self.owner,
            penalty_points=0, number_of_members=1)
        TeamMembers.objects.create(team=team, user=self.owner, is_captain=True)
        TournamentRegistration.objects.create(
            tournament=self.tournament, team=team, status='confirmed')

    def feed(self):
        res = self.client.get('/tournament/%s/overlay-feed/'
                              % (self.tournament.slug or self.tournament.tournament_id))
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    def test_every_name_the_prompt_promises_resolves_in_the_feed(self):
        data = self.feed()
        missing = [name for name, _why in TOURNAMENT_NAMES
                   if resolve(name, data) is KeyError]
        self.assertEqual(
            missing, [],
            'the prompt promises names the feed never sends: %s' % missing)

    def rows_for(self, key, data):
        """What `repeat()` in the runtime hands a template for this key.

        Two of the four are not root keys: `standings` is an alias for `teams`,
        and `players` is the roster of the team the overlay is pointed at.
        Asserting they are root arrays is asserting the wrong thing, which is
        how a checker ends up reporting a fault that is not there.
        """
        if key == 'players':
            teams = data.get('teams') or []
            return (teams[0].get('players') if teams else []) or []
        return data.get('teams' if key == 'standings' else key)

    def test_every_repeat_is_a_list_in_the_feed(self):
        data = self.feed()
        for key, _why, _fields in TOURNAMENT_REPEATS:
            self.assertIsInstance(self.rows_for(key, data), list, key)

    def test_every_bare_field_exists_on_a_row(self):
        data = self.feed()
        for key, _why, fields in TOURNAMENT_REPEATS:
            rows = self.rows_for(key, data)
            if key == 'live':
                continue          # covered by its own test, with a live match
            self.assertTrue(rows, 'no %s in the fixture to check' % key)
            for field in fields:
                self.assertIn(field, rows[0], '%s.%s' % (key, field))


    def test_a_live_match_names_who_is_playing(self):
        """The bracket graphic used to go on air reading "R2  0 - 0" and name
        nobody, because the feed sent the round, the score and nothing else. A
        scoreline with no names tells an audience less than no graphic."""
        from vent_tournament.models import BracketMatch

        registration = TournamentRegistration.objects.get()
        BracketMatch.objects.create(
            tournament=self.tournament, round_number=2, match_number=1,
            participant_1=registration, status='in_progress',
            score_p1=2, score_p2=1)

        live = self.feed()['live']
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]['home'], 'Vocabulary XI')
        self.assertIn('away', live[0])
        self.assertEqual(live[0]['score'], [2, 1])

    def test_the_prompt_text_lists_exactly_those_names(self):
        for name, _why in TOURNAMENT_NAMES:
            self.assertIn(name, DESIGNER_PROMPT_TOURNAMENT, name)
        for key, _why, _fields in TOURNAMENT_REPEATS:
            self.assertIn(key, DESIGNER_PROMPT_TOURNAMENT, key)

    def test_the_prompt_does_not_describe_the_other_kind(self):
        """An organiser given an event example on a tournament screen binds to
        a field that does not exist for them, and finds out on air."""
        self.assertNotIn('event.', DESIGNER_PROMPT_TOURNAMENT)

    def test_the_feed_carries_a_version(self):
        """The runtime redraws only when `version` moves. Without one, every
        poll after the first sees undefined === undefined, decides nothing
        changed, and the overlay freezes at its first frame."""
        self.assertIn('version', self.feed())


class EventVocabularyTests(TestCase):
    def setUp(self):
        self.owner = Users.objects.create(
            username='vocabEvOwner', email='veo@vent.test', is_active=True)
        self.event = Event.objects.create(
            name='Vocabulary Con', creator=self.owner, event_type='physical',
            desc='x', entry_fee=Decimal('0'),
            reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            event_date=timezone.now().date(),
            start_time=timezone.now().time(), end_time=timezone.now().time())
        EventSession.objects.create(
            event=self.event, title='Opening panel', stage='Main hall',
            starts_at=timezone.now(),
            ends_at=timezone.now() + timezone.timedelta(hours=1))
        EventSession.objects.create(
            event=self.event, title='Cosplay judging', stage='Studio 2',
            starts_at=timezone.now() + timezone.timedelta(hours=2),
            ends_at=timezone.now() + timezone.timedelta(hours=3))
        Sponsor.objects.create(event=self.event, name='Vermillion Encore')
        from django.core.files.uploadedfile import SimpleUploadedFile
        from vent_tournament.models import StudioAsset
        StudioAsset.objects.create(
            event=self.event, kind='image', name='Poster', slot='hero',
            file=SimpleUploadedFile('poster.png', b'a picture',
                                    content_type='image/png'))

    def feed(self):
        res = self.client.get('/event/%s/overlay-feed/' % self.event.slug)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    def test_every_name_the_prompt_promises_resolves_in_the_feed(self):
        data = self.feed()
        missing = [name for name, _why in EVENT_NAMES
                   if resolve(name, data) is KeyError]
        self.assertEqual(
            missing, [],
            'the prompt promises names the feed never sends: %s' % missing)

    def test_every_repeat_is_a_list_in_the_feed(self):
        data = self.feed()
        for key, _why, _fields in EVENT_REPEATS:
            self.assertIn(key, data, key)
            self.assertIsInstance(data[key], list, key)

    def test_every_bare_field_exists_on_a_row(self):
        data = self.feed()
        for key, _why, fields in EVENT_REPEATS:
            rows = data.get(key) or []
            self.assertTrue(rows, 'no %s in the fixture to check' % key)
            for field in fields:
                self.assertIn(field, rows[0], '%s.%s' % (key, field))

    def test_what_is_on_now_and_next_come_from_the_programme(self):
        """Typed by an operator, a screen behind a stage disagrees with the
        schedule the audience is holding within the hour."""
        data = self.feed()
        self.assertEqual(data['event']['now_on'], 'Opening panel')
        self.assertEqual(data['event']['room'], 'Main hall')
        self.assertEqual(data['event']['next_on'], 'Cosplay judging')
        self.assertEqual(data['event']['next_room'], 'Studio 2')

    def test_the_prompt_text_lists_exactly_those_names(self):
        for name, _why in EVENT_NAMES:
            self.assertIn(name, DESIGNER_PROMPT_EVENT, name)
        for key, _why, _fields in EVENT_REPEATS:
            self.assertIn(key, DESIGNER_PROMPT_EVENT, key)

    def test_the_prompt_does_not_describe_the_other_kind(self):
        self.assertNotIn('team.', DESIGNER_PROMPT_EVENT)
        self.assertNotIn('tournament.', DESIGNER_PROMPT_EVENT)

    def test_the_feed_carries_a_version(self):
        self.assertIn('version', self.feed())

    def test_the_version_moves_when_the_programme_does(self):
        before = self.feed()['version']
        EventSession.objects.create(
            event=self.event, title='Closing', stage='Main hall',
            starts_at=timezone.now() + timezone.timedelta(hours=4))
        self.assertNotEqual(before, self.feed()['version'])
