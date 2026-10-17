# -*- coding: utf-8 -*-
"""Blueprints and recurring tournaments (P7, design section 16).

The design's row for P7: "blueprints and recurring tournaments". After
Challengermode's blueprints (research C5): a tournament set up once, whose
editions "look, feel and play out exactly like the Blueprint", with the start
time set each time, made hourly to monthly.

ScheduleTests pin the dates (no drift, the month end, the local clock across a
clock change); MakeTests what an edition is; RunDueTests the cron's rules (made
ahead by lead_days, the past skipped, a gone source pausing, twice makes one);
ApiTests the doors.
"""
import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Notification

from . import blueprints
from .models import Progression, Tournament, TournamentBlueprint, TournamentStage
from .tests import client_for, make_tournament, make_user

LAGOS = ZoneInfo('Africa/Lagos')
LONDON = ZoneInfo('Europe/London')


def a_blueprint(owner, tournament, **extra):
    values = dict(owner=owner, tournament=tournament, name='Friday Cup', every='week',
                  timezone='Africa/Lagos', lead_days=7)
    values.update(extra)
    return TournamentBlueprint.objects.create(**values)


class ScheduleTests(TestCase):
    def setUp(self):
        self.owner = make_user(7301)
        self.t = make_tournament(self.owner)

    def test_weekly_keeps_the_local_wall_time(self):
        # 18:00 in London is 17:00 UTC in October before the clocks go back and
        # 18:00 UTC after; the edition is at 18:00 on the wall both times.
        first = datetime(2026, 10, 17, 18, 0, tzinfo=LONDON)
        bp = a_blueprint(self.owner, self.t, timezone='Europe/London', first_start=first)
        third = blueprints.start_of(bp, 2)
        self.assertEqual(third.astimezone(LONDON).replace(tzinfo=None), datetime(2026, 10, 31, 18, 0))
        self.assertEqual(third.astimezone(ZoneInfo('UTC')).hour, 18)
        self.assertEqual(first.astimezone(ZoneInfo('UTC')).hour, 17)

    def test_month_end_clamps_without_drift(self):
        bp = a_blueprint(self.owner, self.t, every='month',
                         first_start=datetime(2027, 1, 31, 18, 0, tzinfo=LAGOS))
        wall = [blueprints.start_of(bp, k).astimezone(LAGOS).replace(tzinfo=None) for k in range(4)]
        self.assertEqual(wall, [datetime(2027, 1, 31, 18, 0), datetime(2027, 2, 28, 18, 0),
                                datetime(2027, 3, 31, 18, 0), datetime(2027, 4, 30, 18, 0)])

    def test_day_and_fortnight(self):
        first = datetime(2026, 11, 2, 20, 30, tzinfo=LAGOS)
        daily = a_blueprint(self.owner, self.t, every='day', first_start=first)
        self.assertEqual(blueprints.start_of(daily, 3), first + timedelta(days=3))
        fortnightly = a_blueprint(self.owner, self.t, every='fortnight', first_start=first)
        self.assertEqual(blueprints.start_of(fortnightly, 1), first + timedelta(days=14))

    def test_by_hand_schedules_nothing(self):
        bp = a_blueprint(self.owner, self.t, every='none',
                         first_start=datetime(2026, 11, 2, 20, 30, tzinfo=LAGOS))
        self.assertIsNone(blueprints.start_of(bp, 0))
        self.assertIsNone(blueprints.next_after(bp, timezone.now()))

    def test_next_after_is_strictly_after(self):
        first = datetime(2026, 11, 6, 19, 0, tzinfo=LAGOS)
        bp = a_blueprint(self.owner, self.t, first_start=first)
        self.assertEqual(blueprints.next_after(bp, first - timedelta(days=1)), first)
        self.assertEqual(blueprints.next_after(bp, first), first + timedelta(days=7))
        self.assertEqual(blueprints.next_after(bp, blueprints.start_of(bp, 5)),
                         blueprints.start_of(bp, 6))


