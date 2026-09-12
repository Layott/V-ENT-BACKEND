#!/usr/bin/env python
"""Walk the whole event feature, in every role, against the local database.

CEO, 12 September 2026 (inbox row 260): create an event with every feature,
test it as an organiser, a buyer, an influencer, a vendor and the door, with
several people buying at once, and check the numbers. Gates in
V-ENT/gates/34-event-end-to-end.md.

Two kinds of proof, because they fail differently:

  * in-process, through the same DRF views the site calls, with the test
    client (`--setup`, `--buyers`, `--vendor`, `--influencer`, `--door`,
    `--numbers`, `--roles`);
  * over HTTP with real threads against the dev server on 8000, which is the
    only way "forty people in the same second" can be made to happen
    (`--rush`, `--stall-rush`).

Everything it creates is named walk_* so it can be found and removed, and it
never touches the demo_* accounts.

  DB_ENGINE=sqlite DEBUG=True venv/Scripts/python.exe tools/walk_event.py --setup
"""
import json
import os
import sys
import threading
import time
import uuid
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vent.settings')
# Ticket emails go to memory here. The dev settings point at Gmail SMTP with
# no working password, and a walk that sends forty receipts is forty
# SMTPAuthenticationErrors in the log for nothing.
os.environ.setdefault('EMAIL_BACKEND', 'django.core.mail.backends.locmem.EmailBackend')

import django  # noqa: E402

django.setup()

from django.contrib.auth.hashers import make_password  # noqa: E402
from django.db.models import Q
from django.utils import timezone  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from vent_auth.models import Games, Transaction, Users, UserWallet  # noqa: E402
from vent_event.models import (Event, EventReferral, Ticket, TicketTier,  # noqa: E402
                               Vendor, VendorOrder, VendorProduct)

PIN = '2468'
WALK_PASSWORD = 'walk-con-2026'  # local dev accounts only
STATE = os.path.join(ROOT, 'tools', '.walk-event-state.json')
FAILS = []


# ------------------------------------------------------------------ helpers
def say(*parts):
    print(' '.join(str(p) for p in parts), flush=True)


def fail(what, detail=''):
    FAILS.append(what)
    say('  FAIL', what, ('| ' + str(detail)[:300]) if detail else '')


def ok(what):
    say('  ok  ', what)


def load_state():
    return json.load(open(STATE, encoding='utf-8')) if os.path.exists(STATE) else {}


def save_state(state):
    json.dump(state, open(STATE, 'w', encoding='utf-8'), indent=1)


def person(handle, coins=0, admin_role=None):
    """A walk_* account with a wallet, a PIN and a bearer token."""
    username = 'walk_%s' % handle
    user = Users.objects.filter(username=username).first()
    if user is None:
        user = Users.objects.create(
            username=username, email='%s@walk.test' % username,
            full_name=handle.replace('_', ' ').title(),
            login_session_token=('wk%s' % uuid.uuid4().hex)[:16],
            login_session_created_at=timezone.now(), is_active=True,
            is_staff=admin_role is not None, admin_role=admin_role,
            role='admin' if admin_role else 'user')
        if admin_role:
            user.login_session_2fa_at = timezone.now()
            user.save(update_fields=['login_session_2fa_at'])
    # A password, so the same account can be signed into on the local site
    # for the Chrome walk of each role. Local sqlite only; never production.
    if not user.has_usable_password():
        user.set_password(WALK_PASSWORD)
        user.save(update_fields=['password'])
    wallet = UserWallet.objects.filter(user=user).first()
    if wallet is None:
        wallet = UserWallet.objects.create(
            user_wallet_id=uuid.uuid4().hex[:10], user=user, wallet_balance=coins,
            pin_hash=make_password(PIN))
    else:
        wallet.wallet_balance = coins
        wallet.pin_hash = make_password(PIN)
        wallet.save(update_fields=['wallet_balance', 'pin_hash'])
    return user


def client(user=None):
    # The test client sends Host: testserver, which ALLOWED_HOSTS refuses
    # outside the test runner. 127.0.0.1 is allowed everywhere.
    c = APIClient(HTTP_HOST='127.0.0.1')
    if user is not None:
        c.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)
    return c


def body(res):
    try:
        return res.json()
    except Exception:
        return {'status': 'error', 'raw': res.content[:200]}


def expect(res, code, what, key=None):
    b = body(res)
    if res.status_code != code:
        fail(what, 'got %s %s %s' % (res.status_code, b.get('code'), b.get('message')))
        return None
    ok(what)
    return b.get('data') if key is None else (b.get('data') or {}).get(key)


def balance(user):
    return UserWallet.objects.get(user=user).wallet_balance


def answers(st, name, size=None):
    """What a buyer sends for the organiser's checkout fields, keyed by id."""
    ids = st.get('field_ids') or {}
    out = {'answers': {ids.get('Full name', 'full_name'): name}}
    if size:
        out['attendees'] = [{'answers': {ids.get('Shirt size', 'shirt_size'): size}}]
    return out


