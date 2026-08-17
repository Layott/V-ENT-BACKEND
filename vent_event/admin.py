from django.contrib import admin

from .models import Event, TicketTier, Sponsor, SocialLink, VendorInvite


class TicketTierInline(admin.TabularInline):
    model = TicketTier
    extra = 0


class SponsorInline(admin.TabularInline):
    model = Sponsor
    extra = 0


class SocialLinkInline(admin.TabularInline):
    model = SocialLink
    extra = 0


class VendorInviteInline(admin.TabularInline):
    model = VendorInvite
    extra = 0


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('event_id', 'name', 'event_type', 'category', 'start_date', 'is_active', 'is_featured')
    list_filter = ('event_type', 'category', 'is_active', 'is_featured')
    search_fields = ('name', 'desc', 'location')
    inlines = (TicketTierInline, SponsorInline, SocialLinkInline, VendorInviteInline)


admin.site.register(TicketTier)
admin.site.register(Sponsor)
admin.site.register(SocialLink)
admin.site.register(VendorInvite)
