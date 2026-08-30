"""Root-mounted routes for the /settings page (no /auth prefix).

The frontend calls these paths directly: /setting/, /device/…, /user/<id>/update/.
Included at root in vent/urls.py.
"""
from django.urls import path

from . import views_settings as v
from . import views_usersearch as usersearch
from . import views_profile as prof
from . import views_account_security as sec
from . import views_rankings as rank
from . import views_orgs as orgs
from . import views_community as community
from . import views_challenges as challenges

urlpatterns = [
    # Rankings (root-mounted: the /rankings page calls /ranking/ with no prefix)
    path('ranking/', rank.rankings),

    # Organizations (root-mounted: the FE calls /organization/...)
    path('organization/list/', orgs.org_list),
    path('organization/create/', orgs.org_create),
    # Literal routes FIRST. `<str:org_id>` matches a single segment, so
    # anything declared after it that is also a single segment is
    # unreachable: `linkable-teams` was, and answered 404 forever.
    path('organization/linkable-teams/', orgs.org_linkable_teams),
    path('organization/<str:org_id>/', orgs.org_detail),
    path('organization/<str:org_id>/members/', orgs.org_members),
    path('organization/<str:org_id>/promote/', orgs.org_promote),
    path('organization/<str:org_id>/kick/', orgs.org_kick),
    path('organization/<str:org_id>/apply/', orgs.org_apply),
    path('organization/<str:org_id>/requests/', orgs.org_requests),
    path('organization/<str:org_id>/approve-request/', orgs.org_approve_request),
    path('organization/<str:org_id>/reject-request/', orgs.org_reject_request),
    path('organization/<str:org_id>/follow/', orgs.org_follow),
    path('organization/<str:org_id>/request-verification/', orgs.org_request_verification),
    path('organization/<str:org_id>/teams/', orgs.org_teams),
    path('organization/<str:org_id>/link-team/', orgs.org_link_team),
    path('organization/<str:org_id>/unlink-team/', orgs.org_unlink_team),
    path('organization/<str:org_id>/tournaments/', orgs.org_tournaments),
    path('organization/<str:org_id>/events/', orgs.org_events),
    path('organization/<str:org_id>/activity/', orgs.org_activity),

    # Community (root-mounted: /post/, /club/, /thread/, /scrim/, /dm/)
    path('post/list/', community.post_list),
    path('post/create/', community.post_create),
    path('post/<str:post_id>/', community.post_detail),
    path('post/<int:post_id>/like/', community.post_like),
    path('post/<int:post_id>/comment/', community.post_comment),

    path('club/list/', community.club_list),
    path('club/create/', community.club_create),
    path('club/<str:club_id>/', community.club_detail),
    path('club/<int:club_id>/join/', community.club_join),

    path('thread/list/', community.thread_list),
    path('thread/create/', community.thread_create),
    path('thread/<str:thread_id>/', community.thread_detail),
    path('thread/<int:thread_id>/reply/', community.thread_reply),
    path('thread/<int:thread_id>/upvote/', community.thread_upvote),
    path('thread/reply/<int:reply_id>/upvote/', community.thread_reply_upvote),

    path('scrim/games/', community.scrim_games),
    path('scrim/list/', community.scrim_list),
    path('scrim/create/', community.scrim_create),
    path('scrim/<int:scrim_id>/accept/', community.scrim_accept),
    # The rest of a challenge's life. Literal routes first; `<str:scrim_id>`
    # takes one segment, so anything single-segment after it is unreachable.
    path('scrim/history/<str:username>/', challenges.challenge_history),
    path('scrim/<str:scrim_id>/detail/', challenges.challenge_detail),
    path('scrim/<str:scrim_id>/talk/', challenges.challenge_conversation),
    path('scrim/<str:scrim_id>/result/', challenges.report_result),
    path('scrim/<str:scrim_id>/result/confirm/', challenges.confirm_result),

    path('dm/list/', community.dm_list),
    path('dm/new/send/', community.dm_send, {'conversation_id': 'new'}),
    # `str`, not `int`: the address is the conversation's token. A numeric id
    # still resolves, because links already shared have to keep working.
    path('dm/<str:conversation_id>/', community.dm_detail),
    path('dm/<str:conversation_id>/send/', community.dm_send),

    # Settings
    path('setting/', v.get_settings),
    path('setting/update/', v.update_settings),
    path('setting/notifications/update/', v.update_notifications),
    path('setting/privacy/update/', v.update_privacy),
    path('setting/security/update/', v.update_security),
    path('setting/payments/update/', v.update_payments),

    # Account
    path('user/<str:user_id>/update/', v.update_user_account),
    # str, not int: a profile is addressed by username. The numeric form
    # still resolves so links shared before this keep working.
    path('user/<str:user_id>/profile/', prof.public_profile),
    # Finding a person by name, so a direct message can be addressed by
    # picking somebody rather than by spelling their handle correctly.
    path('user/search/', usersearch.user_search),

    # Devices / sessions
    path('setting/login-activity/', v.login_activity),
    path('setting/username/', v.change_username),
    path('setting/account/', v.account_overview),
    # Two-factor for an ordinary account, and the danger zone.
    path('setting/2fa/status/', sec.twofactor_status),
    path('setting/2fa/begin/', sec.twofactor_begin),
    path('setting/2fa/confirm/', sec.twofactor_confirm),
    path('setting/2fa/disable/', sec.twofactor_disable),
    path('setting/export/', sec.export_data),
    path('setting/deactivate/', sec.deactivate_account),
    path('setting/delete/', sec.delete_account),
    path('setting/cancel-deletion/', sec.cancel_deletion),
    path('setting/founder-badge/', sec.founder_badge),
    path('device/list/', v.list_devices),
    path('device/<str:device_id>/revoke/', v.revoke_device),
]
