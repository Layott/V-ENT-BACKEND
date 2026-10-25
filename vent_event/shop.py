"""The rules every shop door reads (inbox 550 and 553, CEO 25 October 2026).

An event's own shop is a `Vendor` with `kind='organiser'`; a stall is a
`Vendor` with `kind='stall'`. Both sell through the same storefront, checkout
and order code. What this module holds is the part that must give ONE answer
at every door, because a rule written into each view separately is how a
rule ends up holding at six doors and not ten:

    stall_role(user, stall)      -> 'owner' | 'staff' | None
    pickup_refusal(stall, when)  -> (code, message) or None
    refund_refusal(order, when)  -> (code, message) or None
    unit_ngn(product, promo)     -> the naira one unit is charged at
    alert_low_stock(products)    -> tells the owner and staff, once per dip
    ticket_story(ticket)         -> what the door sees about a ticket holder
    tell_stalls_here(ticket)     -> tells stalls holding an order that the buyer arrived
    buyers_here(event, users)    -> which of these people are checked in
"""
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from .models import Ticket, Vendor, VendorOrder, VendorStaff
from .permissions import may_run_event

# Orders still waiting to be handed over at the stall.
OPEN_COLLECT = ('paid', 'ready')


def stall_role(user, stall):
    """The owner runs everything; staff handle orders. An event's own shop is
    run by whoever may run the event, so a co-manager does not need a second
    invitation to the shop of the event they already manage."""
    if user is None or stall is None:
        return None
    if stall.owner_id and stall.owner_id == user.user_id:
        return 'owner'
    if stall.kind == Vendor.KIND_ORGANISER and may_run_event(user, stall.event):
        return 'owner'
    if VendorStaff.objects.filter(vendor=stall, user=user).exists():
        return 'staff'
    return None


def stall_by_ref(user, ref, roles=('owner',)):
    """A stall this person holds one of `roles` on, by slug or key, or None."""
    ref = str(ref)
    stall = Vendor.objects.select_related('event').filter(slug=ref).first()
    if stall is None and ref.isdigit():
        stall = Vendor.objects.select_related('event').filter(id=int(ref)).first()
    if stall is None or stall_role(user, stall) not in roles:
        return None
    return stall


def _when(value):
    return timezone.localtime(value).strftime('%d %b %Y, %H:%M') if value else ''


def pickup_refusal(stall, when=None):
    """Why an order cannot be handed over now, or None. A stall with no window
    hands over whenever it is open, which is how every stall worked before."""
    when = when or timezone.now()
    if stall.pickup_opens_at and when < stall.pickup_opens_at:
        return ('PICKUP_NOT_OPEN',
                'Collection opens %s.' % _when(stall.pickup_opens_at))
    if stall.pickup_closes_at and when > stall.pickup_closes_at:
        return ('PICKUP_CLOSED',
                'Collection closed %s.' % _when(stall.pickup_closes_at))
    return None


def window_refusal(stall, opens, closes):
    """Why a pickup window cannot be saved, or None. It must run forwards and
    sit inside the event's own days: the spec says the goods are collected at
    the venue, on the event day."""
    if opens and closes and closes <= opens:
        return ('PICKUP_WINDOW_BACKWARDS', 'Collection has to close after it opens.')
    event = stall.event
    starts, ends = event.starts_at(), event.ends_at()
    if starts is not None:
        first_day = timezone.localtime(starts).date()
        for value in (opens, closes):
            if value and timezone.localtime(value).date() < first_day:
                return ('PICKUP_OUTSIDE_EVENT', 'Collection has to be on the event days.')
    if ends is not None:
        last_day = timezone.localtime(ends).date()
        for value in (opens, closes):
            if value and timezone.localtime(value).date() > last_day:
                return ('PICKUP_OUTSIDE_EVENT', 'Collection has to be on the event days.')
    return None


def refund_refusal(order, when=None):
    """Why the BUYER may not cancel this order now, or None. The stall's own
    cancel is not limited by this: the policy is what the buyer was promised."""
    when = when or timezone.now()
    stall = order.vendor
    if order.status in ('collected', 'delivered', 'sent'):
        return ('ALREADY_COLLECTED', 'This order has already been handed over.')
    if order.status == 'cancelled':
        return ('ALREADY_CANCELLED', 'This order was already cancelled.')
    if stall.refund_policy == Vendor.REFUND_NONE:
        return ('REFUNDS_OFF', '%s does not give refunds.' % stall.name)
    if stall.refund_policy == Vendor.REFUND_UNTIL_START:
        starts = stall.event.starts_at()
        if starts is not None and when >= starts:
            return ('REFUND_WINDOW_CLOSED',
                    'Refunds at %s ended when the event started.' % stall.name)
    return None


