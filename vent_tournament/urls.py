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
from . import views_rules

urlpatterns = [
    # The catalogue the wizard asks its questions from. Public: somebody
    # deciding whether to run a tournament here should see what is
    # supported before they have an account.
    path('formats/', views_formats.format_catalogue, name='tournament_formats'),
    path('rule-presets/', views_rules.rule_presets, name='rule_presets'),
    # The organiser's own rules: read them, change them, put them back.
    path('<int:tournament_id>/rules/', views_rules.tournament_rules, name='tournament_rules'),
    path('<int:tournament_id>/rules/set/', views_rules.set_tournament_rules, name='set_tournament_rules'),
    path('<int:tournament_id>/rules/reset/', views_rules.reset_tournament_rules, name='reset_tournament_rules'),
    path('games/<int:game_id>/modes/', views_formats.game_modes, name='game_modes'),
    path('prize-rates/', prize_rates, name='prize_rates'),
    path("create-tournament/", create_tournament, name="create_tournament"),
    path("search-tournament/", search_tournament, name="search_tournament"),
    path("join-tournament/", join_tournament, name="join_tournament"),
    path("register-tournament/", join_tournament, name="register_tournament"),  # alias
    path("get-all-tournaments/", get_all_tournaments, name="get_all_tournaments"),
    path("view-tournament/<str:tournament_id>/", view_tournament, name="view_tournament"),
    path("view-user-drafted-tournaments/", view_user_drafted_tournaments, name="view_user_drafted_tournaments"),
    path("get-tournament-brackets/<int:tournament_id>/", get_tournament_brackets, name="get_tournament_brackets"),
    path("get-tournament-participants/<int:tournament_id>/", get_tournament_participants, name="get_tournament_participants"),
    path("update-bracket/<int:tournament_id>/", update_bracket, name="update_bracket"),
    path("get-organizer-tournaments/", get_organizer_tournaments, name="get_organizer_tournaments"),
    path("delete-draft/<int:tournament_id>/", delete_draft, name="delete_draft"),
    path("edit-tournament/<int:tournament_id>/", edit_tournament, name="edit_tournament"),

    # --- M1 lifecycle endpoints ------------------------------------------
    path("<int:tournament_id>/generate-bracket/", generate_bracket, name="generate_bracket"),
    path("<int:tournament_id>/distribute-prizes/", distribute_prizes, name="distribute_prizes"),
    path("<int:tournament_id>/cancel/", cancel_tournament, name="cancel_tournament"),
    path("match/<int:match_id>/", match_detail, name="match_detail"),
    path("match/<int:match_id>/report-score/", report_match_score, name="report_match_score"),
    path("match/<int:match_id>/confirm-score/", confirm_match_score, name="confirm_match_score"),
    path("match/<int:match_id>/raise-dispute/", raise_dispute, name="raise_dispute"),
    path("match/<int:match_id>/dispute/", raise_dispute, name="raise_dispute_alias"),  # contract-table alias
    path("my-disputes/", my_disputes, name="my_disputes"),

    # --- check-in ---------------------------------------------------------
    path("<int:tournament_id>/check-in/", check_in, name="check_in"),
    path("<int:tournament_id>/check-in/status/", check_in_status, name="check_in_status"),
    path("<int:tournament_id>/close-check-in/", close_check_in, name="close_check_in"),
    path("<int:tournament_id>/extend-check-in/", extend_check_in, name="extend_check_in"),

    # --- league: both tables, and the games inside a tie ------------------
    # Public, because a league table is the most shareable thing a tournament
    # produces and putting it behind a sign-in keeps the competition invisible.
    path("<int:tournament_id>/standings/", standings, name="tournament_standings"),
    path("tie/<int:tie_id>/", tie_detail, name="tie_detail"),
    path("tie/<int:tie_id>/record/", record_fixture, name="record_tie_fixture"),
    path("<int:tournament_id>/league-rules/", set_league_rules, name="set_league_rules"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)