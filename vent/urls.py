"""
URL configuration for vent project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include

from vent_partners import urls as partner_urls
from vent_tournament import views_overlays as overlay_views
from vent_event import views_short_links as short_link_views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    # The URL an organiser pastes into OBS or vMix. Root-mounted and short,
    # because it is typed into a machine at a venue, and public by token,
    # because a browser source cannot sign in.
    path("overlay/<str:token>/", overlay_views.serve_overlay, name="serve_overlay"),
    # A shortened ticket link. Root-mounted and two characters long because the
    # length of the address is the entire feature: it is read aloud on a
    # livestream and printed on a flyer.
    path("s/<str:token>/", short_link_views.resolve_short_link,
         name="resolve_short_link"),
    path("auth/", include('vent_auth.urls')),
    path("tournament/", include('vent_tournament.urls')),
    path("event/", include('vent_event.urls')),
    path("team/", include('vent_team.urls')),
    # Root-mounted /setting/, /device/, /user/<id>/update/ for the settings page.
    # The partner API is versioned and mounted separately, because it is the one
    # surface outside developers build against and its URLs must stay put.
    path("api/v1/", include((partner_urls.api_urlpatterns, 'partner_api'))),
    path("partners/", include((partner_urls.partner_urlpatterns, 'partners'))),
    path("", include('vent_auth.urls_settings')),
]

# Serve uploaded media at the canonical MEDIA_URL (/media/) in dev. The per-app
# urls.py also append static() but those only serve under their mount prefix
# (/auth/media/, ...), so Django-generated `.url` / build_absolute_uri paths
# (which are /media/...) 404 without this root-level serve. Admin KYC review is
# the first surface to render a /media/ image directly, which exposed the gap.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