def unit_ngn(product, promo=None):
    """The naira one unit is charged at: the product's own sale price, then a
    shop code on top, never below nothing. Worked out here and only here, so
    the quote the buyer is shown and the charge cannot disagree (R73)."""
    price = product.sale_price
    if promo is not None:
        price = price - Decimal(promo.discount_for(price, 1))
    return max(price, Decimal('0')).quantize(Decimal('0.01'))


def read_product_extras(data):
    """The shop fields of a product from a request (inbox 550): category,
    discount and the low stock level. Returns `(fields, None)` or
    `(None, (code, message))`. Only the keys present are returned, so a
    PATCH that leaves one out leaves it alone."""
    from vent_auth import inputs
    out = {}
    if 'category' in data:
        out['category'] = inputs.read_text(data, 'category', max_length=40)
    if 'discount_kind' in data:
        kind = inputs.read_text(data, 'discount_kind', max_length=10) or ''
        if kind not in ('', 'percent', 'amount'):
            return None, ('VALIDATION_ERROR', 'A discount is a percentage or an amount off.')
        out['discount_kind'] = kind
    if 'discount_value' in data:
        try:
            value = inputs.read_float(data, 'discount_value', default=0, maximum=99_999_999)
        except (TypeError, ValueError):
            return None, ('VALIDATION_ERROR', 'The discount must be a number.')
        if value < 0:
            return None, ('VALIDATION_ERROR', 'A discount cannot be negative.')
        if out.get('discount_kind') == 'percent' and value > 100:
            return None, ('VALIDATION_ERROR', 'A percentage discount cannot be more than 100.')
        out['discount_value'] = value
    if 'low_stock_at' in data:
        try:
            level = inputs.read_int(data, 'low_stock_at', default=0, maximum=2_000_000_000)
        except (TypeError, ValueError):
            return None, ('VALIDATION_ERROR', 'The low stock level must be a number.')
        out['low_stock_at'] = max(level, 0)
    return out, None


def _people(stall):
    """Everybody running the stall: the owner and the staff."""
    people = []
    if stall.owner_id:
        people.append(stall.owner)
    people.extend(s.user for s in VendorStaff.objects.select_related('user').filter(vendor=stall))
    return people


def _notify(user, title, body, metadata):
    try:
        from vent_auth.views_notifications import create_notification
        create_notification(user=user, category='event', title=title, body=body,
                            link='/events/vendor-shop', metadata=metadata, email=False)
    except Exception:  # a notification is never worth losing the sale over
        pass


def alert_low_stock(products):
    """After an order: each product now at or under its alert level, not yet
    told about, tells everybody running the stall. Once per dip."""
    from .models import VendorProduct
    now = timezone.now()
    for product in products:
        fresh = VendorProduct.objects.select_related('vendor', 'vendor__owner').get(pk=product.pk)
        if not fresh.low_stock_at or fresh.stock > fresh.low_stock_at or fresh.low_stock_alerted_at:
            continue
        VendorProduct.objects.filter(pk=fresh.pk).update(low_stock_alerted_at=now)
        for person in _people(fresh.vendor):
            _notify(person, '%s is running low' % fresh.name,
                    '%s left at %s.' % (fresh.stock, fresh.vendor.name),
                    {'vendor_id': fresh.vendor_id, 'product_id': fresh.pk, 'kind': 'low_stock'})


