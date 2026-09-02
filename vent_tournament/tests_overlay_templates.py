"""The shipped stream elements, and the one thing that can quietly ruin them.

An organiser picks "Standings", gets a URL, pastes it into OBS and it is on air
inside a minute. Nothing between here and there checks that the names inside
that file are names the feed sends, and a name that is wrong does not error: it
fills with an empty string. A standings board with every row blank looks like a
tournament with no entrants.

So every template is rendered here and every `data-vent` name in it is checked
against the same list the prompt promises, which `tests_overlay_vocabulary.py`
in turn checks against the real feed. The chain is: template -> vocabulary ->
feed, all three asserted, none of them able to drift alone.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_event.models import Event
from vent_tournament import overlay_binding, overlay_templates
from vent_tournament.models import Tournament, TournamentOverlay
from vent_tournament.views_overlays import (
    BINDINGS_FOR_EVENT, BINDINGS_FOR_TOURNAMENT,
    TEMPLATES_FOR_EVENT, TEMPLATES_FOR_TOURNAMENT)


class TemplateVocabularyTests(TestCase):
    def check(self, kind, keys, allowed):
        for key in keys:
            markup = overlay_templates.render(kind, key)
            self.assertIsNotNone(
                markup, '%s template "%s" is offered and does not exist' % (kind, key))
            _binding, fields, _warnings = overlay_binding.inspect(markup)
            self.assertTrue(fields, '%s/%s binds to nothing' % (kind, key))
            unknown = [f for f in fields if f not in allowed]
            self.assertEqual(
                unknown, [],
                '%s/%s uses names the feed does not send: %s' % (kind, key, unknown))

    def test_every_tournament_template_uses_real_names(self):
        self.check('tournament',
                   [t['key'] for t in TEMPLATES_FOR_TOURNAMENT],
                   BINDINGS_FOR_TOURNAMENT)

    def test_every_event_template_uses_real_names(self):
        self.check('event',
                   [t['key'] for t in TEMPLATES_FOR_EVENT],
                   BINDINGS_FOR_EVENT)

    def test_every_offered_template_can_actually_be_rendered(self):
        """A key in the list with no builder behind it is a button that fails
        when pressed, which is the worst moment to learn about it."""
        for row in TEMPLATES_FOR_TOURNAMENT:
            self.assertIn(row['key'], overlay_templates.TOURNAMENT_TEMPLATES)
        for row in TEMPLATES_FOR_EVENT:
            self.assertIn(row['key'], overlay_templates.EVENT_TEMPLATES)

    def test_nothing_is_built_that_is_not_offered(self):
        offered = {t['key'] for t in TEMPLATES_FOR_TOURNAMENT}
        self.assertEqual(set(overlay_templates.TOURNAMENT_TEMPLATES), offered)
        offered = {t['key'] for t in TEMPLATES_FOR_EVENT}
        self.assertEqual(set(overlay_templates.EVENT_TEMPLATES), offered)

    def test_a_template_paints_no_background_of_its_own(self):
        """It is composited over live video. A page with a background is a
        rectangle of colour covering the shot."""
        for kind, table in (('tournament', overlay_templates.TOURNAMENT_TEMPLATES),
                            ('event', overlay_templates.EVENT_TEMPLATES)):
            for key in table:
                markup = overlay_templates.render(kind, key)
                self.assertIn('background: transparent', markup, '%s/%s' % (kind, key))

    def test_a_template_is_built_for_the_broadcast_stage(self):
        for kind, table in (('tournament', overlay_templates.TOURNAMENT_TEMPLATES),
                            ('event', overlay_templates.EVENT_TEMPLATES)):
            for key in table:
                markup = overlay_templates.render(kind, key)
                self.assertIn('width: 1920px', markup, '%s/%s' % (kind, key))
                self.assertIn('height: 1080px', markup, '%s/%s' % (kind, key))

    def test_no_template_glows_or_loops(self):
        """The house rule, and it matters more here than anywhere: a graphic
        that breathes behind a caster looks cheap at any bitrate."""
        for kind, table in (('tournament', overlay_templates.TOURNAMENT_TEMPLATES),
                            ('event', overlay_templates.EVENT_TEMPLATES)):
            for key in table:
                markup = overlay_templates.render(kind, key).lower()
                # `box-shadow: 0 0 <n> <colour>` is the centred bloom the ban
                # is about. A bare "0 0 " also appears in `margin: 0 0 6px`,
                # and a checker that reports that is a checker people learn to
                # ignore, which is worse than not having one.
                for banned in ('infinite', 'animation-iteration',
                               'box-shadow: 0 0', 'drop-shadow(0 0',
                               'blur(', 'pulse', 'shimmer', 'text-shadow'):
                    self.assertNotIn(banned, markup,
                                     '%s/%s contains %r' % (kind, key, banned))

    def test_a_template_passes_the_uploads_own_inspection(self):
        """Whatever an upload would be refused for, a shipped file must not
        do either. Otherwise we ship the thing we tell organisers not to."""
        for kind, table in (('tournament', overlay_templates.TOURNAMENT_TEMPLATES),
                            ('event', overlay_templates.EVENT_TEMPLATES)):
            for key in table:
                markup = overlay_templates.render(kind, key)
                binding, _fields, warnings = overlay_binding.inspect(markup)
                self.assertEqual(binding, overlay_binding.MARKED,
                                 '%s/%s' % (kind, key))
                self.assertEqual(warnings, [], '%s/%s: %s' % (kind, key, warnings))


class StartFromATemplateTests(TestCase):
    def setUp(self):
        self.owner = Users.objects.create(
            username='tplOwner', email='to@vent.test',
            login_session_token='tpl-owner-tok'[:16], is_active=True)
        self.owner.login_session_created_at = timezone.now()
        self.owner.save()
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.owner.login_session_token}
        game = Games.objects.create(game_title='Template FC')
        self.tournament = Tournament.objects.create(
            tournament_title='Template Cup', tournament_game=game,
            tournament_creator=self.owner,
            start_date_and_time=timezone.now(),
            end_date_and_time=timezone.now(), is_draft=False)
        self.event = Event.objects.create(
            name='Template Con', creator=self.owner, event_type='physical',
            desc='x', entry_fee=Decimal('0'),
            reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            event_date=timezone.now().date(),
            start_time=timezone.now().time(), end_time=timezone.now().time())

    def test_an_organiser_can_start_a_tournament_element_without_uploading(self):
        res = self.client.post(
            '/tournament/%s/overlays/' % self.tournament.slug,
            data={'template': 'scorebar'},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 201, res.content[:400])
        overlay = res.json()['data']['overlay']
        self.assertIn('/overlay/', overlay['url'])
        self.assertIn('team.name', overlay['bound_fields'])
        self.assertEqual(TournamentOverlay.objects.get().tournament,
                         self.tournament)

    def test_an_organiser_can_start_an_event_element_without_uploading(self):
        res = self.client.post(
            '/event/%s/overlays/' % self.event.slug,
            data={'template': 'now_next'},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 201, res.content[:400])
        overlay = res.json()['data']['overlay']
        self.assertIn('event.now_on', overlay['bound_fields'])
        self.assertEqual(TournamentOverlay.objects.get().event, self.event)

    def test_the_row_says_what_it_is_bound_to(self):
        """An organiser running four tournaments and two events has one folder
        of HTML and no way to tell from a filename which URL is which."""
        self.client.post('/event/%s/overlays/' % self.event.slug,
                         data={'template': 'sponsors'},
                         content_type='application/json', **self.auth)
        row = self.client.get('/event/%s/overlays/' % self.event.slug,
                              **self.auth).json()['data']['overlays'][0]
        self.assertEqual(row['bound_to_kind'], 'event')
        self.assertEqual(row['bound_to'], 'Template Con')

    def test_a_template_that_does_not_exist_is_refused_by_name(self):
        res = self.client.post(
            '/event/%s/overlays/' % self.event.slug,
            data={'template': 'scorebar'},          # a tournament element
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'UNKNOWN_TEMPLATE')

    def test_a_started_element_serves_and_carries_the_runtime(self):
        self.client.post('/tournament/%s/overlays/' % self.tournament.slug,
                         data={'template': 'standings'},
                         content_type='application/json', **self.auth)
        token = TournamentOverlay.objects.get().token
        res = self.client.get('/overlay/%s/' % token)
        self.assertEqual(res.status_code, 200)
        body = res.content.decode('utf-8')
        self.assertIn('vent-overlay-runtime', body)
        self.assertIn('overlay-feed', body)

    def test_a_stranger_cannot_start_one(self):
        other = Users.objects.create(
            username='tplStranger', email='ts@vent.test',
            login_session_token='tpl-stranger'[:16], is_active=True)
        other.login_session_created_at = timezone.now()
        other.save()
        res = self.client.post(
            '/event/%s/overlays/' % self.event.slug,
            data={'template': 'now_next'}, content_type='application/json',
            HTTP_AUTHORIZATION='Bearer %s' % other.login_session_token)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(TournamentOverlay.objects.count(), 0)
