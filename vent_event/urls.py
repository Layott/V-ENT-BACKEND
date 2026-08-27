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
from .views_linking import (
    event_tournaments, linkable_tournaments, link_tournament, unlink_tournament,
    set_shared_ticketing,
)


urlpatterns = [
    path("create-event/", create_event, name="create_event"),
    path("get-all-events/", get_all_events, name="get_all_events"),
    path("view-event/<str:event_id>/", view_event, name="view_event"),
    path("edit-event/<int:event_id>/", edit_event, name="edit_event"),
    # Vendor shops
    path("<int:event_id>/vendors/", event_vendors, name="event_vendors"),
    path("<int:event_id>/vendors/create/", create_vendor, name="create_vendor"),
    path("<int:event_id>/vendor/<int:vendor_id>/", vendor_detail, name="vendor_detail"),
    path("vendor/<int:vendor_id>/products/", create_product, name="create_vendor_product"),
    path("vendor/<int:vendor_id>/order/", create_order, name="create_vendor_order"),
    path("vendor/<int:vendor_id>/orders/", vendor_orders, name="vendor_orders"),
    path("vendor/order/<str:code>/collect/", collect_order, name="collect_vendor_order"),
    path("vendor-orders/", my_vendor_orders, name="my_vendor_orders"),

    # Ticketing
    path("my-tickets/", my_tickets, name="my_tickets"),
    path("ticket/<str:code>/check-in/", check_in_ticket, name="check_in_ticket"),
    path("<int:event_id>/ticket-types/", ticket_types, name="ticket_types"),
    path("<int:event_id>/buy-ticket/", buy_ticket, name="buy_ticket"),
    path("<int:event_id>/attendees/", event_attendees, name="event_attendees"),

    # Tournament linking
    path("<int:event_id>/tournaments/", event_tournaments, name="event_tournaments"),
    path("<int:event_id>/linkable-tournaments/", linkable_tournaments, name="linkable_tournaments"),
    path("<int:event_id>/link-tournament/", link_tournament, name="link_tournament"),
    path("<int:event_id>/unlink-tournament/", unlink_tournament, name="unlink_tournament"),
    path("<int:event_id>/tournament/<int:tournament_id>/ticketing/", set_shared_ticketing,
         name="set_shared_ticketing"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
