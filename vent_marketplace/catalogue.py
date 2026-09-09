"""What a listing may be, and what each kind has to say.

The spec describes three listings - a service, a swap and a sale - and lists
different fields under each. Three models would have been the obvious reading
and the wrong one: they share an author, an address, a price, media, messages,
reports, reviews, bids and a lifecycle, and splitting them means writing all of
that three times and finding out six weeks later that only one of the three
learned about wishlists.

So one model with a `kind`, and this file is where the differences live: which
fields each kind asks for, which it requires, and which of them a free account
may use. A screen reads this to draw its form, and `clean()` reads the same
table to check what comes back, so a form cannot ask for something the server
will not store.
"""

SERVICE = 'service'
SWAP = 'swap'
SALE = 'sale'

KINDS = {
    SERVICE: 'Offering a skill',
    SWAP: 'Trading or exchanging',
    SALE: 'Selling',
}

#: The spec's five, in its order. A category is a KEY here and a translated
#: word on the screen, for the same reason a format is: a category chosen in
#: French has to still be the same category.
CATEGORIES = {
    'coaching': 'Coaching',
    'streaming': 'Streaming',
    'content': 'Content creation',
    'design': 'Graphic design',
    'merchandise': 'Merchandise',
    # Two the spec names in its closing paragraph rather than in the list, and
    # they are what an African gaming marketplace actually trades.
    'in_game': 'In-game items, skins or accounts',
    'anime': 'Anime and manga items',
}

#: How a service is charged. The spec: "hourly rate, package deals or fixed
#: prices".
PRICING = {
    'hourly': 'An hourly rate',
    'package': 'A package',
    'fixed': 'One fixed price',
}

#: The spec: "virtual, in person or hybrid".
DELIVERY = {
    'virtual': 'Online',
    'in_person': 'In person',
    'hybrid': 'Either',
}

CONDITIONS = {
    'new': 'New',
    'used': 'Used',
}

#: Per kind: the fields it uses, and the ones it cannot be published without.
#: `required` is deliberately short. A form that demands eleven things before it
#: will save anything is a form people abandon, and everything outside this set
#: can be added by editing.
FIELDS = {
    SERVICE: {
        'uses': ('price_kind', 'price', 'duration_minutes', 'available_from',
                 'available_to', 'experience', 'delivery', 'location', 'tags'),
        'required': ('price_kind', 'price'),
    },
    SWAP: {
        'uses': ('offered', 'wanted', 'condition', 'trade_value', 'tags'),
        'required': ('offered', 'wanted'),
    },
    SALE: {
        'uses': ('price', 'quantity', 'condition', 'payment_methods', 'tags'),
        'required': ('price', 'quantity'),
    },
}

#: What a free account cannot do. Each maps to a key in `vent_auth.premium`,
#: so there is ONE idea of premium on the platform rather than a second one
#: growing here.
#:
#: The spec marks seven things premium. Six are listed; the seventh, "unlimited
#: active listings", is not a field and lives in `limits.py`.
PREMIUM_FIELDS = {
    'portfolio': 'media_export',          # samples of previous work
    'discount_code': 'financial_analytics',
    'promo_material': 'media_export',     # flyers and banners on a sale
    'analytics': 'financial_analytics',   # views, inquiries, completions
    'hoist': 'advanced_streaming',        # promoting a listing for visibility
    'bulk': 'unlimited_size',             # bulk listings
}

#: What the spec sets for everybody, and what it lifts for premium.
FREE_ACTIVE_LISTINGS = 1
BID_DAYS_FREE = 3
BID_DAYS_PREMIUM = 14


def fields_for(kind):
    return FIELDS.get(kind, {}).get('uses', ())


def required_for(kind):
    return FIELDS.get(kind, {}).get('required', ())


def catalogue():
    """Everything a screen needs to draw the form, from the table that checks it."""
    return {
        'kinds': [{'key': k, 'label': v} for k, v in KINDS.items()],
        'categories': [{'key': k, 'label': v} for k, v in CATEGORIES.items()],
        'pricing': [{'key': k, 'label': v} for k, v in PRICING.items()],
        'delivery': [{'key': k, 'label': v} for k, v in DELIVERY.items()],
        'conditions': [{'key': k, 'label': v} for k, v in CONDITIONS.items()],
        'fields': {kind: {'uses': list(spec['uses']),
                          'required': list(spec['required'])}
                   for kind, spec in FIELDS.items()},
        'premium_fields': dict(PREMIUM_FIELDS),
        'free_active_listings': FREE_ACTIVE_LISTINGS,
        'bid_days': {'free': BID_DAYS_FREE, 'premium': BID_DAYS_PREMIUM},
    }
