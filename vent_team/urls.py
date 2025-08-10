from django.urls import path, include
from .views import *
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # path("admin/", admin.site.urls),
   path("create-team/", create_team, name="create_team"),
   path("get-team-details/<int:team_id>/", get_team_details, name="get_team_details"),
   path("transfer-ownership/", transfer_ownership, name="transfer_ownership"),
   path("assign-new-role/", assign_new_role, name="assign_new_role"),
    path("remove-member/", remove_member, name="remove_member"),

] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)