# -------------------------------------------------------------------- setup
def setup():
    say('== setup: one event with every feature on it')
    game, _ = Games.objects.get_or_create(game_title='EA FC 26')
    organiser = person('organiser', coins=0)
    steward = person('door', coins=0)
    helper = person('manager', coins=0)
    influencer = person('influencer', coins=0)
    admin = person('admin', coins=0, admin_role='super_admin')

    # Anything left from an earlier run, in the order the protected keys allow:
    # tickets before types, order items before products, stalls before the event.
    from vent_event.models import VendorOrderItem
    old = Event.objects.filter(name__startswith='Walk Con')
    VendorOrderItem.objects.filter(order__vendor__event__in=old).delete()
    VendorOrder.objects.filter(vendor__event__in=old).delete()
    VendorProduct.objects.filter(vendor__event__in=old).delete()
    Vendor.objects.filter(event__in=old).delete()
    Ticket.objects.filter(event__in=old).delete()
    TicketTier.objects.filter(event__in=old).delete()
    old.delete()

    start = timezone.now() + timezone.timedelta(days=7)
    day1 = start.date().isoformat()
    day2 = (start + timezone.timedelta(days=1)).date().isoformat()
    c = client(organiser)

    res = c.post('/event/create-event/', data=json.dumps({
        'name': 'Walk Con %s' % uuid.uuid4().hex[:4],
        'event_type': 'physical',
        'description': 'Every feature the platform offers, on one event.',
        'start_date': start.isoformat(),
        'end_date': (start + timezone.timedelta(days=1, hours=8)).isoformat(),
        'location': 'Celebr8 Centre, Ogba, Lagos',
        'game_id': game.game_id,
        'capacity': 60,
        'capacity_mode': 'per_day',
        'max_tickets_per_email': 4,
        'self_check_in': True,
        'self_check_in_opens_minutes': 120,
        'ticket_types': [
            {'name': 'General Admission', 'price': '2000', 'quantity': 25,
             'perks': 'All-day entry', 'day': day1, 'day_label': 'Day 1'},
            {'name': 'VIP', 'price': '9000', 'quantity': 10,
             'perks': 'Front row, lounge', 'day': day1, 'day_label': 'Day 1'},
            {'name': 'Day 2 pass', 'price': '1000', 'quantity': 30,
             'day': day2, 'day_label': 'Day 2'},
            {'name': 'Free entry', 'price': '0', 'quantity': 20, 'day': day2,
             'day_label': 'Day 2'},
        ],
        'sponsors': [{'name': 'CADE ESPORTS', 'website': 'https://cadeesport.com/'}],
        'partners': [{'name': 'KON10DR'}],
        'social_links': {'twitter': 'https://x.com/vent'},
        'vendor_invites': [{'name': 'Suya Corner', 'email': 'walk_suya@walk.test',
                            'booth': 'C1'}],
    }), content_type='application/json')
    data = expect(res, 201, 'event created with 4 tiers, sponsors, a partner, a vendor invite')
    if data is None:
        return None
    slug = data['slug']
    event = Event.objects.get(slug=slug)
    tiers = {t.name: t for t in event.ticket_tiers.all()}
    if len(tiers) != 4:
        fail('four tiers on the event', list(tiers))

    # Price drift on the console's tier endpoint: early bird, group rate, an
    # access code, a per-type email cap.
    ga, vip, d2 = tiers['General Admission'], tiers['VIP'], tiers['Day 2 pass']
    expect(c.patch('/event/%s/tiers/%s/' % (slug, ga.id), data=json.dumps({
        'early_bird_quantity': 5, 'early_bird_price': '3000',
        'group_min': 4, 'group_price': '1000'}), content_type='application/json'),
        200, 'GA: 2000 for the first 5, 3000 after, 1000 each from 4 up')
    expect(c.patch('/event/%s/tiers/%s/' % (slug, vip.id), data=json.dumps({
        'access_code': 'VIPONLY', 'max_tickets_per_email': 1}),
        content_type='application/json'),
        200, 'VIP: behind the code VIPONLY, one per address')

    # What a buyer has to answer.
    fields = expect(c.put('/event/%s/checkout-fields/manage/' % slug, data=json.dumps({
        'fields': [{'label': 'Full name', 'kind': 'text', 'required': True,
                    'per_ticket': False},
                   {'label': 'Shirt size', 'kind': 'choice', 'required': False,
                    'options': ['S', 'M', 'L'], 'per_ticket': True}]}),
        content_type='application/json'), 200, 'checkout fields: name, shirt size')
    # Answers are keyed by field id, which is what the checkout screen sends.
    field_ids = {f.get('label'): str(f.get('id')) for f in ((fields or {}).get('fields') or [])}

    # The programme people see, and the run of show the crew sees.
    for title, hours in (('Doors open', 0), ('Cosplay parade', 2), ('Finals', 5)):
        expect(c.post('/event/%s/sessions/manage/' % slug, data=json.dumps({
            'title': title, 'stage': 'Main Hall',
            'starts_at': (start + timezone.timedelta(hours=hours)).isoformat(),
            'ends_at': (start + timezone.timedelta(hours=hours + 1)).isoformat()}),
            content_type='application/json'), 201, 'session: %s' % title)
    expect(c.post('/event/%s/run-of-show/' % slug, data=json.dumps({
        'name': 'Walk Con run of show', 'visibility': 'public',
        'time_zone': 'Africa/Lagos'}), content_type='application/json'),
        200, 'run of show named and public')
    day = expect(c.post('/event/%s/run-of-show/days/' % slug, data=json.dumps({
        'label': 'Day 1', 'date': day1}), content_type='application/json'),
        200, 'run of show day 1')
    # The days endpoint answers with the whole sheet; the new day is the last.
    sheet_days = (((day or {}).get('sheet') or {}).get('days') or [])
    day_id = sheet_days[-1].get('id') if sheet_days else None
    if not day_id:
        fail('the run of show day has an id the walk can address', day)
    else:
        expect(c.post('/event/%s/run-of-show/items/' % slug, data=json.dumps({
            'day_id': day_id, 'starts_at': '10:00', 'minutes': 30,
            'activity': 'Doors and registration', 'owner': 'Front desk',
            'phase': 'Arrival'}), content_type='application/json'),
            200, 'run of show item')

    # Pitches for sale, one free so the invite path and the bought path both exist.
    slot = expect(c.post('/event/%s/slots/' % slug, data=json.dumps({
        'name': 'Food stall, 3x3m', 'description': 'Table and one power point.',
        'price_ngn': 20000, 'quantity': 3, 'category': 'Food',
        'rules': 'No open flames. Clear your own waste.',
        'requires_approval': False}), content_type='application/json'),
        201, 'vendor pitch on sale: 20,000 naira, 3 of them')

    # An influencer with a code, an allocation and a commission, paid to a
    # real account; a promo code hanging off the same link.
    ref = expect(c.post('/event/%s/referrals/' % slug, data=json.dumps({
        'name': 'Big Streamer', 'code': 'BIGST', 'allocation': 5,
        'commission_pct': 10, 'payee': influencer.username}),
        content_type='application/json'), 201, 'influencer link BIGST, 10 percent, 5 held')
    ref_id = (ref or {}).get('id') or ((ref or {}).get('referral') or {}).get('id')
    expect(c.post('/event/%s/promos/' % slug, data=json.dumps({
        'code': 'STR10', 'kind': 'percent', 'value': 10, 'max_tickets': 100,
        'referral_id': ref_id}), content_type='application/json'),
        201, 'promo STR10, 10 percent off, tied to the link')

    # A short link for the poster.
    short = expect(c.post('/event/%s/short-links/' % slug, data=json.dumps({
        'target': '/events/%s?tab=tickets' % slug, 'label': 'Poster'}),
        content_type='application/json'), 201, 'short link for the poster')
    st_short = ((short or {}).get('link') or {}).get('url') or (short or {}).get('short_url')

    # A poll and an announcement, once anybody holds a ticket they matter.
    expect(c.post('/event/%s/polls/' % slug, data=json.dumps({
        'question': 'Which game first?', 'kind': 'single',
        'options': ['EA FC', 'Free Fire', 'CODM']}), content_type='application/json'),
        201, 'poll with three options')

    expect(c.post('/event/%s/announcements/' % slug, data=json.dumps({
        'subject': 'Doors open at 5', 'body': 'Bring the code on your phone.',
        'audience': 'all'}), content_type='application/json'),
        201, 'announcement to everybody with a ticket')

    # The team: a manager and a door steward.
    expect(c.post('/event/%s/managers/' % slug, data=json.dumps({
        'username': helper.username, 'role': 'manager'}),
        content_type='application/json'), 201, 'manager added')
    expect(c.post('/event/%s/managers/' % slug, data=json.dumps({
        'username': steward.username, 'role': 'door'}),
        content_type='application/json'), 201, 'door steward added')

    # Self check-in is on, two hours before the doors.
    expect(c.post('/event/%s/self-check-in/settings/' % slug, data=json.dumps({
        'enabled': True, 'opens_minutes_before': 120}),
        content_type='application/json'), 200, 'self check-in on, 120 minutes before')

    # Fee bearer: the organiser pays the platform fee.
    expect(c.post('/event/%s/fee-bearer/' % slug, data=json.dumps({
        'fee_bearer': 'organiser'}), content_type='application/json'),
        200, 'organiser bears the fee')

    save_state({'slug': slug, 'event_id': event.event_id, 'short_url': st_short,
                'field_ids': field_ids,
                'tiers': {name: t.id for name, t in tiers.items()},
                'slot_id': (slot or {}).get('id') or ((slot or {}).get('slot') or {}).get('id'),
                'referral_id': ref_id, 'day1': day1, 'day2': day2})
    say('event ready' if not FAILS else 'setup: %d failure(s)' % len(FAILS))
    return slug


