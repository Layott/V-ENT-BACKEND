"""Every marketplace address, and every one of them behind the switch.

`gated` is applied HERE, once per route, rather than as a decorator on each
view. A rule that has to be remembered at every view holds everywhere except
the one added last, and that one ships open - which is exactly how
`vent_billing` went live without anybody deciding.

`tests_switch.py` walks this list and asserts every single entry carries the
mark, so a route added later without it fails a test rather than opening a door.
"""
from django.urls import path

from . import views_listings, views_trade
from .switch import gated

urlpatterns = [
    # What a form may ask, from the table that checks it.
    path('catalogue/', gated(views_listings.listing_catalogue),
         name='marketplace_catalogue'),

    # Browsing and reading.
    path('listings/', gated(views_listings.browse), name='marketplace_browse'),
    path('listings/new/', gated(views_listings.create_listing),
         name='marketplace_create'),
    path('listings/<str:reference>/', gated(views_listings.listing_detail),
         name='marketplace_listing'),
    path('listings/<str:reference>/edit/', gated(views_listings.edit_listing),
         name='marketplace_edit'),
    path('listings/<str:reference>/status/', gated(views_listings.set_status),
         name='marketplace_status'),
    path('listings/<str:reference>/media/', gated(views_listings.add_media),
         name='marketplace_media'),

    # Asking, offering and buying.
    path('listings/<str:reference>/inquire/', gated(views_trade.inquire),
         name='marketplace_inquire'),
    path('listings/<str:reference>/report/', gated(views_trade.report_listing),
         name='marketplace_report'),
    path('listings/<str:reference>/bids/', gated(views_trade.bids),
         name='marketplace_bids'),
    path('listings/<str:reference>/buy/', gated(views_trade.buy),
         name='marketplace_buy'),
    path('bids/<int:bid_id>/', gated(views_trade.settle_bid),
         name='marketplace_settle_bid'),

    # After the money moves.
    path('purchases/', gated(views_trade.my_purchases),
         name='marketplace_purchases'),
    path('purchases/<str:token>/', gated(views_trade.settle_purchase),
         name='marketplace_settle'),
    path('purchases/<str:token>/review/', gated(views_trade.review),
         name='marketplace_review'),

    # The seller's own side, and the buyer's list.
    path('mine/', gated(views_listings.my_listings), name='marketplace_mine'),
    path('sellers/<str:username>/', gated(views_listings.seller_detail),
         name='marketplace_seller'),
    path('wishlist/', gated(views_trade.wishlist), name='marketplace_wishlist'),
    path('wishlist/<int:row_id>/', gated(views_trade.wishlist_delete),
         name='marketplace_wishlist_delete'),

    # What the platform is holding.
    path('admin/holds/', gated(views_trade.admin_holds),
         name='marketplace_holds'),
]
