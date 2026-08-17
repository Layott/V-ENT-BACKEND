from django.urls import path
from django.conf import settings
from django.conf.urls.static import static

from .views import (
    team_list, my_teams, view_team,
    create_team, transfer_ownership, assign_new_role, remove_member,
    request_join, leave_team, promote_member, kick_member,
    join_requests, accept_request, reject_request,
    edit_team, membership_settings,
    team_tournaments, team_events,
)

urlpatterns = [
    # READ (FE calls these names)
    path("get-all-teams/", team_list, name="get_all_teams"),
    path("list-teams/", team_list, name="list_teams"),          # alias (spec name)
    path("my-teams/", my_teams, name="my_teams"),
    path("view-team/<int:team_id>/", view_team, name="view_team"),
    # Team profile activity tabs
    path("tournaments/<int:team_id>/", team_tournaments, name="team_tournaments"),
    path("events/<int:team_id>/", team_events, name="team_events"),
    path("get-team-details/<int:team_id>/", view_team, name="get_team_details"),  # alias (legacy name)

    # WRITE (existing - repointed to unified membership table)
    path("create-team/", create_team, name="create_team"),
    path("transfer-ownership/", transfer_ownership, name="transfer_ownership"),
    path("assign-new-role/", assign_new_role, name="assign_new_role"),
    path("remove-member/", remove_member, name="remove_member"),

    # WRITE (Part C - membership + join requests)
    path("request-join/<int:team_id>/", request_join, name="request_join"),
    path("leave/<int:team_id>/", leave_team, name="leave_team"),
    path("promote-member/", promote_member, name="promote_member"),
    path("kick-member/", kick_member, name="kick_member"),
    path("join-requests/<int:team_id>/", join_requests, name="join_requests"),
    path("accept-request/<int:request_id>/", accept_request, name="accept_request"),
    path("reject-request/<int:request_id>/", reject_request, name="reject_request"),

    # WRITE (Part C - team + membership settings edits)
    path("edit-team/<int:team_id>/", edit_team, name="edit_team"),
    path("membership-settings/<int:team_id>/", membership_settings, name="membership_settings"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
