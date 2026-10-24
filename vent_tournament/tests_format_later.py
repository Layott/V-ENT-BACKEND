"""Choosing the format later (P4c, inbox 478).

CEO: "some tournament organizers, might not know the format they want to use for
an event, either the people who register choose the format, or based off the
amount of people who register, they can then pick what they want."

A stage is saved with its format undecided and one of three rules settles it:
the entrants vote, the confirmed count picks a band, or the organiser picks.
Until it is settled the stage cannot be drawn.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from . import format_later, stage_engine, stage_settings, stages
from .models import TournamentFormatVote, TournamentStage
from .tests import client_for, make_tournament, make_user, register


def rule(**over):
    base = {'decide_by': 'vote', 'options': ['round_robin', 'single_elimination']}
    base.update(over)
    return base


BANDS = [
    {'min': 0, 'max': 7, 'format': 'round_robin'},
    {'min': 8, 'max': 32, 'format': 'double_elimination'},
    {'min': 33, 'max': None, 'format': 'swiss'},
]


class Fixture(TestCase):
    base = 3000000

    def setUp(self):
        Fixture.base += 100
        self.org = make_user(self.base)
        self.t = make_tournament(self.org)
        self.next_user = self.base + 1

    def entrants(self, n):
        out = []
        for _ in range(n):
            self.next_user += 1
            out.append(register(self.t, make_user(self.next_user)))
        return out

    def stage(self, **settings):
        raw = rule(**settings) if settings or True else {}
        return TournamentStage.objects.create(
            tournament=self.t, order=0, label='Main', format='undecided',
            settings=stage_settings.clean('undecided', raw))

    def url(self, stage, tail=''):
        return '/tournament/%s/stages/%s/format/%s' % (self.t.tournament_id, stage.id, tail)

    def close(self):
        self.t.registration_closes_at = timezone.now() - timedelta(minutes=1)
        self.t.save(update_fields=['registration_closes_at'])


class RuleTests(TestCase):
    def test_a_stage_may_be_saved_undecided(self):
        plan = stages.plan([{'format': 'undecided', 'label': 'Main', 'settings': rule()}])
        self.assertEqual(plan[0]['format'], 'undecided')
        self.assertEqual(plan[0]['settings']['decide_by'], 'vote')
        self.assertEqual(plan[0]['settings']['options'], ['round_robin', 'single_elimination'])

    def test_undecided_is_a_state_not_a_catalogue_format(self):
        from . import formats
        self.assertNotIn('undecided', formats.FORMATS)
        self.assertNotIn('undecided', [f['key'] for f in formats.catalogue()])

    def _refused(self, settings):
        with self.assertRaises(stages.StageError) as caught:
            stages.plan([{'format': 'undecided', 'label': 'Main', 'settings': settings}])
        return caught.exception.code

    def test_a_vote_needs_two_to_five_options(self):
        self.assertEqual(self._refused(rule(options=['round_robin'])), 'UNDECIDED_OPTIONS_COUNT')
        six = ['round_robin', 'single_elimination', 'double_elimination', 'swiss', 'gsl', 'ladder']
        self.assertEqual(self._refused(rule(options=six)), 'UNDECIDED_OPTIONS_COUNT')

    def test_an_option_must_be_a_real_format_and_not_repeated(self):
        self.assertEqual(self._refused(rule(options=['round_robin', 'nonsense'])),
                         'UNDECIDED_OPTION_UNKNOWN')
        self.assertEqual(self._refused(rule(options=['round_robin', 'round_robin'])),
                         'UNDECIDED_OPTION_REPEATED')

    def test_the_way_of_deciding_must_be_one_of_three(self):
        self.assertEqual(self._refused(rule(decide_by='coin')), 'DECIDE_BY_UNKNOWN')

    def test_bands_must_not_overlap(self):
        bands = [{'min': 0, 'max': 8, 'format': 'round_robin'},
                 {'min': 8, 'max': 32, 'format': 'double_elimination'}]
        self.assertEqual(self._refused({'decide_by': 'bands', 'bands': bands}), 'BANDS_OVERLAP')

    def test_bands_are_required_and_each_names_a_real_format(self):
        self.assertEqual(self._refused({'decide_by': 'bands', 'bands': []}), 'BANDS_REQUIRED')
        self.assertEqual(self._refused({'decide_by': 'bands', 'bands': [
            {'min': 0, 'max': 4, 'format': 'nonsense'}]}), 'BAND_FORMAT_UNKNOWN')
        self.assertEqual(self._refused({'decide_by': 'bands', 'bands': [
            {'min': 9, 'max': 4, 'format': 'swiss'}]}), 'BAND_RANGE_INVALID')

    def test_apply_is_auto_or_confirm(self):
        self.assertEqual(self._refused({'decide_by': 'bands', 'bands': BANDS, 'apply': 'maybe'}),
                         'APPLY_UNKNOWN')

    def test_the_stages_around_an_undecided_one_still_have_to_fit(self):
        with self.assertRaises(stages.StageError) as caught:
            stages.plan([
                {'format': 'undecided', 'label': 'Main', 'advances': 4,
                 'settings': rule(options=['round_robin', 'aggregate_2v2'])},
                {'format': 'single_elimination', 'label': 'Final'},
            ])
        self.assertEqual(caught.exception.code, 'FORMAT_FEEDS_NOTHING')


class SavingTests(Fixture):
    def test_the_plan_door_saves_it_and_refuses_options_that_do_not_fit_the_size(self):
        self.t.max_number_of_teams = 3
        self.t.min_number_of_teams = 2
        self.t.save()
        c = client_for(self.org)
        path = '/tournament/%s/stages/set/' % self.t.tournament_id
        ok = c.put(path, {'stages': [{'format': 'undecided', 'label': 'Main',
                                      'settings': rule(options=['round_robin', 'single_elimination'])}]},
                   format='json')
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.data['data']['stages'][0]['format'], 'undecided')
        # Swiss needs four.
        sid = ok.data['data']['stages'][0]['id']
        bad = c.put(path, {'stages': [{'id': sid, 'format': 'undecided', 'label': 'Main',
                                       'settings': rule(options=['round_robin', 'swiss'])}]},
                    format='json')
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.data['code'], 'FORMAT_DOES_NOT_FIT')


class VoteTests(Fixture):
    def test_an_entrant_votes_once_and_may_change_it(self):
        stage = self.stage()
        a, b = self.entrants(2)
        c = client_for(a.user)
        first = c.post(self.url(stage, 'vote/'), {'format': 'round_robin'}, format='json')
        self.assertEqual(first.status_code, 200, first.content)
        again = c.post(self.url(stage, 'vote/'), {'format': 'single_elimination'}, format='json')
        self.assertEqual(again.status_code, 200)
        self.assertEqual(TournamentFormatVote.objects.filter(stage=stage).count(), 1)
        self.assertEqual(TournamentFormatVote.objects.get(stage=stage).choice, 'single_elimination')
        client_for(b.user).post(self.url(stage, 'vote/'), {'format': 'single_elimination'},
                                format='json')
        read = c.get(self.url(stage))
        self.assertEqual(read.data['data']['votes'], {'round_robin': 0, 'single_elimination': 2})
        self.assertEqual(read.data['data']['my_vote'], 'single_elimination')

    def test_only_an_entrant_may_vote(self):
        stage = self.stage()
        self.entrants(2)
        stranger = make_user(self.base + 90)
        r = client_for(stranger).post(self.url(stage, 'vote/'), {'format': 'round_robin'},
                                      format='json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data['code'], 'NOT_AN_ENTRANT')
        anon = self.client.post(self.url(stage, 'vote/'), {'format': 'round_robin'},
                                content_type='application/json')
        self.assertIn(anon.status_code, (401, 403))

    def test_the_format_must_be_one_that_is_offered(self):
        stage = self.stage()
        a, = self.entrants(1)
        r = client_for(a.user).post(self.url(stage, 'vote/'), {'format': 'swiss'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data['code'], 'FORMAT_NOT_OFFERED')

    def test_the_vote_closes_with_registration(self):
        stage = self.stage()
        a, = self.entrants(1)
        self.close()
        r = client_for(a.user).post(self.url(stage, 'vote/'), {'format': 'round_robin'},
                                    format='json')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['code'], 'VOTE_CLOSED')

    def test_a_stage_that_is_not_voted_on_refuses_votes(self):
        stage = self.stage(decide_by='organiser')
        a, = self.entrants(1)
        r = client_for(a.user).post(self.url(stage, 'vote/'), {'format': 'round_robin'},
                                    format='json')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['code'], 'NOT_A_VOTE')

    def test_counts_go_to_entrants_and_the_organiser_not_to_strangers(self):
        stage = self.stage()
        a, = self.entrants(1)
        client_for(a.user).post(self.url(stage, 'vote/'), {'format': 'round_robin'}, format='json')
        stranger = make_user(self.base + 91)
        self.assertIsNone(client_for(stranger).get(self.url(stage)).data['data']['votes'])
        self.assertIsNone(self.client.get(self.url(stage)).data['data']['votes'])
        self.assertEqual(client_for(self.org).get(self.url(stage)).data['data']['votes']['round_robin'], 1)

    def test_the_winner_is_settled_when_registration_closes(self):
        stage = self.stage()
        a, b, c = self.entrants(3)
        for reg, pick in ((a, 'round_robin'), (b, 'single_elimination'), (c, 'single_elimination')):
            client_for(reg.user).post(self.url(stage, 'vote/'), {'format': pick}, format='json')
        self.close()
        read = self.client.get('/tournament/%s/stages/' % self.t.tournament_id)
        row = read.data['data']['stages'][0]
        self.assertEqual(row['format'], 'single_elimination')
        decision = row['format_decision']
        self.assertFalse(decision['undecided'])
        self.assertEqual(decision['by'], 'vote')
        self.assertEqual(decision['counts'], {'round_robin': 1, 'single_elimination': 2})

    def test_a_tie_goes_to_the_organiser(self):
        stage = self.stage()
        a, b = self.entrants(2)
        client_for(a.user).post(self.url(stage, 'vote/'), {'format': 'round_robin'}, format='json')
        client_for(b.user).post(self.url(stage, 'vote/'), {'format': 'single_elimination'},
                                format='json')
        self.close()
        row = self.client.get('/tournament/%s/stages/' % self.t.tournament_id).data['data']['stages'][0]
        self.assertEqual(row['format'], 'undecided')
        self.assertEqual(row['format_decision']['needs_organiser'], 'tie')
        self.assertEqual(sorted(row['format_decision']['tied']), ['round_robin', 'single_elimination'])

    def test_no_votes_goes_to_the_organiser_too(self):
        stage = self.stage()
        self.entrants(2)
        self.close()
        row = self.client.get('/tournament/%s/stages/' % self.t.tournament_id).data['data']['stages'][0]
        self.assertEqual(row['format'], 'undecided')
        self.assertEqual(row['format_decision']['needs_organiser'], 'no_votes')


class BandTests(Fixture):
    def banded(self, apply):
        return self.stage(decide_by='bands', options=[], bands=BANDS, apply=apply)

    def test_auto_applies_the_band_for_the_confirmed_count_at_close(self):
        stage = self.banded('auto')
        self.entrants(9)
        self.close()
        row = self.client.get('/tournament/%s/stages/' % self.t.tournament_id).data['data']['stages'][0]
        self.assertEqual(row['format'], 'double_elimination')
        self.assertEqual(row['format_decision']['by'], 'bands')

    def test_only_confirmed_entrants_count(self):
        stage = self.banded('auto')
        regs = self.entrants(9)
        for reg in regs[:3]:
            reg.status = 'pending'
            reg.save(update_fields=['status'])
        self.close()
        row = self.client.get('/tournament/%s/stages/' % self.t.tournament_id).data['data']['stages'][0]
        self.assertEqual(row['format'], 'round_robin')

    def test_confirm_proposes_and_waits_for_a_press(self):
        stage = self.banded('confirm')
        self.entrants(9)
        self.close()
        row = self.client.get('/tournament/%s/stages/' % self.t.tournament_id).data['data']['stages'][0]
        self.assertEqual(row['format'], 'undecided')
        self.assertEqual(row['format_decision']['band_now']['format'], 'double_elimination')
        self.assertEqual(row['format_decision']['proposed'], 'double_elimination')
        pressed = client_for(self.org).post(self.url(stage, 'settle/'), {}, format='json')
        self.assertEqual(pressed.status_code, 200, pressed.content)
        stage.refresh_from_db()
        self.assertEqual(stage.format, 'double_elimination')

    def test_the_band_that_would_apply_now_is_shown_before_the_close(self):
        stage = self.banded('auto')
        self.entrants(3)
        row = self.client.get('/tournament/%s/stages/' % self.t.tournament_id).data['data']['stages'][0]
        self.assertEqual(row['format'], 'undecided')
        self.assertEqual(row['format_decision']['band_now']['format'], 'round_robin')

    def test_a_count_no_band_covers_goes_to_the_organiser(self):
        stage = self.stage(decide_by='bands', options=[], apply='auto',
                           bands=[{'min': 10, 'max': 20, 'format': 'swiss'}])
        self.entrants(3)
        self.close()
        row = self.client.get('/tournament/%s/stages/' % self.t.tournament_id).data['data']['stages'][0]
        self.assertEqual(row['format'], 'undecided')
        self.assertEqual(row['format_decision']['needs_organiser'], 'no_band')


class SettleTests(Fixture):
    def test_the_organiser_settles_by_hand(self):
        stage = self.stage(decide_by='organiser', options=[])
        r = client_for(self.org).post(self.url(stage, 'settle/'), {'format': 'swiss'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        stage.refresh_from_db()
        self.assertEqual(stage.format, 'swiss')
        self.assertEqual(stage.format_decision['by'], 'organiser')
        self.assertEqual(stage.settings, stage_settings.clean('swiss', {}))

    def test_somebody_else_cannot(self):
        stage = self.stage(decide_by='organiser', options=[])
        other = make_user(self.base + 92)
        r = client_for(other).post(self.url(stage, 'settle/'), {'format': 'swiss'}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data['code'], 'NOT_YOURS')

    def test_the_format_must_be_real_and_fit(self):
        stage = self.stage(decide_by='organiser', options=[])
        self.t.max_number_of_teams = 3
        self.t.save()
        c = client_for(self.org)
        r = c.post(self.url(stage, 'settle/'), {'format': 'nonsense'}, format='json')
        self.assertEqual(r.data['code'], 'FORMAT_UNKNOWN')
        r = c.post(self.url(stage, 'settle/'), {'format': 'swiss'}, format='json')
        self.assertEqual(r.data['code'], 'FORMAT_DOES_NOT_FIT')

    def test_a_vote_still_open_is_not_settled_by_hand(self):
        stage = self.stage()
        r = client_for(self.org).post(self.url(stage, 'settle/'),
                                      {'format': 'round_robin'}, format='json')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['code'], 'VOTE_STILL_OPEN')

    def test_after_a_tie_the_organiser_picks_one_of_the_options(self):
        stage = self.stage()
        a, b = self.entrants(2)
        client_for(a.user).post(self.url(stage, 'vote/'), {'format': 'round_robin'}, format='json')
        client_for(b.user).post(self.url(stage, 'vote/'), {'format': 'single_elimination'},
                                format='json')
        self.close()
        c = client_for(self.org)
        r = c.post(self.url(stage, 'settle/'), {'format': 'swiss'}, format='json')
        self.assertEqual(r.data['code'], 'FORMAT_NOT_OFFERED')
        r = c.post(self.url(stage, 'settle/'), {'format': 'round_robin'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)

    def test_a_settled_stage_cannot_be_settled_again(self):
        stage = self.stage(decide_by='organiser', options=[])
        c = client_for(self.org)
        c.post(self.url(stage, 'settle/'), {'format': 'swiss'}, format='json')
        again = c.post(self.url(stage, 'settle/'), {'format': 'round_robin'}, format='json')
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.data['code'], 'STAGE_NOT_UNDECIDED')


class DrawTests(Fixture):
    def test_the_engine_refuses_an_undecided_stage(self):
        stage = self.stage(decide_by='organiser', options=[])
        self.entrants(4)
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            stage_engine.draw(stage, self.org)
        self.assertEqual(caught.exception.code, 'STAGE_FORMAT_UNDECIDED')

    def test_the_draw_door_refuses_it_with_the_code(self):
        stage = self.stage(decide_by='organiser', options=[])
        self.entrants(4)
        r = client_for(self.org).post(
            '/tournament/%s/stages/%s/draw/' % (self.t.tournament_id, stage.id), {}, format='json')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['code'], 'STAGE_FORMAT_UNDECIDED')

    def test_once_settled_it_draws_as_that_format(self):
        stage = self.stage(decide_by='organiser', options=[])
        self.entrants(4)
        c = client_for(self.org)
        c.post(self.url(stage, 'settle/'), {'format': 'single_elimination'}, format='json')
        r = c.post('/tournament/%s/stages/%s/draw/' % (self.t.tournament_id, stage.id), {},
                   format='json')
        self.assertEqual(r.status_code, 201, r.content)

    def test_the_draw_door_applies_a_due_band_before_refusing(self):
        stage = self.stage(decide_by='bands', options=[], bands=BANDS, apply='auto')
        self.entrants(9)
        self.close()
        r = client_for(self.org).post(
            '/tournament/%s/stages/%s/draw/' % (self.t.tournament_id, stage.id), {}, format='json')
        self.assertNotEqual(r.data.get('code'), 'STAGE_FORMAT_UNDECIDED', r.content)
        stage.refresh_from_db()
        self.assertEqual(stage.format, 'double_elimination')
