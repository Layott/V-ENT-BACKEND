"""Party games at an event (inbox 434, 435, 439).

What is pinned here: the catalogue is the fifty games the CEO asked for, in the
order asked (Africa, then the UK, then worldwide); reading is public and playing
takes a live ticket for THIS event; places fill in order and then a waiting
list; a refunded or voided ticket leaves its games, by any path that saves it,
and the next person waiting moves up; only somebody who may run the event
changes a game.
"""
from datetime import time, timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users

from . import party_games
from .models import (Event, EventGame, EventGameEntry, EventManager, Ticket,
                     TicketTier)


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=('g-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def an_event(name, creator, game):
    now = timezone.localtime(timezone.now())
    return Event.objects.create(
        name=name, game=game, creator=creator, event_type='physical', desc='probe',
        entry_fee=0, reg_start_date=timezone.now(),
        reg_end_date=timezone.now() + timedelta(days=3),
        event_date=(now + timedelta(days=3)).date(),
        start_time=time(18, 0), end_time=time(22, 0), location='Lagos')


class CatalogueTests(TestCase):
    def test_fifty_games_africa_first(self):
        games = party_games.GAMES
        self.assertEqual(len(games), 50)
        regions = [g.region for g in games]
        self.assertEqual(regions.count('africa'), 20)
        self.assertEqual(regions.count('uk'), 12)
        self.assertEqual(regions.count('world'), 18)
        # Asked in that order: "africa and then uk and then we can go worldwide".
        self.assertEqual(regions, sorted(regions, key=party_games.REGIONS.index))

    def test_keys_are_unique_and_every_kind_is_known(self):
        keys = [g.key for g in party_games.GAMES]
        self.assertEqual(len(keys), len(set(keys)))
        for g in party_games.GAMES:
            self.assertIn(g.kind, party_games.KINDS, g.key)
            self.assertIn(g.region, party_games.REGIONS, g.key)
            if g.players_max is not None:
                self.assertGreaterEqual(g.players_max, g.players_min, g.key)

    def test_the_catalogue_answers_anybody(self):
        res = self.client.get('/event/party-games/catalogue/')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(res.json()['data']['games']), 50)


class GamesBase(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('pg_org')
        self.stranger, self.stranger_auth = a_user('pg_other')
        self.member, self.member_auth = a_user('pg_member')
        title = Games.objects.create(game_title='EA FC PG')
        self.event = an_event('Games Probe', self.organiser, title)
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=100)
        self.guest = Ticket.objects.create(
            event=self.event, tier=self.tier, code='PG000001',
            attendee_name='Sipho M.', attendee_email='guest@example.com')
        self.member_ticket = Ticket.objects.create(
            event=self.event, tier=self.tier, code='PG000002',
            user=self.member, attendee_email=self.member.email)
        self.other_event = an_event('Somewhere Else', self.stranger, title)
        other_tier = TicketTier.objects.create(
            event=self.other_event, name='General', price=0, quantity=10)
        self.elsewhere = Ticket.objects.create(
            event=self.other_event, tier=other_tier, code='PG000099',
            attendee_email='elsewhere@example.com')

    def url(self, tail=''):
        return '/event/%s/games/%s' % (self.event.slug, tail)

    def add(self, payload, auth=None):
        return self.client.post(self.url(), payload, content_type='application/json',
                                **(self.auth if auth is None else auth))

    def a_game(self, **extra):
        res = self.add(dict({'catalogue_key': 'whot'}, **extra))
        self.assertEqual(res.status_code, 201, res.content)
        return EventGame.objects.get(pk=res.json()['data']['game']['id'])

    def join(self, game, code=None, auth=None):
        body = {'ticket_code': code} if code else {}
        return self.client.post(self.url('%s/join/' % game.id), body,
                                content_type='application/json', **(auth or {}))

    def leave(self, game, code=None, auth=None):
        body = {'ticket_code': code} if code else {}
        return self.client.post(self.url('%s/leave/' % game.id), body,
                                content_type='application/json', **(auth or {}))

    def a_ticket(self, n):
        return Ticket.objects.create(event=self.event, tier=self.tier,
                                     code='PG1%05d' % n, attendee_name='P%d' % n)


