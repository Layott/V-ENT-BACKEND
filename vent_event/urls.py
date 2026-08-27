from django.urls import path
from django.conf import settings
from django.conf.urls.static import static

from .views import create_event, get_all_events, view_event, edit_event
from .views_tickets import (
    ticket_types, buy_ticket, my_tickets, check_in_ticket, event_attendees,
)
from .views_vendors import (
    event_vendors, vendor_detail, create_vendor, create_product,
    create_order, my_vendor_orders, vendor_orders, collect_order,
)
from .views_promos import (
    event_referrals, event_referral_detail, event_promos, event_promo_detail,
    event_managers, event_manager_detail, my_events,
)
from .views_linking import (
    event_tournaments, linkable_tournaments, link_tournament, unlink_tournament,
    set_shared_ticketing,
)


urlpatterns = [
    path("create-event/", create_event, name="create_event"),
    path("get-all-events/", get_all_events, name="get_all_events"),
    path("view-event/<str:event_id>/", view_event, name="view_event"),
    path("edit-event/<str:event_id>/", edit_event, name="edit_event"),
    # Vendor shops
    path("<str:event_id>/vendors/", event_vendors, name="event_vendors"),
    path("<str:event_id>/vendors/create/", create_vendor, name="create_vendor"),
    path("<str:event_id>/vendor/<int:vendor_id>/", vendor_detail, name="vendor_detail"),
    path("vendor/<int:vendor_id>/products/", create_product, name="create_vendor_product"),
    path("vendor/<int:vendor_id>/order/", create_order, name="create_vendor_order"),
    path("vendor/<int:vendor_id>/orders/", vendor_orders, name="vendor_orders"),
    path("vendor/order/<str:code>/collect/", collect_order, name="collect_vendor_order"),
    path("vendor-orders/", my_vendor_orders, name="my_vendor_orders"),

    # Ticketing
    path("my-tickets/", my_tickets, name="my_tickets"),
    path("my-events/", my_events, name="my_events"),
    path("ticket/<str:code>/check-in/", check_in_ticket, name="check_in_ticket"),
    path("<str:event_id>/ticket-types/", ticket_types, name="ticket_types"),
    path("<str:event_id>/buy-ticket/", buy_ticket, name="buy_ticket"),
    path("<str:event_id>/attendees/", event_attendees, name="event_attendees"),

    # Influencer links, promo codes, and who else may run the event
    path("<str:event_id>/referrals/", event_referrals, name="event_referrals"),
    path("<str:event_id>/referrals/<int:referral_id>/", event_referral_detail, name="event_referral_detail"),
    path("<str:event_id>/promos/", event_promos, name="event_promos"),
    path("<str:event_id>/promos/<int:promo_id>/", event_promo_detail, name="event_promo_detail"),
    path("<str:event_id>/managers/", event_managers, name="event_managers"),
    path("<str:event_id>/managers/<int:manager_id>/", event_manager_detail, name="event_manager_detail"),

    # Tournament linking
    path("<str:event_id>/tournaments/", event_tournaments, name="event_tournaments"),
    path("<str:event_id>/linkable-tournaments/", linkable_tournaments, name="linkable_tournaments"),
    path("<str:event_id>/link-tournament/", link_tournament, name="link_tournament"),
    path("<str:event_id>/unlink-tournament/", unlink_tournament, name="unlink_tournament"),
    path("<str:event_id>/tournament/<int:tournament_id>/ticketing/", set_shared_ticketing,
         name="set_shared_ticketing"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
