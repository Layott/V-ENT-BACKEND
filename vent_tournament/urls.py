from django.urls import path, include
from .views import *
from .views_bracket import (
    generate_bracket, report_match_score, confirm_match_score, raise_dispute,
    match_detail, distribute_prizes, cancel_tournament, my_disputes,
)
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
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
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)