class OrganiserTests(GamesBase):
    def test_the_organiser_adds_a_catalogue_game_and_their_own(self):
        res = self.add({'catalogue_key': 'whot', 'place': 'Hall B', 'places': 16,
                        'prize': 'Two VIP passes',
                        'starts_at': '2026-10-31T15:00:00+01:00'})
        self.assertEqual(res.status_code, 201, res.content)
        game = res.json()['data']['game']
        self.assertEqual(game['catalogue_key'], 'whot')
        self.assertEqual(game['catalogue']['region'], 'africa')
        self.assertEqual(game['places'], 16)
        self.assertEqual(res.json()['code'], 'GAME_ADDED')

        own = self.add({'custom_name': 'Cosplay trivia', 'custom_players': '4 to 20',
                        'custom_steps': 'Teams of four\nTen questions\nMost right wins'})
        self.assertEqual(own.status_code, 201, own.content)
        body = own.json()['data']['game']
        self.assertIsNone(body['catalogue'])
        self.assertEqual(body['custom']['name'], 'Cosplay trivia')
        self.assertEqual(len(body['custom']['steps']), 3)

    def test_a_game_not_in_the_catalogue_is_refused_by_code(self):
        res = self.add({'catalogue_key': 'chess-boxing'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'UNKNOWN_GAME')
        res = self.add({})
        self.assertEqual(res.json()['code'], 'GAME_NAME_REQUIRED')

    def test_nobody_else_adds_or_changes_a_game(self):
        res = self.add({'catalogue_key': 'whot'}, auth={})
        self.assertEqual(res.status_code, 401)
        res = self.add({'catalogue_key': 'whot'}, auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'NOT_ORGANIZER')
        game = self.a_game()
        for method in ('patch', 'delete'):
            res = getattr(self.client, method)(
                self.url('%s/' % game.id), {'places': 2},
                content_type='application/json', **self.stranger_auth)
            self.assertEqual(res.status_code, 403, method)
        res = self.client.get(self.url('%s/entries/' % game.id), **self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        res = self.client.get(self.url('%s/entries/' % game.id))
        self.assertEqual(res.status_code, 401)

    def test_a_stranger_gets_the_same_answer_for_a_game_that_does_not_exist(self):
        game = self.a_game()
        real = self.client.patch(self.url('%s/' % game.id), {}, content_type='application/json',
                                 **self.stranger_auth)
        missing = self.client.patch(self.url('999999/'), {}, content_type='application/json',
                                    **self.stranger_auth)
        self.assertEqual(real.status_code, missing.status_code)
        self.assertEqual(real.json()['code'], missing.json()['code'])

    def test_a_manager_the_organiser_added_runs_the_games_too(self):
        EventManager.objects.create(event=self.event, user=self.stranger,
                                    added_by=self.organiser)
        res = self.add({'catalogue_key': 'ludo'}, auth=self.stranger_auth)
        self.assertEqual(res.status_code, 201, res.content)

    def test_a_game_with_people_in_it_is_taken_off_not_deleted(self):
        game = self.a_game()
        self.join(game, code='PG000001')
        res = self.client.delete(self.url('%s/' % game.id), **self.auth)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'GAME_HAS_ENTRIES')
        res = self.client.patch(self.url('%s/' % game.id), {'status': 'off'},
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        public = self.client.get(self.url()).json()['data']['games']
        self.assertEqual(public, [])
        mine = self.client.get(self.url(), **self.auth).json()['data']['games']
        self.assertEqual(len(mine), 1)
        empty = self.a_game()
        res = self.client.delete(self.url('%s/' % empty.id), **self.auth)
        self.assertEqual(res.status_code, 200)

    def test_the_list_of_players_and_its_file(self):
        game = self.a_game(places=1)
        self.join(game, code='PG000001')
        self.join(game, auth=self.member_auth)
        res = self.client.get(self.url('%s/entries/' % game.id), **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        people = res.json()['data']['people']
        self.assertEqual([p['state'] for p in people], ['in', 'waiting'])
        self.assertEqual(people[0]['name'], 'Sipho M.')
        self.assertTrue(people[0]['guest'])
        csv = self.client.get(self.url('%s/entries/' % game.id), {'download': 'csv'}, **self.auth)
        self.assertEqual(csv.status_code, 200)
        self.assertTrue(csv['Content-Type'].startswith('text/csv'))
        self.assertIn(b'Sipho M.', csv.content)


class PlayingTests(GamesBase):
    def test_reading_is_public(self):
        self.a_game()
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        self.assertEqual(len(data['games']), 1)
        self.assertFalse(data['can_play'])
        self.assertEqual(data['tickets'], [])

    def test_a_guest_plays_with_their_ticket_code(self):
        game = self.a_game()
        res = self.join(game, code='pg000001')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['code'], 'GAME_JOINED')
        self.assertEqual(res.json()['data']['game']['mine']['PG000001']['state'], 'in')
        listed = self.client.post(self.url('mine/'), {'ticket_code': 'PG000001'},
                                  content_type='application/json')
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(listed.json()['data']['games'][0]['mine']['PG000001']['state'], 'in')

    def test_a_signed_in_holder_plays_with_their_own_ticket(self):
        game = self.a_game()
        res = self.join(game, auth=self.member_auth)
        self.assertEqual(res.status_code, 200, res.content)
        read = self.client.get(self.url(), **self.member_auth).json()['data']
        self.assertTrue(read['can_play'])
        self.assertEqual([t['code'] for t in read['tickets']], ['PG000002'])
        self.assertEqual(read['games'][0]['mine']['PG000002']['state'], 'in')

    def test_no_ticket_no_play(self):
        game = self.a_game()
        for res in (self.join(game), self.join(game, auth=self.stranger_auth),
                    self.join(game, code='PG000099'), self.join(game, code='NOPE')):
            self.assertEqual(res.status_code, 403, res.content)
            self.assertEqual(res.json()['code'], 'GAME_TICKET_REQUIRED')
        res = self.client.post(self.url('mine/'), {'ticket_code': 'PG000099'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 403)
        self.assertFalse(EventGameEntry.objects.exists())

    def test_a_refunded_ticket_cannot_join(self):
        game = self.a_game()
        self.guest.status = 'refunded'
        self.guest.save()
        res = self.join(game, code='PG000001')
        self.assertEqual(res.status_code, 403)

    def test_places_then_a_waiting_list_in_order(self):
        game = self.a_game(places=2)
        tickets = [self.a_ticket(n) for n in range(4)]
        codes = [self.join(game, code=t.code).json()['code'] for t in tickets]
        self.assertEqual(codes, ['GAME_JOINED', 'GAME_JOINED', 'GAME_WAITING', 'GAME_WAITING'])
        res = self.join(game, code=tickets[3].code)
        self.assertEqual(res.json()['data']['game']['mine'][tickets[3].code]['place_in_line'], 2)
        game_json = res.json()['data']['game']
        self.assertEqual((game_json['playing'], game_json['waiting'], game_json['full']),
                         (2, 2, True))

    def test_joining_twice_keeps_your_place(self):
        game = self.a_game(places=1)
        first, second = self.a_ticket(1), self.a_ticket(2)
        self.join(game, code=first.code)
        self.join(game, code=second.code)
        res = self.join(game, code=second.code)
        self.assertEqual(res.json()['data']['game']['mine'][second.code]['state'], 'waiting')
        self.assertEqual(EventGameEntry.objects.filter(game=game).count(), 2)

    def test_leaving_hands_the_place_to_the_first_in_line(self):
        game = self.a_game(places=1)
        a, b, c = self.a_ticket(1), self.a_ticket(2), self.a_ticket(3)
        for t in (a, b, c):
            self.join(game, code=t.code)
        res = self.leave(game, code=a.code)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['game']['mine'], {})
        states = dict(EventGameEntry.objects.filter(game=game)
                      .values_list('ticket__code', 'state'))
        self.assertEqual(states, {a.code: 'left', b.code: 'in', c.code: 'waiting'})
        # Coming back is to the back of the line.
        self.join(game, code=a.code)
        self.leave(game, code=b.code)
        states = dict(EventGameEntry.objects.filter(game=game)
                      .values_list('ticket__code', 'state'))
        self.assertEqual(states, {a.code: 'waiting', b.code: 'left', c.code: 'in'})

    def test_more_places_moves_people_up_and_fewer_throws_nobody_out(self):
        game = self.a_game(places=1)
        a, b, c = self.a_ticket(1), self.a_ticket(2), self.a_ticket(3)
        for t in (a, b, c):
            self.join(game, code=t.code)
        res = self.client.patch(self.url('%s/' % game.id), {'places': 2},
                                content_type='application/json', **self.auth)
        self.assertEqual(res.json()['data']['game']['playing'], 2)
        self.client.patch(self.url('%s/' % game.id), {'places': 1},
                          content_type='application/json', **self.auth)
        self.assertEqual(EventGameEntry.objects.filter(game=game, state='in').count(), 2)
        self.client.patch(self.url('%s/' % game.id), {'places': None},
                          content_type='application/json', **self.auth)
        self.assertEqual(EventGameEntry.objects.filter(game=game, state='in').count(), 3)

    def test_closed_sign_ups_and_a_cancelled_event_refuse_by_code(self):
        game = self.a_game()
        self.client.patch(self.url('%s/' % game.id), {'status': 'closed'},
                          content_type='application/json', **self.auth)
        res = self.join(game, code='PG000001')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'GAME_NOT_OPEN')
        game.status = EventGame.OPEN
        game.save()
        self.event.is_active = False
        self.event.save()
        res = self.join(game, code='PG000001')
        self.assertEqual(res.json()['code'], 'EVENT_CANCELLED')


class RefundDropsGamesTests(GamesBase):
    def test_a_voided_ticket_leaves_and_the_next_moves_up(self):
        game = self.a_game(places=1)
        waiting = self.a_ticket(7)
        self.join(game, code='PG000001')
        self.join(game, code=waiting.code)
        # Any path that saves the ticket refunded or cancelled: the admin void,
        # refunds.refund_ticket, the event being called off.
        self.guest.status = 'cancelled'
        self.guest.save(update_fields=['status'])
        states = dict(EventGameEntry.objects.filter(game=game)
                      .values_list('ticket__code', 'state'))
        self.assertEqual(states, {'PG000001': 'left', waiting.code: 'in'})

    def test_refund_ticket_drops_its_games(self):
        from .refunds import refund_ticket

        game = self.a_game()
        self.join(game, auth=self.member_auth)
        refund_ticket(self.member_ticket, 'probe', by=self.organiser)
        self.member_ticket.refresh_from_db()
        self.assertIn(self.member_ticket.status, ('refunded', 'cancelled'))
        self.assertEqual(EventGameEntry.objects.get(game=game).state, 'left')

    def test_a_check_in_keeps_the_place(self):
        game = self.a_game()
        self.join(game, code='PG000001')
        self.guest.status = 'checked_in'
        self.guest.save(update_fields=['status'])
        self.assertEqual(EventGameEntry.objects.get(game=game).state, 'in')