class MakeTests(TestCase):
    def setUp(self):
        self.owner = make_user(7311)
        self.t = make_tournament(self.owner)
        start = self.t.start_date_and_time
        self.t.registration_opens_at = start - timedelta(days=5)
        self.t.registration_closes_at = start - timedelta(hours=2)
        self.t.save(update_fields=['registration_opens_at', 'registration_closes_at'])
        self.groups = TournamentStage.objects.create(tournament=self.t, order=0, label='Groups',
                                                     format='round_robin', groups=2, advances=2)
        self.playoff = TournamentStage.objects.create(
            tournament=self.t, order=1, label='Playoff', format='single_elimination',
            starts_at=start + timedelta(hours=6))
        Progression.objects.create(source=self.groups, target=self.playoff, place_to=2)
        self.when = start + timedelta(days=14)

    def test_an_edition_is_the_tournament_moved_to_its_date(self):
        bp = a_blueprint(self.owner, self.t)
        edition = blueprints.make_edition(bp, self.when)
        self.assertEqual(edition.tournament_title, 'Friday Cup #1')
        self.assertEqual(edition.start_date_and_time, self.when)
        self.assertEqual(edition.end_date_and_time, self.t.end_date_and_time + timedelta(days=14))
        self.assertEqual(edition.registration_opens_at, self.t.registration_opens_at + timedelta(days=14))
        self.assertEqual(edition.registration_closes_at, self.t.registration_closes_at + timedelta(days=14))
        playoff = edition.stages.get(label='Playoff')
        self.assertEqual(playoff.starts_at, self.playoff.starts_at + timedelta(days=14))
        self.assertTrue(Progression.objects.filter(source__tournament=edition,
                                                   target=playoff).exists())
        self.assertEqual((edition.blueprint_id, edition.blueprint_edition), (bp.pk, 1))
        self.assertEqual(edition.tournament_creator_id, self.owner.user_id)
        self.assertTrue(edition.is_draft)
        self.assertNotEqual(edition.slug, self.t.slug)
        self.assertTrue(edition.slug.startswith('friday-cup-1'))
        bp.refresh_from_db()
        self.assertEqual((bp.made, bp.next_number), (1, 2))

    def test_a_lineup_window_moves_with_the_edition(self):
        # An absolute window on the source would shut before the edition began.
        from vent_cards.models import LineupRules
        start = self.t.start_date_and_time
        LineupRules.objects.create(tournament=self.t, enabled=True,
                                   opens_at=start - timedelta(days=2), closes_at=start - timedelta(hours=1),
                                   changes_open_at=start + timedelta(hours=3),
                                   changes_close_at=start + timedelta(hours=4))
        edition = blueprints.make_edition(a_blueprint(self.owner, self.t), self.when)
        rules = LineupRules.objects.get(tournament=edition)
        self.assertEqual((rules.opens_at, rules.closes_at, rules.changes_open_at, rules.changes_close_at),
                         (self.when - timedelta(days=2), self.when - timedelta(hours=1),
                          self.when + timedelta(hours=3), self.when + timedelta(hours=4)))
        self.assertEqual(LineupRules.objects.get(tournament=self.t).opens_at, start - timedelta(days=2))

    def test_every_date_an_edition_copies_is_decided(self):
        # A date column added to anything duplicate() copies must be moved by
        # an edition (blueprints.MOVED), reset by duplicate (RESET or
        # SKIP_COLUMNS), or bookkeeping. Otherwise every edition carries the
        # source's date for it, which is the fault this test was written for.
        from django.db import models as dj
        from . import duplicate
        bookkeeping = {'created_at', 'updated_at'}
        tables = {'tournament': (Tournament, duplicate.SKIP_COLUMNS),
                  'stages': (TournamentStage, duplicate.RESET['TournamentStage'])}
        for name in duplicate.ONE_TO_ONE + duplicate.MANY:
            model = Tournament._meta.get_field(name).related_model
            tables[name] = (model, duplicate.RESET.get(model.__name__, set()))
        undecided = []
        for name, (model, reset) in tables.items():
            for field in model._meta.concrete_fields:
                if not isinstance(field, dj.DateTimeField) or field.name in bookkeeping | set(reset):
                    continue
                if field.name not in blueprints.MOVED.get(name, ()):
                    undecided.append('%s.%s' % (model.__name__, field.name))
        self.assertEqual(undecided, [], 'move it in blueprints.MOVED or reset it in duplicate')

    def test_published_when_the_blueprint_says(self):
        edition = blueprints.make_edition(a_blueprint(self.owner, self.t, publish=True), self.when)
        self.assertFalse(edition.is_draft)
        self.assertEqual(edition.status, 'registration_open')

    def test_the_owner_is_told(self):
        # Once the edition is saved for good, never for one rolled back.
        with self.captureOnCommitCallbacks(execute=True):
            edition = blueprints.make_edition(a_blueprint(self.owner, self.t), self.when)
        note = Notification.objects.filter(user=self.owner).latest('pk')
        self.assertIn(edition.slug, note.link)
        self.assertIn('Friday Cup #1', note.title)

    def test_numbering_starts_where_the_owner_says(self):
        edition = blueprints.make_edition(a_blueprint(self.owner, self.t, next_number=12), self.when)
        self.assertEqual(edition.tournament_title, 'Friday Cup #12')
        self.assertEqual(edition.blueprint_edition, 12)

    def test_ends_after_reached_is_finished(self):
        bp = a_blueprint(self.owner, self.t, ends_after=1)
        blueprints.make_edition(bp, self.when)
        with self.assertRaises(blueprints.BlueprintError) as caught:
            blueprints.make_edition(bp, self.when + timedelta(days=7))
        self.assertEqual(caught.exception.code, 'BLUEPRINT_FINISHED')
        self.assertEqual(Tournament.objects.filter(blueprint=bp).count(), 1)

    def test_a_start_in_the_past_is_refused(self):
        with self.assertRaises(blueprints.BlueprintError) as caught:
            blueprints.make_edition(a_blueprint(self.owner, self.t), timezone.now() - timedelta(hours=1))
        self.assertEqual(caught.exception.code, 'START_IN_PAST')

    def test_a_long_name_still_fits(self):
        limit = Tournament._meta.get_field('tournament_title').max_length
        edition = blueprints.make_edition(a_blueprint(self.owner, self.t, name='N' * 120,
                                                      next_number=1000), self.when)
        self.assertLessEqual(len(edition.tournament_title), limit)
        self.assertTrue(edition.tournament_title.endswith(' #1000'))