# ----------------------------------------------------------------- coverage
def coverage():
    """Every field the wizard sends is one the walk sent."""
    say('== coverage: the wizard payload against the walk payload')
    fe = os.path.join(os.path.dirname(ROOT), 'V-ENT-FRONTEND', 'src', 'app',
                      'events', 'create-event', 'page.js')
    src = open(fe, encoding='utf-8').read()
    import re
    sent = set(re.findall(r"^\s*([a-z_]+):\s", src, re.M))
    # What create_event actually reads, which is the contract.
    view = open(os.path.join(ROOT, 'vent_event', 'views.py'), encoding='utf-8').read()
    reads = set(re.findall(r"data\.get\('([a-z_]+)'", view))
    walk = open(os.path.abspath(__file__), encoding='utf-8').read()
    missing = sorted(k for k in reads if k in sent and ("'%s'" % k) not in walk)
    # Fields the walk deliberately does not send, each with a reason.
    deliberate = {
        'banner': 'a file upload; the wizard uploads one, covered by tests_edit_event',
        'banner_url': 'the same picture by URL',
        'logo': 'a file upload',
        'category': 'a label with no behaviour behind it',
        'currency': 'display only, NGN everywhere',
        'desc': 'the old name of description, still accepted',
        'entry_fee': 'the pre-tier price, superseded by ticket_types',
        'event_link': 'a virtual event field; this one is physical',
        'virtual_link': 'the same',
        'game': 'the old name of game_id',
        'game_title': 'the same by title',
        'is_active': 'defaults true',
        'latitude': 'geocoded from the address by the server',
        'longitude': 'the same',
        'organization': 'covered by tests_org_events; this event is a person\'s',
        'series_id': 'a series link, covered by tests_series',
    }
    real = [k for k in missing if k not in deliberate]
    for k in real:
        fail('wizard field never sent: %s' % k)
    say('%d wizard field(s) the walk never sent' % len(real))


# ------------------------------------------------------------------- buyers
def buyers():
    say('== buyers: every way there is to buy')
    st = load_state()
    slug, tiers = st['slug'], st['tiers']
    event = Event.objects.get(slug=slug)
    ga = TicketTier.objects.get(pk=tiers['General Admission'])
    vip = TicketTier.objects.get(pk=tiers['VIP'])
    free = TicketTier.objects.get(pk=tiers['Free entry'])

    # 1. A signed-in buyer pays from the wallet, answering the fields.
    ada = person('buyer_ada', coins=100)
    before = balance(ada)
    res = client(ada).post('/event/%s/buy-ticket/' % slug, data=json.dumps({
        'tier_id': ga.id, 'quantity': 1, 'pin': PIN,
        **answers(st, 'Ada Obi', 'M')}),
        content_type='application/json')
    data = expect(res, 201, 'Ada buys one GA from her wallet')
    if data:
        tks = data.get('tickets') or []
        if len(tks) != 1:
            fail('one ticket issued', data)
        if balance(ada) != before - 2:
            fail('2 coins (2,000 naira) left Ada\'s wallet', (before, balance(ada)))
        st['ada_code'] = (tks[0] or {}).get('code') if tks else None

    # 2. A required field missing is refused BEFORE money moves.
    bola = person('buyer_bola', coins=50)
    before = balance(bola)
    res = client(bola).post('/event/%s/buy-ticket/' % slug, data=json.dumps({
        'tier_id': ga.id, 'quantity': 1, 'pin': PIN}), content_type='application/json')
    expect(res, 400, 'Bola without the full name is refused')
    if balance(bola) != before:
        fail('nothing left Bola\'s wallet on a refusal', (before, balance(bola)))

    # 3. Early bird: GA is 2,000 for the first 5 sold, 3,000 after.
    for i in range(4):
        b = person('buyer_eb%d' % i, coins=50)
        expect(client(b).post('/event/%s/buy-ticket/' % slug, data=json.dumps({
            'tier_id': ga.id, 'quantity': 1, 'pin': PIN,
            **answers(st, 'Early %d' % i)}), content_type='application/json'),
            201, 'early bird buyer %d at 2,000' % i)
    q = client().get('/event/%s/quote/?tier=%s&quantity=1' % (slug, ga.id))
    quote = expect(q, 200, 'quote after 5 sold')
    unit = (quote or {}).get('unit_ngn') or (quote or {}).get('price_ngn') or (quote or {}).get('unit')
    if quote and float(unit or 0) != 3000:
        fail('sixth GA is quoted at 3,000', quote)

    # 4. Group rate: four at once cost 1,000 each, whatever the early bird says.
    grp = person('buyer_group', coins=200)
    before = balance(grp)
    res = client(grp).post('/event/%s/buy-ticket/' % slug, data=json.dumps({
        'tier_id': ga.id, 'quantity': 4, 'pin': PIN,
        **answers(st, 'Group Lead')}), content_type='application/json')
    data = expect(res, 201, 'a group of four buys GA')
    if data and balance(grp) != before - 4:
        fail('four at the group rate cost 4 coins', (before, balance(grp)))

    # 5. The access code: VIP refused without it, sold with it, one per address.
    vp = person('buyer_vip', coins=100)
    expect(client(vp).post('/event/%s/buy-ticket/' % slug, data=json.dumps({
        'tier_id': vip.id, 'quantity': 1, 'pin': PIN,
        **answers(st, 'V Person')}), content_type='application/json'),
        403, 'VIP without the code is refused')
    expect(client(vp).post('/event/%s/buy-ticket/' % slug, data=json.dumps({
        'tier_id': vip.id, 'quantity': 1, 'pin': PIN, 'code': 'viponly',
        **answers(st, 'V Person')}), content_type='application/json'),
        201, 'VIP with the code (any case) is sold')
    expect(client(vp).post('/event/%s/buy-ticket/' % slug, data=json.dumps({
        'tier_id': vip.id, 'quantity': 1, 'pin': PIN, 'code': 'VIPONLY',
        **answers(st, 'V Person')}), content_type='application/json'),
        409, 'a second VIP to the same address is refused (one per address)')

    # 6. A guest with no account buys a free ticket, gets a code, finds it again.
    res = client().post('/event/%s/guest-buy/' % slug, data=json.dumps({
        'tier_id': free.id, 'quantity': 1, 'email': 'walk_guest@walk.test',
        **answers(st, 'Guest Person')}), content_type='application/json')
    data = expect(res, 201, 'a guest gets a free ticket with no account')
    gcode = None
    if data:
        tks = data.get('tickets') or []
        gcode = (tks[0] or {}).get('code') if tks else None
        st['guest_code'] = gcode
    if gcode:
        expect(client().post('/event/guest-lookup/', data=json.dumps({
            'email': 'walk_guest@walk.test', 'code': gcode}),
            content_type='application/json'), 200, 'the guest finds the ticket by email and code')
        expect(client().post('/event/guest-lookup/', data=json.dumps({
            'email': 'somebody@else.test', 'code': gcode}),
            content_type='application/json'), 404, 'another address does not find it')

    # 7. The promo code takes 10 percent off, and counts for the influencer.
    pr = person('buyer_promo', coins=100)
    before = balance(pr)
    res = client(pr).post('/event/%s/buy-ticket/' % slug, data=json.dumps({
        'tier_id': ga.id, 'quantity': 1, 'pin': PIN, 'code': 'STR10',
        **answers(st, 'Promo Person')}), content_type='application/json')
    data = expect(res, 201, 'STR10 buys GA at 10 percent off')
    if data:
        st['promo_code_ticket'] = ((data.get('tickets') or [{}])[0] or {}).get('code')

    # 8. Sold out and the waitlist. Day 2 pass: 30 on the tier. Sell them all
    #    to one group of buyers, then the next joins the waitlist.
    d2 = TicketTier.objects.get(pk=tiers['Day 2 pass'])
    d2.quantity = 3
    d2.save(update_fields=['quantity'])
    for i in range(3):
        b = person('buyer_d2_%d' % i, coins=50)
        expect(client(b).post('/event/%s/buy-ticket/' % slug, data=json.dumps({
            'tier_id': d2.id, 'quantity': 1, 'pin': PIN,
            **answers(st, 'Day Two %d' % i)}), content_type='application/json'),
            201, 'day 2 buyer %d' % i)
    late = person('buyer_late', coins=50)
    expect(client(late).post('/event/%s/buy-ticket/' % slug, data=json.dumps({
        'tier_id': d2.id, 'quantity': 1, 'pin': PIN,
        **answers(st, 'Too Late')}), content_type='application/json'),
        409, 'the fourth Day 2 buyer is told the type is sold out')
    # The queue opens when the whole EVENT has nothing left, not one type: with
    # other types still on sale a buyer is expected to pick one of those.
    expect(client(late).post('/event/%s/waitlist/' % slug, data=json.dumps({
        'tier_id': d2.id, 'quantity': 1}), content_type='application/json'),
        409, 'the waitlist refuses while other types are on sale')
    for t in event.ticket_tiers.exclude(pk=d2.pk):
        t.quantity = max(int(t.sold), 0)
        t.save(update_fields=['quantity'])
    expect(client(late).post('/event/%s/waitlist/' % slug, data=json.dumps({
        'tier_id': d2.id, 'quantity': 1}), content_type='application/json'),
        201, 'and joins the waitlist once everything is sold out')
    place = expect(client(late).get('/event/%s/waitlist/mine/' % slug), 200,
                   'sees their place in the queue')
    for name, tid in tiers.items():
        t = TicketTier.objects.get(pk=tid)
        t.quantity = {'General Admission': 25, 'VIP': 10, 'Day 2 pass': 3, 'Free entry': 20}[name]
        t.save(update_fields=['quantity'])

    # 9. My tickets, transfer, and the dead code.
    mine = expect(client(ada).get('/event/my-tickets/'), 200, 'Ada lists her tickets')
    ada_code = st.get('ada_code')
    if ada_code:
        friend = person('buyer_friend', coins=0)
        res = client(ada).post('/event/ticket/%s/transfer/' % ada_code, data=json.dumps({
            'to': friend.email}), content_type='application/json')
        data = expect(res, 200, 'Ada gives her ticket to a friend')
        new_code = ((data or {}).get('ticket') or {}).get('code') or (data or {}).get('code')
        st['friend_code'] = new_code
        theirs = expect(client(friend).get('/event/my-tickets/'), 200, 'the friend lists tickets')
        codes = [t.get('code') for t in ((theirs or {}).get('tickets') or [])]
        if new_code and new_code not in codes:
            fail('the friend holds the new code', (new_code, codes))
        if ada_code in codes:
            fail('the old code is not the friend\'s', codes)

    save_state(st)
    say('buyers: %d failure(s)' % len(FAILS))


