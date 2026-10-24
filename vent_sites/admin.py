from django.contrib import admin

from .models import CustomDomain, EmbedLock, EmbedView, Site

admin.site.register(Site)
admin.site.register(EmbedLock)
admin.site.register(EmbedView)
admin.site.register(CustomDomain)
