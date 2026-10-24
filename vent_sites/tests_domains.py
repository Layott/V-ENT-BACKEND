"""A record on the organiser's own address, through the API (P8c, level 3).

The CNAME lookup is replaced the way tests_verify replaces the resolver, so
nothing here touches the network. Roles: signed out (401), a signed-in
stranger (403), a non-Premium organiser (402 on add only), the Premium
organiser, and the two public doors the certificate tool and the middleware use.
"""
from unittest.mock import patch

from django.core.cache import cache

from . import domains, verify
from .models import CustomDomain
from .tests_views import Base, proven


class DomainBase(Base):
    def setUp(self):
        super().setUp()
        self.organiser.is_premium = True
        self.organiser.save()
        self.site = proven(self.organiser, 'theirsite.com')
        self.base = '/sites/tournament/%s/domains/' % self.tournament.slug

    def add(self, name='tournaments.theirsite.com', path=None):
        return self.post(path or self.base, {'hostname': name}, self.auth)

    def made(self, name='tournaments.theirsite.com', state='active'):
        return CustomDomain.objects.create(site=self.site, tournament=self.tournament,
                                           hostname=name, state=state)


class AddTests(DomainBase):
    def test_a_premium_organiser_adds_a_name_under_a_proven_site(self):
        response = self.add()
        self.assertEqual(response.status_code, 201)
        row = response.json()['data']['domains'][0]
        self.assertEqual((row['hostname'], row['state']), ('tournaments.theirsite.com', 'pending'))
        self.assertEqual(response.json()['data']['target'], 'sites.v-ent.co')

    def test_an_event_works_the_same_way(self):
        response = self.add(path='/sites/event/%s/domains/' % self.event.slug)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(CustomDomain.objects.get().event, self.event)

    def test_without_premium_it_is_refused_with_a_code(self):
        self.organiser.is_premium = False
        self.organiser.save()
        response = self.add()
        self.assertEqual((response.status_code, response.json()['code']), (402, 'CUSTOM_DOMAIN_NOT_PREMIUM'))
        self.assertEqual(CustomDomain.objects.count(), 0)
        # Seeing the panel is still allowed, and says it is not Premium.
        listing = self.client.get(self.base, **self.auth).json()['data']
        self.assertFalse(listing['premium'])

    def test_a_name_that_cannot_be_a_host_is_refused(self):
        for bad in ('localhost', '10.0.0.1', 'sites.v-ent.co', 'https://x.com/a b'):
            response = self.add(bad)
            self.assertEqual(response.status_code, 400, bad)
            self.assertEqual(response.json()['code'], 'CUSTOM_DOMAIN_BAD_HOST', bad)

    def test_a_name_outside_every_proven_site_is_refused(self):
        response = self.add('tournaments.elsewhere.com')
        self.assertEqual(response.json()['code'], 'CUSTOM_DOMAIN_NOT_PROVEN')

    def test_a_meta_tag_site_does_not_cover_a_domain(self):
        proven(self.organiser, 'metasite.com', method='meta')
        self.assertEqual(self.add('go.metasite.com').json()['code'], 'CUSTOM_DOMAIN_NOT_PROVEN')

    def test_somebody_elses_proven_site_does_not_cover_it(self):
        proven(self.stranger, 'theirs.org')
        self.assertEqual(self.add('go.theirs.org').json()['code'], 'CUSTOM_DOMAIN_NOT_PROVEN')

    def test_a_name_held_by_another_record_is_taken(self):
        CustomDomain.objects.create(site=self.site, event=self.event, hostname='tournaments.theirsite.com')
        response = self.add()
        self.assertEqual((response.status_code, response.json()['code']), (409, 'CUSTOM_DOMAIN_TAKEN'))

    def test_adding_the_same_name_twice_is_one_row(self):
        self.add()
        self.assertEqual(self.add().status_code, 200)
        self.assertEqual(CustomDomain.objects.count(), 1)

    def test_one_record_holds_three_at_most(self):
        for n in range(domains.MOST_DOMAINS):
            self.assertEqual(self.add('t%d.theirsite.com' % n).status_code, 201)
        self.assertEqual(self.add('t9.theirsite.com').json()['code'], 'CUSTOM_DOMAIN_LIMIT')


