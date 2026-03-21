from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, connection
from vent_auth.models import Users


class Command(BaseCommand):
    help = 'Permanently delete a user and all their associated data. Use --dry-run to preview.'

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--email', type=str, help='Email address of the user to delete')
        group.add_argument('--id', type=int, help='User ID of the user to delete')
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be deleted without making any changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        try:
            if options['email']:
                user = Users.objects.get(email=options['email'])
            else:
                user = Users.objects.get(pk=options['id'])
        except Users.DoesNotExist:
            raise CommandError('No user found matching the provided email or ID.')

        self.stdout.write(f"User: {user.username} (id={user.user_id}, email={user.email})")

        tables = [
            ("vent_auth_users_groups", "users_id"),
            ("vent_auth_users_user_permissions", "users_id"),
            ("account_emailaddress", "user_id"),
            ("django_admin_log", "user_id"),
            ("authtoken_token", "user_id"),
            ("socialaccount_socialaccount", "user_id"),
            ("vent_auth_teammembers", "user_id"),
            ("vent_auth_usercommunity", "user_id"),
            ("vent_auth_favoritegames", "user_id"),
            ("vent_auth_usergenre", "user_id"),
            ("vent_auth_teams", "team_creator_id"),
            ("vent_auth_teams", "team_owner_id"),
            ("vent_auth_organization", "org_creator_id"),
            ("vent_auth_organization", "org_owner_id"),
            ("vent_auth_gameaccount", "user_id"),
            ("vent_auth_userwallet", "user_id"),
            ("vent_auth_achievement_awarded_to", "users_id"),
            ("vent_auth_sociallink", "user_id"),
            ("vent_auth_usergamestats", "user_id"),
            ("vent_auth_userinterests", "user_id"),
            ("vent_auth_userprofile", "user_id"),
            ("vent_auth_usergallery", "user_id"),
            ("vent_event_event", "creator_id"),
            ("vent_tournament_tournament", "tournament_creator_id"),
        ]

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be made.\n'))
            with connection.cursor() as cursor:
                for table, column in tables:
                    cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = %s", [user.user_id])
                    count = cursor.fetchone()[0]
                    if count:
                        self.stdout.write(f"  Would delete {count} row(s) from {table} ({column})")
            self.stdout.write(f"  Would delete user row: {user.username}")
            return

        self.stdout.write(self.style.WARNING('Deleting user and all related data...'))

        with transaction.atomic():
            with connection.cursor() as cursor:
                for table, column in tables:
                    cursor.execute(f"DELETE FROM {table} WHERE {column} = %s", [user.user_id])
            user.delete()

        self.stdout.write(self.style.SUCCESS(f"Successfully deleted user '{user.username}' and all related data."))