# --------------------------------------------------------------------- rush
def rush(base='http://127.0.0.1:8000'):
    """Forty buyers, one tier with 25 seats, the same second, over HTTP."""
    import requests
    say('== rush: 40 buyers, 25 seats, same second, against %s' % base)
    st = load_state()
    slug = st['slug']
    event = Event.objects.get(slug=slug)
    tier = TicketTier.objects.create(event=event, name='Rush %s' % uuid.uuid4().hex[:4],
                                     price=1000, quantity=25)
    people = [person('rush_%02d' % i, coins=10) for i in range(40)]
    results = []
    gate = threading.Barrier(40)

    def go(u):
        gate.wait()
        r = requests.post('%s/event/%s/buy-ticket/' % (base, slug), json={
            'tier_id': tier.id, 'quantity': 1, 'pin': PIN,
            **answers(st, u.username)},
            headers={'Authorization': 'Bearer %s' % u.login_session_token}, timeout=60)
        try:
            code = r.json().get('code')
        except ValueError:
            # Not JSON at all: a 500 page. Keep the first line of it.
            code = 'NOT_JSON:' + r.text.strip().splitlines()[0][:120]
        results.append((u.username, r.status_code, code))

    threads = [threading.Thread(target=go, args=(u,)) for u in people]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    took = time.time() - t0
    sold = Ticket.objects.filter(tier=tier).count()
    won = sum(1 for _, s, _ in results if s == 201)
    refused = [c for _, s, c in results if s != 201]
    negative = UserWallet.objects.filter(user__in=people, wallet_balance__lt=0).count()
    tier.refresh_from_db()
    say('  %d requests in %.1fs: %d sold (tickets), %d answered 201, tier.sold=%d, refusals=%s'
        % (len(results), took, sold, won, tier.sold, sorted(set(refused))))
    if sold != 25 or won != 25:
        fail('exactly 25 sold', (sold, won))
    if tier.sold != sold:
        fail('tier.sold equals the ticket count', (tier.sold, sold))
    if negative:
        fail('no wallet went negative', negative)
    say('rush: %d sold of %d, %d oversold, %d negative wallets'
        % (sold, len(results), max(sold - 25, 0), negative))


