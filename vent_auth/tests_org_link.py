"""Following an organisation showed an empty feed, for everybody.

CEO, 2 September 2026: "Users shuould be able to follow an organization, in
which that particular orgs events, tournaments and anything about that org
should show constantly."

Walking it signed in as a real account that already followed two
organisations, `/organization/following/feed/` returned zero items. The feed
was not the problem; it filters on `organization_id__in` and orders soonest
first, correctly. The problem was at the other end:

    tournaments with an organisation: 0 of 10
    events with an organisation:      0 of 5

`Tournament.tournament_organization` and `Event.organization` existed from the
beginning and **nothing anywhere could set them**. Not either creation wizard,
not either console, and neither create endpoint accepted the field. The follow
was a counter rather than a subscription, and the person who pressed it had no
way to tell.

There was also a booby trap in the one place the link was ever read:

    "tournament_organization": t.tournament_organization.name

`Organization` has `org_name`. That line raises `AttributeError` the first time
any tournament has an organisation, which is precisely the moment the feature
starts working.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import (Games, Organization, OrgFollower, OrgMember,
                              Users)
from vent_event.models import Event
from vent_tournament.models import Tournament


class OrgLinkTests(TestCase):
    def setUp(self):
        def account(username, token):
            user = Users.objects.create(
                username=username, email='%s@vent.test' % username,
                login_session_token=token[:16], is_active=True)
            user.login_session_created_at = timezone.now()
            user.save()
            return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}

        self.owner, self.owner_auth = account('linkOwner', 'link-owner-tok')
        self.manager, self.manager_auth = account('linkManager', 'link-mgr-tok')
        self.member, self.member_auth = account('linkMember', 'link-mem-tok')
        self.stranger, self.stranger_auth = account('linkStranger', 'link-str-tok')
        self.follower, self.follower_auth = account('linkFollower', 'link-fol-tok')

        self.org = Organization.objects.create(
            org_name='Vermillion Encore', org_creator=self.owner,
            org_owner=self.owner)
        OrgMember.objects.create(org=self.org, user=self.manager, role='manager')
        OrgMember.objects.create(org=self.org, user=self.member, role='member')

        self.game = Games.objects.create(game_title='Link FC')

    # ------------------------------------------------------------ the picker

    def test_the_picker_lists_the_organisations_i_can_speak_for(self):
        res = self.client.get('/organization/mine/', **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        names = [o['name'] for o in res.json()['data']['organizations']]
        self.assertEqual(names, ['Vermillion Encore'])

    def test_a_manager_may_speak_for_it_and_an_ordinary_member_may_not(self):
        """Putting the organisation's name on a tournament is speaking for it."""
        managers = self.client.get('/organization/mine/',
                                   **self.manager_auth).json()['data']['organizations']
        self.assertEqual(len(managers), 1)

        members = self.client.get('/organization/mine/',
                                  **self.member_auth).json()['data']['organizations']
        self.assertEqual(members, [])

    def test_somebody_in_no_organisation_sees_an_empty_list_not_an_error(self):
        """The wizards hide the field entirely for them, which is most people."""
        res = self.client.get('/organization/mine/', **self.stranger_auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['organizations'], [])

    def test_signed_out_is_refused(self):
        self.assertIn(self.client.get('/organization/mine/').status_code,
                      (401, 403))

    # ------------------------------------------------- the resolver's own rules

    def test_an_organisation_resolves_by_slug_by_id_and_by_name(self):
        from vent_auth import org_link

        for value in (self.org.slug, self.org.org_id, 'vermillion encore'):
            org, error = org_link.resolve(value, self.owner)
            self.assertIsNone(error, value)
            self.assertEqual(org, self.org, value)

    def test_asking_for_nothing_is_not_an_error(self):
        """Most tournaments belong to a person, not an organisation."""
        from vent_auth import org_link

        for value in (None, '', '  ', 'none', '0'):
            org, error = org_link.resolve(value, self.owner)
            self.assertIsNone(org)
            self.assertIsNone(error)

    def test_an_organisation_that_is_not_yours_is_refused_not_ignored(self):
        """Dropping it silently would create the tournament under the person's
        own name and tell them it worked. They find out when it never appears
        on the organisation."""
        from vent_auth import org_link

        org, error = org_link.resolve(self.org.slug, self.stranger)
        self.assertIsNone(org)
        self.assertEqual(error, 'ORG_NOT_YOURS')

    def test_an_organisation_that_does_not_exist_says_so(self):
        from vent_auth import org_link

        org, error = org_link.resolve('no-such-org', self.owner)
        self.assertIsNone(org)
        self.assertEqual(error, 'ORG_NOT_FOUND')

    # ------------------------------------------------------ end to end: events

    def event_body(self, **extra):
        start = timezone.now() + timezone.timedelta(days=14)
        body = {
            'name': 'Encore Con', 'event_type': 'physical', 'description': 'x',
            'start_date': start.isoformat(),
            'end_date': (start + timezone.timedelta(days=1)).isoformat(),
            'location': 'Lagos',
        }
        body.update(extra)
        return body

    def test_an_event_can_be_created_in_an_organisations_name(self):
        res = self.client.post(
            '/event/create-event/', data=self.event_body(organization=self.org.slug),
            content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 201, res.content[:400])
        self.assertEqual(Event.objects.get().organization, self.org)

    def test_a_stranger_cannot_create_an_event_in_its_name(self):
        res = self.client.post(
            '/event/create-event/', data=self.event_body(organization=self.org.slug),
            content_type='application/json', **self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'ORG_NOT_YOURS')
        self.assertEqual(Event.objects.count(), 0)

    def test_an_event_with_no_organisation_still_works(self):
        res = self.client.post(
            '/event/create-event/', data=self.event_body(),
            content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(Event.objects.get().organization)

    # ------------------------------------------------------------ the feed

    def test_following_an_organisation_shows_what_it_is_running(self):
        """The whole point, and what came back empty on production."""
        OrgFollower.objects.create(org=self.org, user=self.follower)

        Event.objects.create(
            name='Encore Con', creator=self.owner, event_type='physical',
            desc='x', entry_fee=Decimal('0'), organization=self.org,
            reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            event_date=timezone.now().date(),
            start_time=timezone.now().time(), end_time=timezone.now().time())
        Tournament.objects.create(
            tournament_title='Encore Cup', tournament_game=self.game,
            tournament_creator=self.owner, tournament_organization=self.org,
            start_date_and_time=timezone.now(),
            end_date_and_time=timezone.now(), is_draft=False)

        res = self.client.get('/organization/following/feed/',
                              **self.follower_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        items = res.json()['data']['items']
        titles = sorted(i['title'] for i in items)
        self.assertEqual(titles, ['Encore Con', 'Encore Cup'])

    def test_the_feed_carries_the_organisations_name(self):
        OrgFollower.objects.create(org=self.org, user=self.follower)
        Tournament.objects.create(
            tournament_title='Encore Cup', tournament_game=self.game,
            tournament_creator=self.owner, tournament_organization=self.org,
            start_date_and_time=timezone.now(),
            end_date_and_time=timezone.now(), is_draft=False)

        item = self.client.get('/organization/following/feed/',
                               **self.follower_auth).json()['data']['items'][0]
        self.assertEqual(item['organization']['name'], 'Vermillion Encore')

    def test_a_draft_does_not_appear_to_followers(self):
        """An organiser drafting something is not announcing it."""
        OrgFollower.objects.create(org=self.org, user=self.follower)
        Tournament.objects.create(
            tournament_title='Not announced yet', tournament_game=self.game,
            tournament_creator=self.owner, tournament_organization=self.org,
            start_date_and_time=timezone.now(),
            end_date_and_time=timezone.now(), is_draft=True)

        items = self.client.get('/organization/following/feed/',
                                **self.follower_auth).json()['data']['items']
        self.assertEqual(items, [])

    def test_following_nothing_says_so_rather_than_looking_broken(self):
        res = self.client.get('/organization/following/feed/',
                              **self.follower_auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['items'], [])
        self.assertIn('not following', res.json()['message'].lower())

    # --------------------------------------------------------- the booby trap

    def test_the_drafts_list_survives_a_draft_with_an_organisation(self):
        """`t.tournament_organization.name` raised AttributeError, because the
        field is `org_name`. It is read in view_user_drafted_tournaments and
        nowhere else, so it could only ever fire once something carried an
        organisation - which is to say, once the feature started working."""
        Tournament.objects.create(
            tournament_title='Encore Cup', tournament_game=self.game,
            tournament_creator=self.owner, tournament_organization=self.org,
            start_date_and_time=timezone.now(),
            end_date_and_time=timezone.now(), is_draft=True)

        res = self.client.get('/tournament/view-user-drafted-tournaments/',
                              **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertIn('Vermillion Encore', res.content.decode('utf-8'))
