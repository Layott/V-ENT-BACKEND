from django.urls import path, include
from .views import *
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("create-tournament/", create_tournament, name="create_tournament"),
    path("search-tournament/", search_tournament, name="search_tournament"),
    path("join-tournament/", join_tournament, name="join_tournament"),
    path("register-tournament/", join_tournament, name="register_tournament"),  # alias
    path("get-all-tournaments/", get_all_tournaments, name="get_all_tournaments"),
    path("view-tournament/<int:tournament_id>/", view_tournament, name="view_tournament"),
    path("view-user-drafted-tournaments/", view_user_drafted_tournaments, name="view_user_drafted_tournaments"),
    path("get-tournament-brackets/<int:tournament_id>/", get_tournament_brackets, name="get_tournament_brackets"),
    path("get-tournament-participants/<int:tournament_id>/", get_tournament_participants, name="get_tournament_participants"),
    path("update-bracket/<int:tournament_id>/", update_bracket, name="update_bracket"),
    path("get-organizer-tournaments/", get_organizer_tournaments, name="get_organizer_tournaments"),
    path("delete-draft/<int:tournament_id>/", delete_draft, name="delete_draft"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)