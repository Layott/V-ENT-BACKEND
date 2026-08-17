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
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("auth/", include('vent_auth.urls')),
    path("tournament/", include('vent_tournament.urls')),
    path("event/", include('vent_event.urls')),
    path("team/", include('vent_team.urls')),
    # Root-mounted /setting/, /device/, /user/<id>/update/ for the settings page.
    path("", include('vent_auth.urls_settings')),
]

# Serve uploaded media at the canonical MEDIA_URL (/media/) in dev. The per-app
# urls.py also append static() but those only serve under their mount prefix
# (/auth/media/, ...), so Django-generated `.url` / build_absolute_uri paths
# (which are /media/...) 404 without this root-level serve. Admin KYC review is
# the first surface to render a /media/ image directly, which exposed the gap.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
