"""Readable addresses, and the promise that old ones keep working.

Two properties matter. A link with the name in it resolves, and every link
already in the world - shared in a group chat, sitting in an August claim email,
bookmarked - resolves to the same thing it did before.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from vent_auth.models import Games, Teams, Users
from vent_auth.slugs import build_slug, lookup_kwargs
from vent_event.models import Event
from vent_tournament.models import Tournament


class SlugBuildingTests(TestCase):
    def setUp(self):
        self.game = Games.objects.get_or_create(game_title='Free Fire')[0]
        self.user = Users.objects.create(username='organiser', email='o@vent.test')

    def make_tournament(self, title):
        return Tournament.objects.create(
            tournament_title=title, tournament_game=self.game, tournament_creator=self.user,
            start_date_and_time=timezone.now(), end_date_and_time=timezone.now(),
        )

    def test_a_slug_is_made_from_the_name(self):
        t = self.make_tournament('Naija Free Fire Weekly #12')
        self.assertEqual(t.slug, 'naija-free-fire-weekly-12')

    def test_two_things_with_the_same_name_do_not_collide(self):
        first = self.make_tournament('Lagos Open')
        second = self.make_tournament('Lagos Open')
        self.assertEqual(first.slug, 'lagos-open')
        self.assertEqual(second.slug, 'lagos-open-2')

    def test_renaming_moves_the_address(self):
        """Reversed deliberately on 2026-08-26 (CEO): the URL follows the name.

        The old rule froze the slug so a shared link could not break, at the
        cost of an address that still carried last month's name. Both are
        gettable, because the retired slug is kept and redirects - see
        SlugHistoryTests below.
        """
        t = self.make_tournament('Original Name')
        self.assertEqual(t.slug, 'original-name')
        t.tournament_title = 'Something Else Entirely'
        t.save()
        t.refresh_from_db()
        self.assertEqual(t.slug, 'something-else-entirely')

    def test_a_rename_that_names_fields_still_writes_the_slug(self):
        """edit_tournament saves with update_fields, which would otherwise
        compute the new slug and silently drop it - the whole rename path."""
        t = self.make_tournament('Before')
        t.tournament_title = 'After'
        t.save(update_fields=['tournament_title'])
        t.refresh_from_db()
        self.assertEqual(t.slug, 'after')

    def test_a_name_with_nothing_sluggable_still_gets_an_address(self):
        t = self.make_tournament('中文名字')
        self.assertTrue(t.slug)
        self.assertRegex(t.slug, r'^[a-z0-9-]+$')

    def test_teams_and_events_get_them_too(self):
        team = Teams.objects.create(
            team_name='Lagos Rangers', game=self.game, description='',
            team_creator=self.user, team_owner=self.user, penalty_points=0, number_of_members=1,
        )
        self.assertEqual(team.slug, 'lagos-rangers')

        event = Event.objects.create(
            name='V-ENT Lagos Meetup', game=self.game, creator=self.user, event_type='physical',
            desc='Meetup', entry_fee=0, reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            event_date=timezone.now().date(), start_time=timezone.now().time(),
            end_time=timezone.now().time(),
        )
        self.assertEqual(event.slug, 'v-ent-lagos-meetup')


class LookupTests(TestCase):
    def test_a_number_is_read_as_an_id(self):
        self.assertEqual(lookup_kwargs('25', id_field='tournament_id'), {'tournament_id': 25})

    def test_anything_else_is_read_as_a_slug(self):
        self.assertEqual(
            lookup_kwargs('naija-weekly', id_field='tournament_id'), {'slug': 'naija-weekly'},
        )

    def test_whitespace_does_not_change_the_reading(self):
        self.assertEqual(lookup_kwargs('  25 ', id_field='tournament_id'), {'tournament_id': 25})


class ResolutionTests(TestCase):
    def setUp(self):
        self.game = Games.objects.get_or_create(game_title='Free Fire')[0]
        self.user = Users.objects.create(username='organiser2', email='o2@vent.test')
        self.tournament = Tournament.objects.create(
            tournament_title='Naija Weekly', tournament_game=self.game,
            tournament_creator=self.user, start_date_and_time=timezone.now(),
            end_date_and_time=timezone.now(), is_draft=False, tournament_visibility='public',
        )
        self.team = Teams.objects.create(
            team_name='Kano Falcons', game=self.game, description='',
            team_creator=self.user, team_owner=self.user, penalty_points=0, number_of_members=1,
        )

    def test_a_tournament_resolves_by_id_and_by_slug(self):
        by_id = self.client.get(f'/tournament/view-tournament/{self.tournament.tournament_id}/')
        by_slug = self.client.get(f'/tournament/view-tournament/{self.tournament.slug}/')
        self.assertEqual(by_id.status_code, 200)
        self.assertEqual(by_slug.status_code, 200)
        self.assertEqual(
            by_id.json()['data']['tournament_id'],
            by_slug.json()['data']['tournament_id'],
        )

    def test_a_team_resolves_by_id_and_by_slug(self):
        by_id = self.client.get(f'/team/get-team-details/{self.team.team_id}/')
        by_slug = self.client.get(f'/team/get-team-details/{self.team.slug}/')
        self.assertEqual(by_id.status_code, 200)
        self.assertEqual(by_slug.status_code, 200)
        self.assertEqual(
            by_id.json()['data']['team']['id'], by_slug.json()['data']['team']['id'],
        )

    def test_a_slug_that_does_not_exist_is_a_404_not_a_crash(self):
        res = self.client.get('/tournament/view-tournament/no-such-thing/')
        self.assertIn(res.status_code, (404, 400))

    def test_the_payload_carries_the_slug_so_links_can_be_built(self):
        res = self.client.get(f'/tournament/view-tournament/{self.tournament.tournament_id}/')
        self.assertEqual(res.json()['data']['slug'], 'naija-weekly')


class SlugHistoryTests(TestCase):
    """A link shared before a rename still has to open the right page."""

    def setUp(self):
        self.game = Games.objects.get_or_create(game_title='History Game')[0]
        self.user = Users.objects.create(username='hist_user', email='hist@example.com')

    def make_tournament(self, title):
        return Tournament.objects.create(
            tournament_title=title, tournament_game=self.game,
            tournament_creator=self.user,
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            tournament_type='online', tournament_access='individual',
            entry_fee='Free', is_draft=False, status='published',
        )

    def test_the_old_address_is_remembered(self):
        from vent_auth.models_slughistory import SlugHistory

        t = self.make_tournament('Lagos Invitational')
        t.tournament_title = 'Lagos Championship'
        t.save()
        self.assertTrue(
            SlugHistory.objects.filter(
                entity_type='tournament', slug='lagos-invitational', entity_id=t.tournament_id,
            ).exists()
        )

    def test_the_old_address_redirects_to_the_new_one(self):
        t = self.make_tournament('Kano Cup')
        t.tournament_title = 'Kano Masters'
        t.save()

        res = self.client.get(reverse('view_tournament', args=['kano-cup']))
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body['status'], 'moved')
        self.assertEqual(body['code'], 'SLUG_CHANGED')
        self.assertEqual(body['data']['slug'], 'kano-masters')
        self.assertEqual(body['data']['url'], '/tournaments/kano-masters')

    def test_the_new_address_serves_the_page(self):
        t = self.make_tournament('Abuja Open')
        t.tournament_title = 'Abuja Grand Open'
        t.save()
        res = self.client.get(reverse('view_tournament', args=['abuja-grand-open']))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['tournament_title'], 'Abuja Grand Open')

    def test_every_address_it_ever_had_keeps_working(self):
        t = self.make_tournament('First Name')
        for name in ('Second Name', 'Third Name', 'Fourth Name'):
            t.tournament_title = name
            t.save()

        for old in ('first-name', 'second-name', 'third-name'):
            res = self.client.get(reverse('view_tournament', args=[old]))
            body = res.json()
            self.assertEqual(body['status'], 'moved', f'{old} should point onward')
            self.assertEqual(body['data']['slug'], 'fourth-name')

    def test_renaming_back_does_not_point_the_page_at_itself(self):
        """A redirect loop is the obvious way to get this wrong."""
        t = self.make_tournament('Round Trip')
        t.tournament_title = 'Somewhere Else'
        t.save()
        t.tournament_title = 'Round Trip'
        t.save()
        t.refresh_from_db()
        self.assertEqual(t.slug, 'round-trip')

        res = self.client.get(reverse('view_tournament', args=['round-trip']))
        self.assertEqual(res.status_code, 200)

    def test_a_slug_freed_by_one_rename_and_taken_by_another_points_at_the_holder(self):
        first = self.make_tournament('Shared Name')
        first.tournament_title = 'Moved On'
        first.save()

        second = self.make_tournament('Shared Name')
        self.assertEqual(second.slug, 'shared-name')

        res = self.client.get(reverse('view_tournament', args=['shared-name']))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['tournament_id'], second.tournament_id)

    def test_an_id_that_never_existed_is_still_a_404(self):
        res = self.client.get(reverse('view_tournament', args=['999999']))
        self.assertEqual(res.status_code, 404)

    def test_a_slug_that_never_existed_is_still_a_404(self):
        res = self.client.get(reverse('view_tournament', args=['no-such-thing-anywhere']))
        self.assertEqual(res.status_code, 404)

    def test_events_and_teams_redirect_the_same_way(self):
        from vent_event.models import Event
        from vent_auth.models import Teams

        event = Event.objects.create(
            name='Ikeja Meetup', game=self.game, creator=self.user,
            event_type='virtual', desc='x', entry_fee=0,
            reg_start_date=timezone.now(), reg_end_date=timezone.now() + timedelta(days=1),
            event_date=(timezone.now() + timedelta(days=2)).date(),
            start_time='10:00', end_time='12:00',
        )
        event.name = 'Ikeja Gathering'
        event.save()
        res = self.client.get(reverse('view_event', args=['ikeja-meetup']))
        self.assertEqual(res.json()['status'], 'moved')
        self.assertEqual(res.json()['data']['url'], '/events/ikeja-gathering')

        team = Teams.objects.create(
            team_name='Old Guard', game=self.game, description='',
            team_creator=self.user, team_owner=self.user,
            penalty_points=0, number_of_members=1,
        )
        team.team_name = 'New Guard'
        team.save()
        res = self.client.get(reverse('view_team', args=['old-guard']))
        self.assertEqual(res.json()['status'], 'moved')
        self.assertEqual(res.json()['data']['url'], '/teams/new-guard')


    def test_a_move_is_not_an_http_redirect(self):
        """fetch() follows redirects transparently, so a 301 carrying a frontend
        path would be chased against the API host and arrive as a 404 with the
        body discarded. The move has to be readable in the body."""
        t = self.make_tournament('Body Not Header')
        t.tournament_title = 'Renamed Again'
        t.save()
        res = self.client.get(reverse('view_tournament', args=['body-not-header']))
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('Location', res)


class CommunityAndOrgSlugTests(TestCase):
    """No numeric id in any address, including the things people post."""

    def setUp(self):
        self.game = Games.objects.get_or_create(game_title='Slug Game 2')[0]
        self.user = Users.objects.create(username='slug_user2', email='su2@example.com')

    def test_a_club_gets_its_name_as_an_address(self):
        from vent_auth.models import Club

        club = Club.objects.create(name='Lagos Snipers', owner=self.user)
        self.assertEqual(club.slug, 'lagos-snipers')

    def test_renaming_a_club_moves_it_and_keeps_the_old_address(self):
        from vent_auth.models import Club
        from vent_auth.models_slughistory import SlugHistory

        club = Club.objects.create(name='Old Club Name', owner=self.user)
        club.name = 'New Club Name'
        club.save()
        club.refresh_from_db()
        self.assertEqual(club.slug, 'new-club-name')
        self.assertTrue(SlugHistory.objects.filter(
            entity_type='club', slug='old-club-name', entity_id=club.id).exists())

    def test_a_thread_gets_its_title_as_an_address(self):
        from vent_auth.models import Thread

        thread = Thread.objects.create(
            title='Who is the best IGL right now', body='Discuss.', author=self.user,
        )
        self.assertEqual(thread.slug, 'who-is-the-best-igl-right-now')

    def test_an_organization_gets_its_name_as_an_address(self):
        from vent_auth.models import Organization

        org = Organization.objects.create(
            org_name='Vermillion Encore', org_creator=self.user, org_owner=self.user,
        )
        self.assertEqual(org.slug, 'vermillion-encore')

    def test_a_post_gets_an_opaque_token_not_its_id(self):
        """A sequential id in a URL lets anybody walk the whole table by
        counting, which is how scrapers enumerate content."""
        from vent_auth.models import Post

        post = Post.objects.create(body='Nothing to name here.', author=self.user)
        self.assertTrue(post.slug.startswith('p_'))
        self.assertNotIn(str(post.id), post.slug)
        self.assertRegex(post.slug, r'^p_[a-z2-9]{10}$')

    def test_two_posts_do_not_get_the_same_token(self):
        from vent_auth.models import Post

        tokens = {
            Post.objects.create(body=f'Post {i}', author=self.user).slug
            for i in range(25)
        }
        self.assertEqual(len(tokens), 25)

    def test_a_token_never_changes_once_set(self):
        from vent_auth.models import Post

        post = Post.objects.create(body='Original', author=self.user)
        original = post.slug
        post.body = 'Edited afterwards'
        post.save()
        post.refresh_from_db()
        self.assertEqual(post.slug, original)

    def test_a_token_carries_no_easily_misread_characters(self):
        """Tokens get read aloud and typed by hand. l/1 and o/0 are the pairs
        that cause it, so the alphabet excludes them."""
        from vent_auth.models import Post

        for i in range(30):
            slug = Post.objects.create(body=f'x{i}', author=self.user).slug
            body = slug.split('_', 1)[1]
            self.assertFalse(set(body) & set('lo01'), f'{slug} contains a misreadable character')