class RunDueTests(TestCase):
    def setUp(self):
        self.owner = make_user(7321)
        self.t = make_tournament(self.owner)
        self.now = timezone.now()

    def scheduled(self, first_start, **extra):
        bp = a_blueprint(self.owner, self.t, first_start=first_start, **extra)
        blueprints.reschedule(bp, self.now)
        return bp

    def test_due_editions_are_made_ahead_by_lead_days(self):
        first = self.now + timedelta(days=3)
        bp = self.scheduled(first)
        self.assertEqual(bp.next_start, first)
        made = blueprints.run_due(self.now)
        self.assertEqual([(r[0].pk, r[2]) for r in made], [(bp.pk, None)])
        self.assertEqual(made[0][1].start_date_and_time, first)
        bp.refresh_from_db()
        self.assertEqual(bp.next_start, first + timedelta(days=7))

    def test_not_due_yet(self):
        self.scheduled(self.now + timedelta(days=10))
        self.assertEqual(blueprints.run_due(self.now), [])
        self.assertEqual(Tournament.objects.count(), 1)

    def test_missed_starts_are_skipped(self):
        # The cron was down for three weeks: the starts already past are not
        # made as tournaments that began without anybody in them.
        first = self.now - timedelta(days=21) + timedelta(hours=2)
        bp = a_blueprint(self.owner, self.t, first_start=first, next_start=first)
        made = blueprints.run_due(self.now)
        self.assertEqual(len(made), 1)
        edition = made[0][1]
        self.assertGreater(edition.start_date_and_time, self.now)
        self.assertEqual(edition.start_date_and_time, first + timedelta(days=21))
        self.assertEqual(Tournament.objects.filter(blueprint=bp).count(), 1)

    def test_a_deleted_source_pauses(self):
        bp = self.scheduled(self.now + timedelta(days=2))
        Tournament.all_objects.filter(pk=self.t.pk).update(deleted_at=self.now)
        made = blueprints.run_due(self.now)
        self.assertEqual([(r[0].pk, r[1], r[2]) for r in made], [(bp.pk, None, 'BLUEPRINT_SOURCE_GONE')])
        bp.refresh_from_db()
        self.assertEqual((bp.active, bp.paused_reason), (False, 'BLUEPRINT_SOURCE_GONE'))
        self.assertEqual(Tournament.all_objects.filter(blueprint=bp).count(), 0)
        self.assertEqual(blueprints.run_due(self.now), [])

    def test_running_twice_makes_one(self):
        bp = self.scheduled(self.now + timedelta(days=2))
        blueprints.run_due(self.now)
        blueprints.run_due(self.now)
        self.assertEqual(Tournament.objects.filter(blueprint=bp).count(), 1)

    def test_two_due_at_once_are_both_made(self):
        first = self.now + timedelta(days=2)
        bp = self.scheduled(first, lead_days=14)
        blueprints.run_due(self.now)
        starts = sorted(Tournament.objects.filter(blueprint=bp).values_list('start_date_and_time', flat=True))
        self.assertEqual(starts, [first, first + timedelta(days=7)])
        bp.refresh_from_db()
        self.assertEqual(bp.next_start, first + timedelta(days=14))

    def test_ends_after_stops_the_schedule(self):
        bp = self.scheduled(self.now + timedelta(days=2), ends_after=1, lead_days=14)
        blueprints.run_due(self.now)
        bp.refresh_from_db()
        self.assertEqual(Tournament.objects.filter(blueprint=bp).count(), 1)
        self.assertIsNone(bp.next_start)

    def test_a_paused_blueprint_makes_nothing(self):
        bp = self.scheduled(self.now + timedelta(days=2))
        TournamentBlueprint.objects.filter(pk=bp.pk).update(active=False)
        self.assertEqual(blueprints.run_due(self.now), [])

    def test_the_command_dry_run_makes_nothing(self):
        self.scheduled(timezone.now() + timedelta(days=2))
        out = io.StringIO()
        call_command('make_due_editions', '--dry-run', stdout=out)
        self.assertIn('Friday Cup', out.getvalue())
        self.assertEqual(Tournament.objects.count(), 1)
        call_command('make_due_editions', stdout=io.StringIO())
        self.assertEqual(Tournament.objects.count(), 2)

    def test_the_dry_run_numbers_two_due_editions_apart(self):
        self.scheduled(timezone.now() + timedelta(days=2), lead_days=14)
        out = io.StringIO()
        call_command('make_due_editions', '--dry-run', stdout=out)
        self.assertIn('Friday Cup #1 ', out.getvalue())
        self.assertIn('Friday Cup #2 ', out.getvalue())