def stall_rush(base='http://127.0.0.1:8000'):
    """Twelve buyers, the last five units of one product, the same second."""
    import requests
    say('== stall rush: 12 buyers, 5 units, same second')
    st = load_state()
    stall = Vendor.objects.filter(slug=st.get('stall_slug')).first()
    if stall is None:
        fail('a stall exists (run --vendor first)')
        say('stall rush: not run')
        return
    product = VendorProduct.objects.create(vendor=stall, name='Last five %s' % uuid.uuid4().hex[:3],
                                           price=1000, stock=5)
    people = [person('srush_%02d' % i, coins=10) for i in range(12)]
    results = []
    gate = threading.Barrier(12)

    def go(u):
        gate.wait()
        r = requests.post('%s/event/vendor/%s/order/' % (base, stall.slug), json={
            'items': [{'product_id': product.id, 'quantity': 1}], 'pin': PIN},
            headers={'Authorization': 'Bearer %s' % u.login_session_token}, timeout=60)
        try:
            code = r.json().get('code')
        except ValueError:
            code = 'NOT_JSON:' + r.text.strip().splitlines()[0][:120]
        results.append((r.status_code, code))

    threads = [threading.Thread(target=go, args=(u,)) for u in people]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    product.refresh_from_db()
    orders = VendorOrder.objects.filter(vendor=stall, items__product=product).distinct().count()
    won = sum(1 for s, _ in results if s == 201)
    codes = sorted(set(c for s, c in results if s != 201))
    say('  %d answered 201, %d orders, stock=%d, refusals=%s' % (won, orders, product.stock, codes))
    if orders != 5 or won != 5 or product.stock != 0:
        fail('exactly 5 sold and stock 0', (orders, won, product.stock))
    say('stall rush: %d sold of %d, stock %d, %d oversold'
        % (orders, len(results), product.stock, max(orders - 5, 0)))


# ------------------------------------------------------------------- vendor
def vendor():
    say('== vendor: a bought pitch and an invited one, products, orders, fulfilment')
    st = load_state()
    slug = st['slug']
    event = Event.objects.get(slug=slug)
    organiser = Users.objects.get(username='walk_organiser')
    trader = person('vendor_bisi', coins=50)
    slot_id = st.get('slot_id')

    # Runnable twice: a stall left by an earlier pass goes, with its orders,
    # products and the purchase row that made it, so the pitch is for sale again.
    from vent_event.models import VendorOrderItem, VendorSlotPurchase
    stale = Vendor.objects.filter(event=event, owner__username__in=('walk_vendor_bisi', 'walk_suya'))
    VendorOrderItem.objects.filter(order__vendor__in=stale).delete()
    VendorOrder.objects.filter(vendor__in=stale).delete()
    VendorProduct.objects.filter(vendor__in=stale).delete()
    VendorSlotPurchase.objects.filter(vendor__in=stale).delete()
    stale.delete()
    from vent_auth.models import UserWallet
    UserWallet.objects.filter(user=trader).update(wallet_balance=50)

    # 1. Buy the pitch: 20,000 naira = 20 coins, the rules accepted.
    before = balance(trader)
    res = client(trader).post('/event/%s/slots/%s/buy/' % (slug, slot_id), data=json.dumps({
        'accept_rules': True, 'pin': PIN}), content_type='application/json')
    data = expect(res, 201, 'Bisi buys the pitch and is a vendor at once')
    stall_slug = ((data or {}).get('vendor') or {}).get('slug')
    if balance(trader) != before - 20:
        fail('20 coins left Bisi for the pitch', (before, balance(trader)))
    if balance(organiser) < 20:
        fail('the organiser was paid for the pitch', balance(organiser))
    st['stall_slug'] = stall_slug

    # 2. The invited vendor arrives: signing up with the invited address opens
    #    the stall. claim_pending is what signup calls.
    from vent_auth.invites import claim_pending
    suya = person('suya', coins=0)
    suya.email = 'walk_suya@walk.test'
    suya.save(update_fields=['email'])
    claim_pending(suya)
    invited = Vendor.objects.filter(event=event, owner=suya).first()
    if invited is None:
        fail('the invited address became a stall on signup')
    else:
        ok('the invited vendor has a stall the moment they sign up')
        st['invited_stall_slug'] = invited.slug

    # 3. Products, with stock. The stall opens.
    c = client(trader)
    jollof = expect(c.post('/event/vendor/%s/products/' % stall_slug, data=json.dumps({
        'name': 'Jollof plate', 'price': 2000, 'stock': 40}), content_type='application/json'),
        201, 'a plate of jollof, 2,000, 40 in stock')
    hoodie = expect(c.post('/event/vendor/%s/products/' % stall_slug, data=json.dumps({
        'name': 'Hoodie', 'price': 25000, 'stock': 3, 'variants': 'S, M, L',
        'can_deliver': True}),
        content_type='application/json'), 201, 'a hoodie in three sizes, 3 in stock')
    pid = lambda d: ((d or {}).get('product') or d or {}).get('id')  # noqa: E731
    expect(c.patch('/event/my-stalls/%s/' % stall_slug, data=json.dumps({
        'status': 'live', 'description': 'Home cooking.'}), content_type='application/json'),
        200, 'the stall opens')

    # 4. Buyers order: one to collect, one to deliver with a variant.
    eater = person('buyer_eater', coins=50)
    before_e, before_t = balance(eater), balance(trader)
    res = client(eater).post('/event/vendor/%s/order/' % stall_slug, data=json.dumps({
        'items': [{'product_id': pid(jollof), 'quantity': 2}], 'pin': PIN,
        'fulfilment': 'collect'}), content_type='application/json')
    order1 = expect(res, 201, 'the eater orders two plates to collect')
    code1 = ((order1 or {}).get('order') or {}).get('code')
    if balance(eater) != before_e - 4:
        fail('4 coins left the eater', (before_e, balance(eater)))
    if balance(trader) != before_t + 4:
        fail('4 coins reached the vendor', (before_t, balance(trader)))
    wearer = person('buyer_wearer', coins=50)
    res = client(wearer).post('/event/vendor/%s/order/' % stall_slug, data=json.dumps({
        'items': [{'product_id': pid(hoodie), 'quantity': 1, 'variant': 'M'}], 'pin': PIN,
        'fulfilment': 'deliver',
        'delivery': {'name': 'Bisi Adeleke', 'phone': '08030000000',
                     'address': '12 Awolowo Road, Ikoyi, Lagos'}}),
        content_type='application/json')
    order2 = expect(res, 201, 'the wearer orders a medium hoodie delivered')
    code2 = ((order2 or {}).get('order') or {}).get('code')

    # 5. The vendor sees both the same second, and stock moved.
    seen = expect(c.get('/event/my-stalls/%s/orders/' % stall_slug), 200,
                  'the vendor lists orders')
    codes = [o.get('code') for o in ((seen or {}).get('orders') or [])]
    for code in (code1, code2):
        if code and code not in codes:
            fail('order %s is on the vendor\'s list' % code, codes)
    j = VendorProduct.objects.get(pk=pid(jollof))
    if j.stock != 38:
        fail('jollof stock went 40 -> 38', j.stock)

    # 6. Fulfilment, both paths.
    if code1:
        expect(c.post('/event/my-stalls/%s/orders/%s/status/' % (stall_slug, code1),
                      data=json.dumps({'status': 'ready'}), content_type='application/json'),
               200, 'plates: ready')
        expect(c.post('/event/my-stalls/%s/orders/%s/status/' % (stall_slug, code1),
                      data=json.dumps({'status': 'collected'}), content_type='application/json'),
               200, 'plates: collected')
    if code2:
        expect(c.post('/event/my-stalls/%s/orders/%s/status/' % (stall_slug, code2),
                      data=json.dumps({'status': 'sent', 'tracking': 'GIG-12345'}),
                      content_type='application/json'), 200, 'hoodie: sent with a tracking number')
        expect(c.post('/event/my-stalls/%s/orders/%s/status/' % (stall_slug, code2),
                      data=json.dumps({'status': 'delivered'}), content_type='application/json'),
               200, 'hoodie: arrived')
    # A stranger may not move somebody else's order.
    if code1:
        expect(client(eater).post('/event/my-stalls/%s/orders/%s/status/' % (stall_slug, code1),
                                  data=json.dumps({'status': 'ready'}), content_type='application/json'),
               404, 'the buyer may not move the vendor\'s order (my-stalls resolves only your own)')

    # 7. Money moved once: the ledger of both wallets.
    tx = Transaction.objects.filter(wallet__user=eater).order_by('-id').first()
    if tx is None or tx.amount != -4:
        fail('one transaction of -4 on the eater', getattr(tx, 'amount', None))
    save_state(st)
    say('vendor: %d failure(s)' % len(FAILS))