def _origin(ticket):
    """How this person got the ticket, from what the ticket already records.

    Nothing new is stored: a tournament reward carries `reward:<match>`, a
    qualifier `qualified:<line>:<entry>`, a complimentary ticket says who
    comped it in its answers, and a code is on the ticket itself."""
    ref = ticket.payment_reference or ''
    answers = ticket.answers if isinstance(ticket.answers, dict) else {}
    if ref.startswith('reward:') or ref.startswith('qualified:'):
        from vent_tournament.models import BracketMatch, TournamentRegistration
        title = ''
        try:
            if ref.startswith('reward:'):
                match = BracketMatch.objects.select_related('tournament').filter(
                    pk=int(ref.split(':')[1])).first()
                title = match.tournament.tournament_title if match else ''
            else:
                entry = TournamentRegistration.objects.select_related('tournament').filter(
                    pk=int(ref.split(':')[2])).first()
                title = entry.tournament.tournament_title if entry else ''
        except (ValueError, IndexError):
            title = ''
        if ref.startswith('reward:'):
            return {'kind': 'tournament_reward', 'label': 'Earned in a tournament', 'detail': title}
        return {'kind': 'qualified', 'label': 'Qualified through a tournament', 'detail': title}
    if answers.get('comped_by'):
        return {'kind': 'complimentary', 'label': 'Complimentary ticket',
                'detail': 'From %s' % answers['comped_by']}
    if ticket.promo_id:
        return {'kind': 'promo', 'label': 'Bought with a code',
                'detail': ticket.promo.code if ticket.promo else ''}
    if ticket.price_vc or ticket.price_ngn:
        return {'kind': 'bought', 'label': 'Bought', 'detail': ''}
    return {'kind': 'free', 'label': 'Free ticket', 'detail': ''}


def _plays_in(ticket):
    """The tournaments at this event the holder is entered in, alone or with
    a team: what makes somebody at the door one of the players."""
    if not ticket.user_id:
        return []
    from vent_tournament.models import TournamentRegistration
    from vent_auth.models import TeamMembers
    links = ticket.event.tournament_links.values_list('tournament_id', flat=True)
    if not links:
        return []
    teams = TeamMembers.objects.filter(user_id=ticket.user_id).values_list('team_id', flat=True)
    entries = (TournamentRegistration.objects.select_related('tournament')
               .filter(tournament_id__in=list(links), status__in=('pending', 'confirmed'))
               .filter(Q(user_id=ticket.user_id) | Q(team_id__in=list(teams))))
    seen, out = set(), []
    for entry in entries:
        t = entry.tournament
        if t.pk in seen:
            continue
        seen.add(t.pk)
        out.append({'title': t.tournament_title, 'slug': t.slug,
                    'team': entry.team.team_name if entry.team_id else None})
    return out


def _orders(ticket):
    """What the holder bought at this event's shops and stalls."""
    if not ticket.user_id:
        return []
    orders = (VendorOrder.objects.select_related('vendor')
              .prefetch_related('items__product')
              .filter(buyer_id=ticket.user_id, vendor__event_id=ticket.event_id)
              .exclude(status='cancelled').order_by('created_at'))
    return [{
        'code': o.code,
        'stall': o.vendor.name,
        'stall_kind': o.vendor.kind,
        'status': o.status,
        'waiting': o.status in OPEN_COLLECT and o.fulfilment == 'collect',
        'items': [{'name': i.product.name if i.product_id else '',
                   'variant': i.variant or None, 'quantity': i.quantity}
                  for i in o.items.all()],
    } for o in orders]


def ticket_story(ticket):
    """What the door sees about the person holding this ticket (inbox 553):
    how they got it, its tier and perks, the tournaments they play in here,
    and what they bought at the event."""
    tier = ticket.tier if ticket.tier_id else None
    return {
        'origin': _origin(ticket),
        'tier': tier.name if tier else '',
        'perks': tier.perks if tier else '',
        'plays_in': _plays_in(ticket),
        'orders': _orders(ticket),
    }


def tell_stalls_here(ticket):
    """A check-in tells everybody running a stall that holds an order of this
    person waiting to be collected, so they can have it ready (inbox 553)."""
    if not ticket.user_id:
        return 0
    waiting = (VendorOrder.objects.select_related('vendor', 'vendor__owner')
               .filter(buyer_id=ticket.user_id, vendor__event_id=ticket.event_id,
                       status__in=OPEN_COLLECT, fulfilment='collect'))
    name = ticket.attendee_name or (ticket.user.full_name or ticket.user.username)
    told = 0
    by_stall = {}
    for order in waiting:
        by_stall.setdefault(order.vendor, []).append(order.code)
    for stall, codes in by_stall.items():
        for person in _people(stall):
            _notify(person, '%s is here' % name,
                    'Waiting at %s: %s.' % (stall.name, ', '.join(codes)),
                    {'vendor_id': stall.id, 'order_codes': codes, 'kind': 'buyer_here'})
            told += 1
    return told


def buyers_here(event_id, user_ids):
    """The subset of these people holding a checked-in ticket for the event."""
    if not user_ids:
        return set()
    return set(Ticket.objects.filter(event_id=event_id, user_id__in=list(user_ids),
                                     checked_in_at__isnull=False)
               .values_list('user_id', flat=True))
