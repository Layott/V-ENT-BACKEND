from django.contrib import admin

from .models import EmbedLock, EmbedView, Site

admin.site.register(Site)
admin.site.register(EmbedLock)
admin.site.register(EmbedView)