class ApiTests(TestCase):
    def setUp(self):
        self.owner = make_user(7341)
        self.other = make_user(7342)
        self.t = make_tournament(self.owner)
        self.first = (timezone.now() + timedelta(days=10)).replace(microsecond=0)

    def create(self, who=None, **body):
        values = {'tournament': self.t.slug, 'name': 'Friday Cup', 'every': 'week',
                  'first_start': self.first.isoformat(), 'timezone': 'Africa/Lagos', 'lead_days': 7}
        values.update(body)
        values = {k: v for k, v in values.items() if v is not None}
        return client_for(who or self.owner).post('/tournament/blueprints/', values, format='json')

    def token(self, **body):
        res = self.create(**body)
        self.assertEqual(res.status_code, 201, res.content[:300])
        return res.json()['data']['blueprint']['token']

    def at(self, token, suffix=''):
        return '/tournament/blueprints/%s/%s' % (token, suffix)

    def test_create_and_list(self):
        res = self.create()
        self.assertEqual(res.status_code, 201, res.content[:300])
        row = res.json()['data']['blueprint']
        self.assertTrue(row['token'].startswith('bp_'))
        self.assertEqual(row['tournament']['slug'], self.t.slug)
        self.assertEqual(row['every'], 'week')
        self.assertEqual(row['next_number'], 1)
        self.assertTrue(row['active'])
        self.assertEqual(when_of(row['next_start']), self.first)
        mine = client_for(self.owner).get('/tournament/blueprints/').json()['data']['blueprints']
        self.assertEqual([b['token'] for b in mine], [row['token']])
        theirs = client_for(self.other).get('/tournament/blueprints/').json()['data']['blueprints']
        self.assertEqual(theirs, [])

    def test_another_owners_token_is_404_like_a_missing_one(self):
        token = self.token()
        as_other = client_for(self.other)
        for res in (as_other.get(self.at(token)),
                    as_other.patch(self.at(token), {'name': 'Mine'}, format='json'),
                    as_other.delete(self.at(token)),
                    as_other.post(self.at(token, 'make/'), {}, format='json'),
                    client_for(self.owner).get(self.at('bp_00000000000000000000'))):
            self.assertEqual(res.status_code, 404, res.content[:200])
            self.assertEqual(res.json()['code'], 'BLUEPRINT_NOT_FOUND')
        self.assertEqual(TournamentBlueprint.objects.get(token=token).name, 'Friday Cup')
        self.assertEqual(Tournament.objects.count(), 1)

    def test_not_my_tournament_is_refused(self):
        res = self.create(who=self.other)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'ONLY_TOURNAMENT_ORGANIZER_CAN')
        self.assertFalse(TournamentBlueprint.objects.exists())

    def test_the_first_start_is_in_the_future_and_said(self):
        res = self.create(first_start=(timezone.now() - timedelta(hours=1)).isoformat())
        self.assertEqual((res.status_code, res.json()['code']), (400, 'START_IN_PAST'))
        res = self.create(first_start=None)
        self.assertEqual((res.status_code, res.json()['code']), (400, 'FIRST_START_REQUIRED'))
        res = self.create(first_start=None, every='none')
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.assertIsNone(res.json()['data']['blueprint']['next_start'])

    def test_bad_input_is_refused_by_field(self):
        for body, field in (({'every': 'hourly'}, 'every'), ({'lead_days': 99}, 'lead_days'),
                            ({'timezone': 'Mars/Olympus'}, 'timezone'), ({'name': 'x' * 121}, 'name')):
            res = self.create(**body)
            self.assertEqual((res.status_code, res.json().get('field')), (400, field), body)

    def test_make_the_next_one_now(self):
        token = self.token()
        res = client_for(self.owner).post(self.at(token, 'make/'), {}, format='json')
        self.assertEqual(res.status_code, 201, res.content[:300])
        made = res.json()['data']['tournament']
        self.assertEqual((made['title'], made['edition'], made['is_draft']), ('Friday Cup #1', 1, True))
        edition = Tournament.objects.get(slug=made['slug'])
        self.assertEqual(edition.start_date_and_time, self.first)
        detail = client_for(self.owner).get(self.at(token)).json()['data']
        self.assertEqual(when_of(detail['blueprint']['next_start']), self.first + timedelta(days=7))
        self.assertEqual([e['slug'] for e in detail['editions']], [made['slug']])

    def test_make_a_one_off_leaves_the_schedule(self):
        token = self.token()
        when = self.first + timedelta(days=3)
        res = client_for(self.owner).post(self.at(token, 'make/'), {'starts_at': when.isoformat()},
                                          format='json')
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.assertEqual(Tournament.objects.get(slug=res.json()['data']['tournament']['slug'])
                         .start_date_and_time, when)
        self.assertEqual(TournamentBlueprint.objects.get(token=token).next_start, self.first)

    def test_pause_and_resume(self):
        token = self.token()
        res = client_for(self.owner).patch(self.at(token), {'active': False}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertIsNone(res.json()['data']['blueprint']['next_start'])
        res = client_for(self.owner).post(self.at(token, 'make/'), {}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'BLUEPRINT_PAUSED'))
        res = client_for(self.owner).patch(self.at(token), {'active': True}, format='json')
        row = res.json()['data']['blueprint']
        self.assertEqual((row['active'], row['paused_reason']), (True, ''))
        self.assertEqual(when_of(row['next_start']), self.first)

    def test_resuming_never_schedules_a_slot_already_made(self):
        # Walk fault, 17 Oct (session A, Nigeria Finals): Make the next one now
        # made #1 for 24 Oct and moved the schedule to 31 Oct; Pause then
        # Resume put it back on 24 Oct, so the cron would make a second #1.
        token = self.token()
        as_owner = client_for(self.owner)
        made = as_owner.post(self.at(token, 'make/'), {}, format='json')
        self.assertEqual(made.status_code, 201, made.content[:300])
        week_two = self.first + timedelta(days=7)
        as_owner.patch(self.at(token), {'active': False}, format='json')
        row = as_owner.patch(self.at(token), {'active': True}, format='json').json()['data']['blueprint']
        self.assertEqual(when_of(row['next_start']), week_two)
        # An edit that keeps the cadence keeps the floor too.
        row = as_owner.patch(self.at(token), {'lead_days': 3}, format='json').json()['data']['blueprint']
        self.assertEqual(when_of(row['next_start']), week_two)
        # A one-off on a date of its own takes no slot from the schedule.
        as_owner.post(self.at(token, 'make/'), {'starts_at': (self.first + timedelta(days=3)).isoformat()},
                      format='json')
        row = as_owner.patch(self.at(token), {'lead_days': 4}, format='json').json()['data']['blueprint']
        self.assertEqual(when_of(row['next_start']), week_two)

    def test_resume_with_the_source_gone_is_refused(self):
        token = self.token()
        TournamentBlueprint.objects.filter(token=token).update(
            active=False, paused_reason='BLUEPRINT_SOURCE_GONE')
        Tournament.all_objects.filter(pk=self.t.pk).update(deleted_at=timezone.now())
        res = client_for(self.owner).patch(self.at(token), {'active': True}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'BLUEPRINT_SOURCE_GONE'))
        self.assertFalse(TournamentBlueprint.objects.get(token=token).active)

    def test_repoint_to_another_tournament(self):
        token = self.token()
        mine = make_tournament(self.owner)
        res = client_for(self.owner).patch(self.at(token), {'tournament': mine.slug}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['blueprint']['tournament']['slug'], mine.slug)
        theirs = make_tournament(self.other)
        res = client_for(self.owner).patch(self.at(token), {'tournament': theirs.slug}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (403, 'ONLY_TOURNAMENT_ORGANIZER_CAN'))

    def test_delete_keeps_the_editions(self):
        token = self.token()
        client_for(self.owner).post(self.at(token, 'make/'), {}, format='json')
        res = client_for(self.owner).delete(self.at(token))
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertFalse(TournamentBlueprint.objects.exists())
        edition = Tournament.objects.get(tournament_title='Friday Cup #1')
        self.assertIsNone(edition.blueprint_id)

    def test_signed_out_is_refused(self):
        self.assertEqual(self.client.get('/tournament/blueprints/').status_code, 401)
        res = self.client.post('/tournament/blueprints/', {}, content_type='application/json')
        self.assertEqual(res.status_code, 401)


def when_of(value):
    from django.utils.dateparse import parse_datetime
    return parse_datetime(value) if value else None
