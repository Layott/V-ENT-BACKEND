from django.db import transaction
from django.contrib.auth import get_user_model

User = get_user_model()

def delete_user_completely(user_id):
    from vent_auth.models import (
        UserProfile, UserWallet, TeamMembers, UserCommunity, FavoriteGames,
        UserGenre, Teams, Organization, GameAccount, Achievement,
        SocialLink, UserGameStats, UserInterests, UserGallery
    )
    from vent_event.models import Event
    from vent_tournament.models import Tournament
    from django.contrib.admin.models import LogEntry
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount
    from rest_framework.authtoken.models import Token

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        print(f"User with id {user_id} does not exist.")
        return

    with transaction.atomic():
        # Delete related data
        UserProfile.objects.filter(user_id=user_id).delete()
        UserWallet.objects.filter(user_id=user_id).delete()
        TeamMembers.objects.filter(user_id=user_id).delete()
        UserCommunity.objects.filter(user_id=user_id).delete()
        FavoriteGames.objects.filter(user_id=user_id).delete()
        UserGenre.objects.filter(user_id=user_id).delete()
        Teams.objects.filter(team_creator_id=user_id).delete()
        Teams.objects.filter(team_owner_id=user_id).delete()
        Organization.objects.filter(org_creator_id=user_id).delete()
        Organization.objects.filter(org_owner_id=user_id).delete()
        GameAccount.objects.filter(user_id=user_id).delete()
        Achievement.objects.filter(users_id=user_id).delete()
        SocialLink.objects.filter(user_id=user_id).delete()
        UserGameStats.objects.filter(user_id=user_id).delete()
        UserInterests.objects.filter(user_id=user_id).delete()
        UserGallery.objects.filter(user_id=user_id).delete()
        Event.objects.filter(creator_id=user_id).delete()
        Tournament.objects.filter(tournament_creator_id=user_id).delete()

        LogEntry.objects.filter(user_id=user_id).delete()
        EmailAddress.objects.filter(user_id=user_id).delete()
        SocialAccount.objects.filter(user_id=user_id).delete()
        Token.objects.filter(user_id=user_id).delete()

        # Groups and permissions (through tables)
        user.groups.clear()
        user.user_permissions.clear()

        # Finally, delete user
        user.delete()

        print(f"Successfully deleted user {user_id} and all related data.")


# Example usage, replace with actual user 


from django.db import transaction
from vent_auth.models import Users

@transaction.atomic
def delete_user_completely_by_email(email):
    try:
        user = Users.objects.get(email=email)
    except Users.DoesNotExist:
        print(f"No user found with email {email}")
        return

    user_id = user.user_id

    related_models = [
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
        ("vent_event_event", "creator_id"),
        ("vent_auth_usergallery", "user_id"),
        ("vent_tournament_tournament", "tournament_creator_id"),
    ]

    from django.db import connection
    with connection.cursor() as cursor:
        for table, column in related_models:
            cursor.execute(f"DELETE FROM {table} WHERE {column} = %s", [user_id])

    user.delete()
    print(f"User with email {email} and all related data deleted.")