# --------------------------------------------------------------- influencer
def influencer():
    say('== influencer: a code, a sale through it, the commission, the view')
    st = load_state()
    slug = st['slug']
    ga = TicketTier.objects.get(pk=st['tiers']['General Admission'])
    inf = Users.objects.get(username='walk_influencer')
    ref = EventReferral.objects.get(event__slug=slug, code='BIGST')
    sold_before = ref.sold

    # A visit through the link is counted, then a purchase carrying the code.
    expect(client().post('/event/%s/ref/BIGST/visit/' % slug, data=json.dumps({
        'first_time': True}), content_type='application/json'), 200, 'a visit through the link')
    fan = person('buyer_fan', coins=50)
    res = client(fan).post('/event/%s/buy-ticket/' % slug, data=json.dumps({
        'tier_id': ga.id, 'quantity': 1, 'pin': PIN, 'ref': 'BIGST',
        **answers(st, 'Fan One')}), content_type='application/json')
    expect(res, 201, 'a fan buys through the link')
    ref.refresh_from_db()
    if ref.sold != sold_before + 1:
        fail('the link\'s sold count moved by one', (sold_before, ref.sold))

    # A purchase without the code accrues nothing to the link.
    plain = person('buyer_plain', coins=50)
    expect(client(plain).post('/event/%s/buy-ticket/' % slug, data=json.dumps({
        'tier_id': ga.id, 'quantity': 1, 'pin': PIN,
        **answers(st, 'Plain Buyer')}), content_type='application/json'),
        201, 'a plain purchase')
    ref.refresh_from_db()
    if ref.sold != sold_before + 1:
        fail('a plain purchase does not count for the link', ref.sold)

    # The organiser's view of the link.
    org = Users.objects.get(username='walk_organiser')
    rows = expect(client(org).get('/event/%s/referrals/' % slug), 200, 'the organiser lists links')
    row = next((r for r in ((rows or {}).get('results') or []) if r.get('code') == 'BIGST'), None)
    if row is None:
        fail('BIGST is in the organiser\'s list', rows)
    else:
        if int(row.get('tickets_sold') or 0) < 1:
            fail('the row shows the sale', row)
        if not row.get('has_payee'):
            fail('the row shows a payee', row)
        st['referral_row'] = row

    # The influencer's own view. This is the door that did not exist on
    # 12 September: nothing on the site showed a payee their own link.
    mine = client(inf).get('/event/referrals/mine/')
    data = expect(mine, 200, 'the influencer sees their own links')
    if data is not None:
        links = data.get('results') or []
        me = next((r for r in links if r.get('code') == 'BIGST'), None)
        if me is None:
            fail('BIGST is on the influencer\'s own list', data)
        else:
            if int(me.get('tickets_sold') or 0) < 1:
                fail('the influencer sees the sale', me)
            # 10% of a 3,000 naira (3 coin) ticket floors to 0 coins; the
            # ledger line is what is checked, not a number the walk guesses.
            from vent_event.models import EventLedgerEntry
            ledger_owed = sum(l.amount_vc for l in EventLedgerEntry.objects.filter(
                referral=ref, kind='affiliate', settled_at__isnull=True))
            if int(me.get('owed_vc') or 0) != ledger_owed:
                fail('the influencer sees what the ledger says they are owed',
                     (me.get('owed_vc'), ledger_owed))
            if not (me.get('url') or '').endswith('?ref=BIGST'):
                fail('the influencer sees the link to post', me.get('url'))
            if (me.get('event') or {}).get('slug') != slug:
                fail('the row names the event', me.get('event'))
        # A stranger's list is empty, not somebody else's.
        other = person('buyer_plain', coins=0)
        theirs = expect(client(other).get('/event/referrals/mine/'), 200, 'a plain buyer asks for links')
        if theirs is not None and theirs.get('count') != 0:
            fail('a plain buyer has no links', theirs)
        expect(client().get('/event/referrals/mine/'), 401, 'signed out is refused')
    save_state(st)
    say('influencer: %d failure(s)' % len(FAILS))


# --------------------------------------------------------------------- door
def door():
    say('== door: check in, look up, search, undo, self check-in, summary')
    st = load_state()
    slug = st['slug']
    steward = Users.objects.get(username='walk_door')
    code = st.get('friend_code') or st.get('promo_code_ticket')
    c = client(steward)
    d1 = st['day1']

    # Runnable twice: a ticket admitted on an earlier pass is let back out first.
    if Ticket.objects.filter(code=code, status='checked_in').exists():
        c.post('/event/ticket/%s/undo-check-in/' % code, data=json.dumps({}),
               content_type='application/json')

    look = expect(c.get('/event/ticket/%s/lookup/?gate=Main' % code), 200,
                  'look up admits nobody and answers the state')
    if look and look.get('already_checked_in'):
        fail('not yet checked in before the door', look)
    expect(c.get('/event/%s/door-search/?q=%s' % (slug, code[-4:])), 200,
           'search by the last four characters')
    res = c.post('/event/ticket/%s/check-in/' % code, data=json.dumps({
        'gate': 'Main', 'day': d1}), content_type='application/json')
    expect(res, 200, 'checked in at Main')
    res = c.post('/event/ticket/%s/check-in/' % code, data=json.dumps({
        'gate': 'Main', 'day': d1}), content_type='application/json')
    b = expect(res, 409, 'a second check-in is refused with when and where')
    expect(c.post('/event/ticket/%s/undo-check-in/' % code, data=json.dumps({}),
                  content_type='application/json'), 200, 'the steward takes it back')
    expect(c.post('/event/ticket/%s/check-in/' % code, data=json.dumps({
        'gate': 'Main', 'day': d1}), content_type='application/json'),
        200, 'and admits again')
    # The door log is the organiser's, not the steward's: who searched for
    # what is a record of the door, and the person on the gate reads tickets.
    expect(c.get('/event/%s/door-lookups/' % slug), 403, 'the steward cannot read the door log')
    org = client(Users.objects.get(username='walk_organiser'))
    log = expect(org.get('/event/%s/door-lookups/' % slug), 200, 'the searches were recorded')
    terms = [row.get('term') or row.get('query') for row in ((log or {}).get('lookups') or (log or {}).get('results') or [])]
    if log is not None and not any(t for t in terms):
        fail('the search term is in the door log', log)
    summary = expect(c.get('/event/%s/door-summary/' % slug), 200, 'the door summary')
    if summary:
        st['door_summary'] = summary
    # The stranger cannot work this door.
    stranger = person('stranger', coins=0)
    expect(client(stranger).post('/event/ticket/%s/check-in/' % code, data=json.dumps({
        'gate': 'Main'}), content_type='application/json'), 403, 'a stranger cannot check in')
    # Self check-in obeys its window: the event is 7 days away, so it is shut.
    gcode = st.get('guest_code')
    if gcode:
        res = client().post('/event/ticket/%s/self-check-in/' % gcode, data=json.dumps({
            'email': 'walk_guest@walk.test'}), content_type='application/json')
        if res.status_code == 200:
            fail('self check-in is shut seven days out', body(res))
        else:
            ok('self check-in refuses outside its window (%s)' % body(res).get('code'))
    save_state(st)
    say('door: %d failure(s)' % len(FAILS))


