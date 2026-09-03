from django.urls import path, include
from .views import *
from .views_standings import (
    record_fixture, set_league_rules, standings, tie_detail,
)
from .views_checkin import check_in, check_in_status, close_check_in, extend_check_in
from .views_bracket import (
    generate_bracket, report_match_score, confirm_match_score, raise_dispute,
    match_detail, distribute_prizes, cancel_tournament, my_disputes,
)
from django.conf import settings
from django.conf.urls.static import static

from . import views_formats
from . import views_standings as standings_views
from vent_event import views_short_links as short_link_views
from . import views_running_order
from . import views_access
from . import views_invitations
from . import views_overlay_feed
from . import views_overlays
from . import views_squads
from vent_cards import views_lineups
from . import views_studio
from . import views_staff
from . import views_assets
from . import views_export
from . import views_mvp
from . import views_reminders
from . import views_scheduled
from . import views_requirements
from . import views_stages
from . import views_rules

# Every tournament route takes `<str:tournament_id>` and resolves a slug or an
# id through `lookup.find`. Twenty-six of them used to take `<int:>`, which does
# not match a slug, and the console addresses tournaments by slug because the
# slug rule says no numeric id appears in an address a person can see.
#
# Django answered 404, and the frontend reads a 404 as "the backend has not
# shipped this endpoint yet", so the organiser was shown "Pending BE deploy" for
# Cancel & Refund on a feature that had worked for months.
#
# Slug or id, everywhere. A route that takes only one of the two is a route half
# the product cannot call.
urlpatterns = [
    # The production studio. Operator side is tournament-scoped and signed in;
    # the browser-source side is token-scoped and public, mounted at the root.
    # Who may enter results, and what this viewer may do. See access.py.
    path("<str:tournament_id>/access/", views_staff.access, name="tournament_access"),
    path("<str:tournament_id>/staff/", views_staff.staff, name="tournament_staff"),
    path("<str:tournament_id>/staff/<int:user_id>/", views_staff.staff_remove,
         name="tournament_staff_remove"),
    # The studio's media library: clips and pictures uploaded once and
    # called on whenever. See views_assets.
    path("<str:tournament_id>/studio/assets/", views_assets.assets,
         name="studio_assets"),
    path("<str:tournament_id>/studio/assets/<int:asset_id>/",
         views_assets.asset_detail, name="studio_asset_detail"),
    path("<str:tournament_id>/studio/sessions/", views_studio.sessions,
         name="studio_sessions"),
    path("<str:tournament_id>/studio/sessions/<int:session_id>/",
         views_studio.session_detail, name="studio_session_detail"),
    path("<str:tournament_id>/studio/sessions/<int:session_id>/element/<str:kind>/",
         views_studio.element, name="studio_element"),

    # The catalogue the wizard asks its questions from. Public: somebody
    # deciding whether to run a tournament here should see what is
    # supported before they have an account.
    path('formats/', views_formats.format_catalogue, name='tournament_formats'),
    path('rule-presets/', views_rules.rule_presets, name='rule_presets'),
    # The organiser's own rules: read them, change them, put them back.
    path('<str:tournament_id>/rules/', views_rules.tournament_rules, name='tournament_rules'),
    # Who may enter, what they still owe, and the queue of what a person checks.
    path('<str:tournament_id>/stages/', views_stages.tournament_stages, name='tournament_stages'),
    path('<str:tournament_id>/stages/set/', views_stages.set_stages, name='set_stages'),
    path('<str:tournament_id>/stages/<int:stage_id>/advance/', views_stages.advance_stage, name='advance_stage'),
    # Short addresses for a tournament, the same mechanism the events side
    # uses. By slug as well as id, because this is reached from the share
    # dialog on a page addressed by name.
    # How this league is scored, and corrections to it. The table itself is
    # already served by the existing standings endpoint, now carrying the rest
    # of the columns rather than a second endpoint beside it.
    path('<str:tournament_id>/stat-settings/', standings_views.stat_settings,
         name='tournament_stat_settings'),
    path('<str:tournament_id>/league-adjustment/', standings_views.league_adjustment,
         name='tournament_league_adjustment'),
    path('<str:tournament_id>/head-to-head/', standings_views.head_to_head,
         name='tournament_head_to_head'),
    path('<str:tournament_id>/short-links/', short_link_views.tournament_short_links,
         name='tournament_short_links'),
    path('<str:tournament_id>/short-links/<int:link_id>/',
         short_link_views.delete_tournament_short_link,
         name='delete_tournament_short_link'),
    path('<str:tournament_id>/requirements/', views_requirements.entry_requirements, name='entry_requirements'),
    path('<str:tournament_id>/requirements/set/', views_requirements.set_entry_requirements, name='set_entry_requirements'),
    path('<str:tournament_id>/requirements/mine/', views_requirements.my_entry_status, name='my_entry_status'),
    path('<str:tournament_id>/requirements/<int:requirement_id>/submit/', views_requirements.submit_requirement, name='submit_requirement'),
    path('<str:tournament_id>/requirements/queue/', views_requirements.review_queue, name='requirement_queue'),
    path('<str:tournament_id>/requirements/queue/<int:submission_id>/', views_requirements.review_submission, name='review_submission'),
    path('<str:tournament_id>/rules/set/', views_rules.set_tournament_rules, name='set_tournament_rules'),
    path('<str:tournament_id>/rules/reset/', views_rules.reset_tournament_rules, name='reset_tournament_rules'),
    path('games/<int:game_id>/modes/', views_formats.game_modes, name='game_modes'),
    path('prize-rates/', prize_rates, name='prize_rates'),
    path("create-tournament/", create_tournament, name="create_tournament"),
    path("search-tournament/", search_tournament, name="search_tournament"),
    path("join-tournament/", join_tournament, name="join_tournament"),
    path("register-tournament/", join_tournament, name="register_tournament"),  # alias
    path("get-all-tournaments/", get_all_tournaments, name="get_all_tournaments"),
    path("view-tournament/<str:tournament_id>/", view_tournament, name="view_tournament"),
    path("view-user-drafted-tournaments/", view_user_drafted_tournaments, name="view_user_drafted_tournaments"),
    path("get-tournament-brackets/<str:tournament_id>/", get_tournament_brackets, name="get_tournament_brackets"),
    path("get-tournament-participants/<str:tournament_id>/", get_tournament_participants, name="get_tournament_participants"),
    path("update-bracket/<str:tournament_id>/", update_bracket, name="update_bracket"),
    path("get-organizer-tournaments/", get_organizer_tournaments, name="get_organizer_tournaments"),
    path("delete-draft/<str:tournament_id>/", delete_draft, name="delete_draft"),
    path("edit-tournament/<str:tournament_id>/", edit_tournament, name="edit_tournament"),

    # --- M1 lifecycle endpoints ------------------------------------------
    path("<str:tournament_id>/generate-bracket/", generate_bracket, name="generate_bracket"),
    path("<str:tournament_id>/distribute-prizes/", distribute_prizes, name="distribute_prizes"),
    path("<str:tournament_id>/cancel/", cancel_tournament, name="cancel_tournament"),
    path("match/<int:match_id>/", match_detail, name="match_detail"),
    path("match/<int:match_id>/report-score/", report_match_score, name="report_match_score"),
    path("match/<int:match_id>/confirm-score/", confirm_match_score, name="confirm_match_score"),
    path("match/<int:match_id>/raise-dispute/", raise_dispute, name="raise_dispute"),
    path("match/<int:match_id>/dispute/", raise_dispute, name="raise_dispute_alias"),  # contract-table alias
    path("my-disputes/", my_disputes, name="my_disputes"),

    # --- check-in ---------------------------------------------------------
    path("<str:tournament_id>/check-in/", check_in, name="check_in"),
    path("<str:tournament_id>/check-in/status/", check_in_status, name="check_in_status"),
    path("<str:tournament_id>/close-check-in/", close_check_in, name="close_check_in"),
    path("<str:tournament_id>/extend-check-in/", extend_check_in, name="extend_check_in"),

    # --- league: both tables, and the games inside a tie ------------------
    # Public, because a league table is the most shareable thing a tournament
    # produces and putting it behind a sign-in keeps the competition invisible.
    path("<str:tournament_id>/standings/", standings, name="tournament_standings"),
    path("<str:tournament_id>/export/", views_export.export_tournament,
         name="tournament_export"),
    # Telling entrants what they have to do, before they miss it.
    path("<str:tournament_id>/remind/", views_reminders.send_reminder,
         name="send_reminder"),
    path("<str:tournament_id>/remind/audience/",
         views_reminders.reminder_audience, name="reminder_audience"),
    # Reminders set now, for the platform to send later.
    path("<str:tournament_id>/remind/scheduled/",
         views_scheduled.scheduled_reminders, name="scheduled_reminders"),
    path("<str:tournament_id>/remind/scheduled/<int:reminder_id>/",
         views_scheduled.cancel_scheduled_reminder,
         name="cancel_scheduled_reminder"),
    # What counts as a good game here, the stat lines, and the award.
    path("<str:tournament_id>/metrics/", views_mvp.tournament_metrics,
         name="tournament_metrics"),
    path("<str:tournament_id>/matches/<int:match_id>/stats/",
         views_mvp.match_stats, name="match_stats"),
    path("<str:tournament_id>/mvp/", views_mvp.mvp_table, name="mvp_table"),
    path("<str:tournament_id>/mvp/award/", views_mvp.award_mvp,
         name="award_mvp"),
    # Codes anybody holding one can spend.
    path("<str:tournament_id>/invites/", views_access.invites, name="tournament_invites"),
    # Invitations addressed to a named player or a named team.
    path("<str:tournament_id>/invitations/", views_invitations.invitations,
         name="tournament_invitations"),
    # What the person who was invited can see. The organiser's list is theirs
    # alone, so without this a recipient has no way to reach their own.
    path("<str:tournament_id>/invitations/mine/", views_invitations.my_invitation,
         name="tournament_my_invitation"),
    path("<str:tournament_id>/invitations/<int:invitation_id>/",
         views_invitations.invitation_detail, name="tournament_invitation_detail"),
    path("<str:tournament_id>/invitations/<int:invitation_id>/respond/",
         views_invitations.respond, name="tournament_invitation_respond"),
    path("<str:tournament_id>/invites/download/", views_access.invites_download,
         name="tournament_invites_download"),
    path("<str:tournament_id>/registrations/", views_access.registrations,
         name="tournament_registrations_manage"),
    path("<str:tournament_id>/running-order/", views_running_order.running_order,
         name="tournament_running_order"),
    path("<str:tournament_id>/running-order/set/", views_running_order.set_running_order,
         name="set_tournament_running_order"),
    # A tournament in the shape a stream overlay consumes. Public and
    # cheap: it is polled by OBS at a venue for hours, and carries
    # nothing that is not already on the public tournament page.
    path("<str:tournament_id>/overlay-feed/", views_overlay_feed.overlay_feed,
         name="tournament_overlay_feed"),
    # A player's EAFC lineup for this tournament, and the deadline the
    # organiser sets for it.
    path("<str:tournament_id>/lineup/", views_lineups.my_lineup,
         name="tournament_my_lineup"),
    path("<str:tournament_id>/lineup/<str:username>/", views_lineups.player_lineup,
         name="tournament_player_lineup"),
    path("<str:tournament_id>/lineups/", views_lineups.tournament_lineups,
         name="tournament_lineups"),
    path("<str:tournament_id>/lineup-rules/", views_lineups.lineup_rules,
         name="tournament_lineup_rules"),
    # Sides assembled for one tournament out of people from anywhere, and
    # entrants an organiser puts in directly rather than by invitation.
    path("<str:tournament_id>/squads/", views_squads.squads,
         name="tournament_squads"),
    path("<str:tournament_id>/squads/<int:squad_id>/", views_squads.squad_detail,
         name="tournament_squad_detail"),
    path("<str:tournament_id>/squads/<int:squad_id>/members/",
         views_squads.squad_members, name="tournament_squad_members"),
    path("<str:tournament_id>/squads/<int:squad_id>/members/<str:username>/",
         views_squads.squad_member_detail, name="tournament_squad_member_detail"),
    path("<str:tournament_id>/squads/<int:squad_id>/enter/",
         views_squads.squad_enter, name="tournament_squad_enter"),
    path("<str:tournament_id>/entrants/", views_squads.entrants,
         name="tournament_entrants"),
    # Uploading an overlay and getting the URL that goes into OBS.
    path("<str:tournament_id>/overlays/", views_overlays.overlays,
         name="tournament_overlays"),
    path("<str:tournament_id>/overlays/<int:overlay_id>/",
         views_overlays.overlay_detail, name="tournament_overlay_detail"),
    path("<str:tournament_id>/overlays/<int:overlay_id>/rotate/",
         views_overlays.rotate, name="tournament_overlay_rotate"),
    path("tie/<int:tie_id>/", tie_detail, name="tie_detail"),
    path("tie/<int:tie_id>/record/", record_fixture, name="record_tie_fixture"),
    path("<str:tournament_id>/league-rules/", set_league_rules, name="set_league_rules"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)