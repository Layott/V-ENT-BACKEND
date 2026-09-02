from django.urls import path
from django.conf import settings
from django.conf.urls.static import static

from . import views_guest
from vent_tournament import views_overlays as overlay_views
from vent_tournament import views_overlay_feed as overlay_feed_views
from . import views_sponsors
from . import views_holds
from . import views_announce
from . import views_metrics
from . import views_comp
from . import views_referrals
from . import views_map
from . import views_polls
from . import views_self_check_in
from . import views_sessions
from . import views_waitlist
from . import views_tiers
from . import views_limits
from . import views_short_links
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
    # Admitting yourself, where the organiser allows it. No Bearer token
    # required: a guest has no account, and the code plus the address it was
    # sent to is what stands in for a steward.
    path("ticket/<str:code>/self-check-in/", views_self_check_in.self_check_in,
         name="self_check_in"),
    path("<str:event_id>/self-check-in/settings/",
         views_self_check_in.self_check_in_settings,
         name="self_check_in_settings"),
    path("<str:event_id>/ticket-types/", ticket_types, name="ticket_types"),
    path("<str:event_id>/sessions/", views_sessions.sessions, name="event_sessions"),
    path("<str:event_id>/sessions/manage/", views_sessions.manage_sessions, name="manage_sessions"),
    path("<str:event_id>/sessions/<int:session_id>/", views_sessions.session_detail, name="session_detail"),
    path("<str:event_id>/waitlist/", views_waitlist.join_waitlist, name="join_waitlist"),
    path("<str:event_id>/waitlist/mine/", views_waitlist.my_waitlist_place, name="my_waitlist_place"),
    path("<str:event_id>/waitlist/all/", views_waitlist.event_waitlist, name="event_waitlist"),
    path("<str:event_id>/holds/", views_holds.holds, name="event_holds"),
    path("<str:event_id>/holds/<int:hold_id>/release/", views_holds.release_hold, name="release_hold"),
    path("<str:event_id>/holds/<int:hold_id>/issue/", views_holds.issue_hold, name="issue_hold"),
    path("<str:event_id>/money/", views_holds.event_money, name="event_money"),
    path("<str:event_id>/tiers/", views_tiers.create_tier, name="create_tier"),
    # How many tickets one address may hold, per type, per day, or across the
    # whole event. One endpoint for all three scopes, because a screen that
    # edits them together should not have to write them one request at a time.
    path("<str:event_id>/email-limits/", views_limits.email_limits,
         name="email_limits"),
    # Stream overlays for an event, the same mechanism tournaments have.
    # What an event overlay fills itself from. Public by design: a browser
    # source in OBS has no session and cannot sign in.
    path("<str:event_id>/overlay-feed/", overlay_feed_views.event_overlay_feed,
         name="event_overlay_feed"),
    path("<str:event_id>/overlays/", overlay_views.event_overlays,
         name="event_overlays"),
    path("<str:event_id>/overlays/<int:overlay_id>/", overlay_views.event_overlay_detail,
         name="event_overlay_detail"),
    path("<str:event_id>/overlays/<int:overlay_id>/rotate/", overlay_views.event_overlay_rotate,
         name="event_overlay_rotate"),
    path("<str:event_id>/short-links/", views_short_links.short_links,
         name="event_short_links"),
    path("<str:event_id>/short-links/<int:link_id>/",
         views_short_links.delete_short_link, name="delete_short_link"),
    path("<str:event_id>/checkout-fields/", views_guest.checkout_fields, name="checkout_fields"),
    path("<str:event_id>/sponsors/manage/", views_sponsors.event_sponsors,
         name="event_sponsors"),
    path("<str:event_id>/sponsors/<int:sponsor_id>/", views_sponsors.event_sponsor,
         name="event_sponsor"),
    path("<str:event_id>/checkout-fields/manage/", views_tiers.manage_checkout_fields, name="manage_checkout_fields"),
    path("<str:event_id>/guest-buy/", views_guest.guest_buy, name="guest_buy"),
    path("guest-verify/", views_guest.guest_verify, name="guest_verify"),
    path("guest-lookup/", views_guest.guest_lookup, name="guest_lookup"),
    path("<str:event_id>/tiers/<int:tier_id>/", views_tiers.update_tier, name="update_tier"),
    path("<str:event_id>/tiers/<int:tier_id>/delete/", views_tiers.delete_tier, name="delete_tier"),
    path("<str:event_id>/buy-ticket/", buy_ticket, name="buy_ticket"),
    path("<str:event_id>/attendees/", event_attendees, name="event_attendees"),
    # What the event did: sold, turned up, and what is left.
    path("<str:event_id>/metrics/", views_metrics.event_metrics,
         name="event_metrics"),
    path("<str:event_id>/metrics/export/", views_metrics.export_metrics,
         name="export_event_metrics"),
    # A message from the organiser to everybody holding a ticket.
    path("<str:event_id>/announcements/", views_announce.announcements,
         name="event_announcements"),
    path("<str:event_id>/announcements/audience/",
         views_announce.announcement_audience, name="announcement_audience"),
    # Asking the room. A vote belongs to a ticket, not to an account.
    path("<str:event_id>/polls/", views_polls.polls, name="event_polls"),
    path("<str:event_id>/polls/<int:poll_id>/", views_polls.poll_detail,
         name="event_poll_detail"),
    # Where people are coming from, as cells rather than as people.
    path("<str:event_id>/origins/", views_map.event_origins, name="event_origins"),
    # Tickets an organiser hands out by typing an address.
    path("<str:event_id>/comp-tickets/", views_comp.comp_tickets,
         name="comp_tickets"),
    path("<str:event_id>/polls/<int:poll_id>/vote/", views_polls.vote,
         name="event_poll_vote"),

    # Influencer links, promo codes, and who else may run the event
    # An arrival through an influencer's link. Public and unauthenticated,
    # because somebody arriving through an influencer's link is by definition
    # somebody who has never been here.
    path("<str:event_id>/ref/<str:code>/visit/", views_referrals.referral_visit,
         name="referral_visit"),
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
    path("<str:event_id>/tournament/<str:tournament_id>/ticketing/", set_shared_ticketing,
         name="set_shared_ticketing"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