# ------------------------------------------------------------------ numbers
def numbers():
    say('== numbers: what the screens say against what the database holds')
    st = load_state()
    slug = st['slug']
    event = Event.objects.get(slug=slug)
    org = Users.objects.get(username='walk_organiser')
    c = client(org)
    mismatches = 0

    def check(label, got, want):
        nonlocal mismatches
        if got is None:
            say('  %s: not in the payload' % label)
            mismatches += 1
        elif str(got) != str(want) and (not _num(got) or not _num(want)
                                        or float(got) != float(want)):
            say('  %s: screen says %s, database says %s' % (label, got, want))
            mismatches += 1
        else:
            ok('%s = %s' % (label, want))

    # ---- the truth, computed here and nowhere else
    from vent_event.models import EventLedgerEntry
    tickets = Ticket.objects.filter(event=event)
    live = tickets.exclude(status__in=['void', 'cancelled', 'transferred', 'refunded'])
    admitted = tickets.filter(status='checked_in')
    truth = {
        'sold': live.count(),
        'checked_in': admitted.count(),
        'revenue_vc': sum(int(t.price_vc or 0) for t in live),
    }
    by_tier = {}
    for tier in event.ticket_tiers.all():
        rows = live.filter(tier=tier)
        by_tier[tier.id] = {'name': tier.name, 'sold': rows.count(),
                            'revenue_vc': sum(int(t.price_vc or 0) for t in rows),
                            'checked_in': rows.filter(status='checked_in').count()}
        if int(tier.sold) != by_tier[tier.id]['sold']:
            say('  tier %s: sold=%s but %s live ticket(s)' % (tier.name, tier.sold, rows.count()))
            mismatches += 1
    ok('tier.sold agrees with the tickets on %d tiers' % len(by_tier))
    by_day = {}
    for t in admitted.select_related('tier'):
        day = t.tier.day.isoformat() if t.tier_id and t.tier.day else 'any'
        by_day[day] = by_day.get(day, 0) + 1

    lines = EventLedgerEntry.objects.filter(event=event)

    def ledger(kind, settled=None):
        q = lines.filter(kind=kind) | lines.filter(kind='reversal', reverses__kind=kind)
        if settled is not None:
            q = q.filter(settled_at__isnull=not settled)
        return sum(l.amount_vc for l in q)
    gross = sum(l.gross_vc for l in lines.filter(kind='organiser'))
    truth['ledger_gross'] = gross
    truth['organiser_owed'] = ledger('organiser', settled=False)
    truth['platform_fee'] = ledger('platform')
    truth['affiliate_owed'] = ledger('affiliate', settled=False)

    # ---- the screens
    money = expect(c.get('/event/%s/money/' % slug), 200, 'the money tab')
    metrics = expect(c.get('/event/%s/metrics/' % slug), 200, 'the numbers tab')
    earnings = expect(c.get('/event/%s/earnings/' % slug), 200, 'earnings')
    summary = expect(c.get('/event/%s/door-summary/' % slug), 200, 'door summary')
    for name, payload in (('money', money), ('metrics', metrics),
                          ('earnings', earnings), ('summary', summary)):
        st['numbers_%s' % name] = payload
    money, metrics, earnings, summary = money or {}, metrics or {}, earnings or {}, summary or {}

    # sold and revenue, on every screen that states them
    check('money taken.count', (money.get('taken') or {}).get('count'), truth['sold'])
    check('money taken.vc', (money.get('taken') or {}).get('vc'), truth['revenue_vc'])
    check('money checked_in', money.get('checked_in'), truth['checked_in'])
    check('metrics tickets.issued', (metrics.get('tickets') or {}).get('issued'), truth['sold'])
    check('metrics tickets.checked_in', (metrics.get('tickets') or {}).get('checked_in'), truth['checked_in'])
    check('metrics revenue.vc', (metrics.get('revenue') or {}).get('vc'), truth['revenue_vc'])
    check('summary sold', summary.get('sold'), truth['sold'])
    check('summary admitted', summary.get('admitted'), truth['checked_in'])

    # by tier, on the two screens that break it down
    m_tiers = {r.get('id'): r for r in (money.get('by_tier') or [])}
    x_tiers = {r.get('tier_id'): r for r in (metrics.get('tiers') or [])}
    for tid, t in by_tier.items():
        check('money by_tier %s count' % t['name'], (m_tiers.get(tid) or {}).get('count'), t['sold'])
        check('money by_tier %s vc' % t['name'], (m_tiers.get(tid) or {}).get('vc'), t['revenue_vc'])
        check('metrics tier %s sold' % t['name'], (x_tiers.get(tid) or {}).get('sold'), t['sold'])
        check('metrics tier %s revenue' % t['name'], (x_tiers.get(tid) or {}).get('revenue_vc'), t['revenue_vc'])
        check('metrics tier %s checked_in' % t['name'], (x_tiers.get(tid) or {}).get('checked_in'), t['checked_in'])
    # by day, at the door
    for day, n in by_day.items():
        check('summary by_day %s' % day, (summary.get('by_day') or {}).get(day), n)

    # the ledger: every coin taken is owed to somebody, and the screen says so
    check('ledger gross = ticket revenue', gross, truth['revenue_vc'])
    check('ledger organiser + platform + affiliate = gross',
          ledger('organiser') + ledger('platform') + ledger('affiliate'), gross)
    check('earnings organiser_owed_vc', earnings.get('organiser_owed_vc'), truth['organiser_owed'])
    check('earnings platform_fee_vc', earnings.get('platform_fee_vc'), truth['platform_fee'])
    check('earnings affiliates_owed_vc', earnings.get('affiliates_owed_vc'), truth['affiliate_owed'])
    check('money owed.vc = organiser owed + affiliates owed',
          (money.get('owed') or {}).get('vc'), truth['organiser_owed'] + truth['affiliate_owed'])

    # vendor sales: what the stall's orders endpoint totals against the orders
    for stall in Vendor.objects.filter(event=event):
        orders = VendorOrder.objects.filter(vendor=stall).exclude(status='cancelled')
        want = sum(int(o.total_vc or 0) for o in orders)
        got = expect(c.get('/event/vendor/%s/orders/' % stall.slug), 200,
                     'the organiser reads %s orders' % stall.name) or {}
        check('vendor %s revenue_vc' % stall.name, got.get('revenue_vc'), want)
        check('vendor %s count' % stall.name, got.get('count'), VendorOrder.objects.filter(vendor=stall).count())

    # influencer commission: the organiser's list and the payee's own view agree with the ledger
    refs_seen = expect(c.get('/event/%s/referrals/' % slug), 200, 'referrals') or {}
    for row in refs_seen.get('results') or []:
        ref_live = live.filter(referral_id=row.get('id'))
        check('referral %s tickets_sold' % row.get('code'), row.get('tickets_sold'), ref_live.count())
        check('referral %s revenue_vc' % row.get('code'), row.get('revenue_vc'),
              sum(int(t.price_vc or 0) for t in ref_live))
    inf = Users.objects.get(username='walk_influencer')
    mine = expect(client(inf).get('/event/referrals/mine/'), 200, 'the payee\'s own view') or {}
    for row in mine.get('results') or []:
        if (row.get('event') or {}).get('slug') != slug:
            continue
        owed = sum(l.amount_vc for l in lines.filter(referral_id=row.get('id'), settled_at__isnull=True)
                   .filter(Q(kind='affiliate') | Q(kind='reversal', reverses__kind='affiliate')))
        check('payee %s owed_vc' % row.get('code'), row.get('owed_vc'), owed)

    save_state(st)
    say('numbers: %d mismatch(es)' % mismatches)


