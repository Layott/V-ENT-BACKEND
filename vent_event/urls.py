from django.urls import path, include
from .views import *
from django.conf import settings
from django.conf.urls.static import static


urlpatterns = [
    path("create-event/", create_event, name="create_event")
    path("get-all-events/", get_all_events, name="get_all_events")
    
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)