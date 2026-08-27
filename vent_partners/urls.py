"""Partner routes.

Two mounts, on purpose:

- `/api/v1/...` is the partner-facing read API, authenticated by an API key.
- `/partners/...` is everything else: applying, managing keys, the admin queue,
  the SSO endpoints, and inbound sign-in. Those are authenticated the way the
  rest of the platform is, by a session bearer token.
"""
from django.urls import path

from . import views_api as api
from . import views_manage as manage
from . import views_sso as sso

api_urlpatterns = [
    path('', api.api_index),
    path('whoami/', api.whoami),

    path('events/', api.events_list),
    path('events/<int:event_id>/', api.event_detail),

    path('tournaments/', api.tournaments_list),
    path('tournaments/<int:tournament_id>/', api.tournament_detail),
    path('tournaments/<int:tournament_id>/participants/', api.tournament_participants),
    path('tournaments/<int:tournament_id>/bracket/', api.tournament_bracket),

    path('teams/', api.teams_list),
    path('teams/<int:team_id>/', api.team_detail),

    path('players/<str:username>/', api.player_detail),
    path('rankings/', api.rankings),
]

partner_urlpatterns = [
    # Applying and self-management
    path('scopes/', manage.scope_catalogue),
    path('apply/', manage.apply_partner),
    path('mine/', manage.my_partners),
    path('<int:partner_id>/update/', manage.update_partner),
    path('<int:partner_id>/keys/', manage.create_key),
    path('<int:partner_id>/keys/<int:key_id>/revoke/', manage.revoke_key),

    # Admin review
    path('admin/list/', manage.admin_list),
    path('admin/<int:partner_id>/review/', manage.admin_review),
    path('admin/<int:partner_id>/sso-review/', manage.admin_sso_review),
    path('admin/<int:partner_id>/scopes/', manage.admin_set_scopes),
    path('admin/<int:partner_id>/keys/', manage.admin_issue_key),
    path('admin/<int:partner_id>/keys/<int:key_id>/rotate/', manage.admin_rotate_key),

    # V-ENT as a sign-in provider
    path('sso/metadata/', sso.sso_metadata),
    path('sso/authorize-info/', sso.sso_authorize_info),
    path('sso/approve/', sso.sso_approve),
    path('sso/token/', sso.sso_token),
    path('sso/userinfo/', sso.sso_userinfo),

    # Signing in to V-ENT with an outside account
    path('inbound/providers/', sso.inbound_providers),
    path('inbound/<str:provider>/start/', sso.inbound_start),
    path('inbound/<str:provider>/callback/', sso.inbound_callback),
]