def _num(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


# -------------------------------------------------------------------- roles
def roles():
    """Every event route, every role, the status code each gets, for a read
    and for a write. The organiser and admin columns are never sent at a
    route that would change the walk's own event (delete, settle, refund);
    those cells read `skip`, and the gate is about the other six roles."""
    say('== roles: every route against every role')
    import re
    st = load_state()
    slug = st['slug']
    src = open(os.path.join(ROOT, 'vent_event', 'urls.py'), encoding='utf-8').read()
    routes = re.findall(r'path\(\s*["\']([^"\']+)["\']', src)
    people = {
        'stranger': None,
        'buyer': Users.objects.get(username='walk_buyer_ada'),
        'vendor': Users.objects.get(username='walk_vendor_bisi'),
        'influencer': Users.objects.get(username='walk_influencer'),
        'door': Users.objects.get(username='walk_door'),
        'manager': Users.objects.get(username='walk_manager'),
        'organiser': Users.objects.get(username='walk_organiser'),
        'admin': Users.objects.get(username='walk_admin'),
    }
    outsiders = ('stranger', 'buyer', 'vendor', 'influencer')
    # Writes on the organiser's console: the four outsiders must be refused.
    organiser_only_writes = re.compile(
        r'(tiers|sessions/manage|run-of-show|slots|referrals|promos|managers|announcements|'
        r'polls|holds|short-links|sponsors|checkout-fields/manage|comp-tickets|'
        r'self-check-in/settings|fee-bearer|settle|email-limits|delete|restore|'
        r'edit-event|link-tournament|unlink-tournament|abandoned|days|programme|'
        r'attendees|export|fields|door-lookups)')
    # Reads that show the organiser's money, people or door log.
    organiser_only_reads = re.compile(
        r'(money|metrics|earnings|attendees|door-lookups|abandoned|export|funnel|'
        r'referrals/$|promos|managers|holds|settlements|email-limits|comp-tickets|'
        r'ledger|waitlist/$|checkout-fields/manage)')
    # Routes that would change the walk's own event if the organiser or an
    # admin reached them with a real body. Their cells are not sent.
    destructive = re.compile(r'(delete|restore|settle|refund|cancel|undo|transfer|void|reset)')
    fills = {'<str:event_id>': slug, '<int:tier_id>': '1', '<int:slot_id>': '1',
             '<str:vendor_id>': st.get('stall_slug') or 'x', '<str:code>': st.get('friend_code') or 'X',
             '<int:session_id>': '1', '<int:hold_id>': '1', '<int:overlay_id>': '1',
             '<int:layer_id>': '1', '<int:asset_id>': '1', '<str:kind>': 'scoreboard',
             '<str:role>': 'a', '<int:day_id>': '1', '<int:item_id>': '1',
             '<int:link_id>': '1', '<int:sponsor_id>': '1', '<int:product_id>': '1',
             '<int:poll_id>': '1', '<int:referral_id>': '1', '<int:promo_id>': '1',
             '<int:manager_id>': '1', '<str:tournament_id>': 'x', '<str:token>': 'x',
             '<int:field_id>': '1', '<int:announcement_id>': '1', '<int:order_id>': '1',
             '<str:order_code>': 'X', '<int:purchase_id>': '1', '<str:username>': 'x',
             '<int:run_id>': '1', '<int:entry_id>': '1', '<int:template_id>': '1'}
    wrong = []
    table = []
    unfilled = set()
    for route in routes:
        path = '/event/' + route
        for k, v in fills.items():
            path = path.replace(k, v)
        if '<' in path:
            unfilled.add(route)
            continue
        row = {}
        for role, user in people.items():
            cl = client(user)
            get = cl.get(path).status_code
            if role in ('organiser', 'admin', 'manager') and destructive.search(route):
                post = 'skip'
            else:
                # A write probe with an empty body: what matters is whether the
                # door is open, not whether the form is filled.
                post = cl.post(path, data=json.dumps({}), content_type='application/json').status_code
            row[role] = {'get': get, 'post': post}
        table.append((route, row))
        if organiser_only_writes.search(route):
            for role in outsiders:
                if row[role]['post'] in (200, 201):
                    wrong.append((route, role, 'POST', row[role]['post']))
        if organiser_only_reads.search(route):
            for role in outsiders:
                if row[role]['get'] == 200:
                    wrong.append((route, role, 'GET', row[role]['get']))
        # A stranger never gets a 500, and never a 200 on a write. Two writes
        # are public on purpose: the analytics beacon and the influencer-link
        # visit counter, both of which exist to be hit by people with no account.
        public_writes = ('<str:event_id>/track/', '<str:event_id>/ref/<str:code>/visit/')
        if route not in public_writes and row['stranger']['post'] not in (400, 401, 403, 404, 405, 409, 'skip'):
            wrong.append((route, 'stranger', 'POST', row['stranger']['post']))
        for role, cell in row.items():
            for verb in ('get', 'post'):
                if cell[verb] == 500:
                    wrong.append((route, role, verb.upper(), 500))
    for route, role, verb, code in wrong:
        say('  LET IN: %s %s as %s -> %s' % (verb, route, role, code))
    for route in sorted(unfilled):
        say('  UNFILLED: %s' % route)
    json.dump(table, open(os.path.join(ROOT, 'tools', '.walk-event-roles.json'), 'w'), indent=1)
    say('roles: %d route(s), %d that let the wrong role in'
        % (len(routes) - len(unfilled), len(wrong)))


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--setup' in args:
        setup()
    if '--coverage' in args:
        coverage()
    if '--buyers' in args:
        buyers()
    if '--rush' in args:
        rush()
    if '--vendor' in args:
        vendor()
    if '--stall-rush' in args:
        stall_rush()
    if '--influencer' in args:
        influencer()
    if '--door' in args:
        door()
    if '--numbers' in args:
        numbers()
    if '--roles' in args:
        roles()
    if not args:
        print(__doc__)
