from django.urls import path, include
from .views import *
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # path("admin/", admin.site.urls),
    path("create-tournament/", create_tournament, name="create_tournament"),
    path("search-tournament/", search_tournament, name="search_tournament"),
    path("join-tournament/", join_tournament, name="join_tournament"),
    path("get-all-tournaments/", get_all_tournaments, name="get_all_tournaments")
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)