class CheckTests(DomainBase):
    def check(self, name='tournaments.theirsite.com'):
        return self.post('%s%s/check/' % (self.base, name), {}, self.auth)

    def test_the_right_cname_makes_it_active(self):
        self.made(state='pending')
        with patch.object(verify, 'cname_records', return_value=['sites.v-ent.co']) as lookup:
            response = self.check()
        lookup.assert_called_once_with('tournaments.theirsite.com')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CustomDomain.objects.get().state, 'active')

    def test_each_wrong_answer_has_its_own_code(self):
        self.made(state='pending')
        cases = ((['elsewhere.example.net'], 'CUSTOM_DOMAIN_CNAME_WRONG'),
                 ([], 'CUSTOM_DOMAIN_CNAME_MISSING'))
        for found, code in cases:
            with patch.object(verify, 'cname_records', return_value=found):
                response = self.check()
            self.assertEqual((response.status_code, response.json()['code']), (400, code))
            self.assertEqual(CustomDomain.objects.get().state, 'failed')
        with patch.object(verify, 'cname_records', side_effect=verify.Unreachable()):
            self.assertEqual(self.check().json()['code'], 'CUSTOM_DOMAIN_UNREACHABLE')

    def test_a_cname_that_goes_away_turns_an_active_address_off(self):
        self.made()
        with patch.object(verify, 'cname_records', return_value=[]):
            self.check()
        self.assertEqual(CustomDomain.objects.get().state, 'failed')
        self.assertEqual(self.client.get('/sites/tls-ask/?domain=tournaments.theirsite.com').status_code, 404)

    def test_another_records_domain_is_not_yours(self):
        CustomDomain.objects.create(site=self.site, event=self.event, hostname='ev.theirsite.com')
        with patch.object(verify, 'cname_records', return_value=['sites.v-ent.co']) as lookup:
            response = self.check('ev.theirsite.com')
        self.assertEqual((response.status_code, response.json()['code']), (404, 'CUSTOM_DOMAIN_NOT_YOURS'))
        lookup.assert_not_called()

    def test_remove_deletes_it(self):
        self.made()
        response = self.client.delete('%stournaments.theirsite.com/' % self.base, **self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CustomDomain.objects.count(), 0)
        again = self.client.delete('%stournaments.theirsite.com/' % self.base, **self.auth)
        self.assertEqual(again.status_code, 404)


class RoleTests(DomainBase):
    def test_signed_out_is_refused_on_every_organiser_door(self):
        self.made()
        name = 'tournaments.theirsite.com'
        for method, path in (('get', self.base), ('post', self.base),
                             ('post', '%s%s/check/' % (self.base, name)),
                             ('delete', '%s%s/' % (self.base, name))):
            response = getattr(self.client, method)(path)
            self.assertEqual(response.status_code, 401, (method, path))

    def test_a_stranger_changes_nothing(self):
        self.made()
        name = 'tournaments.theirsite.com'
        stranger = self.stranger_auth
        self.assertEqual(self.client.get(self.base, **stranger).status_code, 403)
        self.assertEqual(self.post(self.base, {'hostname': 'x.theirsite.com'}, stranger).status_code, 403)
        self.assertEqual(self.post('%s%s/check/' % (self.base, name), {}, stranger).status_code, 403)
        self.assertEqual(self.client.delete('%s%s/' % (self.base, name), **stranger).status_code, 403)
        self.assertEqual(CustomDomain.objects.count(), 1)


class PublicDoorTests(DomainBase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def ask(self, domain):
        return self.client.get('/sites/tls-ask/', {'domain': domain}).status_code

    def test_tls_ask_says_yes_only_for_an_active_address(self):
        self.made('live.theirsite.com')
        self.made('waiting.theirsite.com', state='pending')
        self.assertEqual(self.ask('live.theirsite.com'), 200)
        self.assertEqual(self.ask('LIVE.theirsite.com'), 200)
        self.assertEqual(self.ask('waiting.theirsite.com'), 404)
        self.assertEqual(self.ask('unknown.theirsite.com'), 404)
        self.assertEqual(self.ask('v-ent.co'), 404)
        self.assertEqual(self.ask(''), 404)

    def test_host_maps_an_active_address_to_its_record(self):
        self.made('live.theirsite.com')
        CustomDomain.objects.create(site=self.site, event=self.event, hostname='fest.theirsite.com', state='active')
        one = self.client.get('/sites/host/', {'host': 'live.theirsite.com'})
        self.assertEqual(one.json()['data'], {'kind': 'tournament', 'slug': self.tournament.slug})
        two = self.client.get('/sites/host/', {'host': 'fest.theirsite.com'})
        self.assertEqual(two.json()['data'], {'kind': 'event', 'slug': self.event.slug})

    def test_host_is_404_for_anything_else(self):
        self.made('waiting.theirsite.com', state='pending')
        for host in ('waiting.theirsite.com', 'nobody.theirsite.com', 'v-ent.co', ''):
            self.assertEqual(self.client.get('/sites/host/', {'host': host}).status_code, 404, host)

    def test_a_draft_tournament_is_not_served(self):
        self.tournament.is_draft = True
        self.tournament.save()
        self.made('live.theirsite.com')
        self.assertEqual(self.client.get('/sites/host/', {'host': 'live.theirsite.com'}).status_code, 404)

    def test_removing_an_address_shows_at_once(self):
        self.made('live.theirsite.com')
        self.assertEqual(self.client.get('/sites/host/', {'host': 'live.theirsite.com'}).status_code, 200)
        self.client.delete('%slive.theirsite.com/' % self.base, **self.auth)
        self.assertEqual(self.client.get('/sites/host/', {'host': 'live.theirsite.com'}).status_code, 404)
