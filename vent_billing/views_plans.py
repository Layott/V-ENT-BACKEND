"""Plans: reading them, and an organiser writing them.

    GET    /billing/plans/                    plans for one seller, public ones
    GET    /billing/plans/public/             every public plan, for the sitemap
    GET    /billing/plan/<slug>/              one plan
    POST   /billing/plans/create/             organiser
    POST   /billing/plan/<slug>/edit/         organiser
    GET    /billing/catalogue/                the benefit catalogue, sent once
    GET    /billing/plan/<slug>/members-area/ what a member is paying for

The last one is the whole of gate C1 in one endpoint. It reads the subscription
and the clock on every request, so a membership that lapsed an hour ago is
refused an hour ago, without anything having run in between and without the
interface having been redeployed.
"""
from rest_framework import status
from rest_framework.decorators import api_view

from vent_auth.models import Organization
from vent_auth.slugs import lookup_kwargs

from . import benefits, clock, serializers
from .models import Plan
from .permissions import (
    error, may_manage_org, may_manage_plan, ok, require_viewer, viewer,
)


def _plan_by_ref(ref):
    """A plan by slug, falling back to the id and then to a retired address.

    Every address a plan has ever had keeps working: a rename moves the slug
    and the old one is remembered, so a link posted in a group chat in June
    still opens the right page in December.
    """
    plan = Plan.objects.filter(
        **lookup_kwargs(ref, id_field='plan_id')).first()
    if plan is not None:
        return plan, None
    from vent_auth.models_slughistory import SlugHistory
    row = SlugHistory.objects.filter(entity_type='plan', slug=str(ref)).first()
    if row is None:
        return None, None
    moved = Plan.objects.filter(plan_id=row.entity_id).first()
    return moved, (moved.slug if moved else None)


def _org_by_ref(ref):
    return Organization.objects.filter(
        **lookup_kwargs(ref, id_field='org_id')).first()


@api_view(['GET'])
def plan_list(request):
    """`?org=<slug>` or `?owner=<username>`. Public plans, readable by anybody.

    A manager of the seller also sees the drafts, marked as drafts, so the
    organiser's console and the public page are the same endpoint rather than
    two that can disagree about what exists.
    """
    who = viewer(request)
    rows = Plan.objects.select_related('org', 'owner')

    org_ref = request.query_params.get('org')
    owner_ref = request.query_params.get('owner')
    if org_ref:
        org = _org_by_ref(org_ref)
        if org is None:
            return error('Organization not found.', 'NOT_FOUND',
                         status.HTTP_404_NOT_FOUND)
        rows = rows.filter(org=org)
        privileged = may_manage_org(who, org)
    elif owner_ref:
        rows = rows.filter(owner__username=owner_ref, org__isnull=True)
        privileged = who is not None and who.username == owner_ref
    else:
        return error('Say whose plans to list.', 'VALIDATION_ERROR')

    if not privileged:
        rows = rows.filter(status=Plan.STATUS_PUBLIC)

    return ok({
        'plans': [serializers.plan_row(p, request=request,
                                       include_private=privileged)
                  for p in rows],
        'can_manage': bool(privileged),
    })


@api_view(['GET'])
def public_plans(request):
    """Every public plan on the platform, for the sitemap.

    A sitemap is a positive claim that a URL is worth crawling, so this answers
    only what is actually public: a draft is not listed, and neither is a
    retired plan, because a page saying "closed to new members" is not what
    somebody clicking a search result wants.

    Its own endpoint rather than `plan_list` with no filter. That view refuses
    a request that does not say whose plans it wants, and relaxing it so a
    sitemap could reuse it would mean the seller filter could be forgotten
    somewhere it matters.
    """
    rows = Plan.objects.filter(status=Plan.STATUS_PUBLIC).select_related(
        'org', 'owner')[:1000]
    return ok({'plans': [{
        'slug': p.slug,
        'name': p.name,
        'updated_at': p.updated_at.isoformat(),
    } for p in rows]})


@api_view(['GET'])
def plan_detail(request, ref):
    who = viewer(request)
    plan, moved_to = _plan_by_ref(ref)
    if plan is None:
        return error('That plan could not be found.', 'NOT_FOUND',
                     status.HTTP_404_NOT_FOUND)
    if moved_to and moved_to != str(ref):
        # 200 with a move, not a 301. `fetch()` follows a redirect
        # transparently and would chase a frontend path against the API host,
        # arriving as a 404 with the body discarded. The envelope reports the
        # move and the app rewrites its own address.
        return ok({'url': '/plans/%s' % moved_to}, 'moved')

    manager = may_manage_plan(who, plan)
    if plan.status == Plan.STATUS_DRAFT and not manager:
        # A draft is not readable by anybody who could subscribe to it: a price
        # somebody has seen is a price they expect to be charged.
        return error('That plan could not be found.', 'NOT_FOUND',
                     status.HTTP_404_NOT_FOUND)

    row = serializers.plan_row(plan, request=request, include_private=manager)
    row['can_manage'] = manager

    if who is not None:
        from .lifecycle import existing_subscription
        mine = existing_subscription(who, plan)
        row['my_subscription'] = (serializers.subscription_row(mine, request=request)
                                  if mine else None)
    else:
        row['my_subscription'] = None

    from . import charging
    row['fee_pct'] = charging.platform_rate()
    return ok(row)


@api_view(['GET'])
def catalogue(request):
    """The benefit catalogue, sent rather than copied.

    Five label maps for tournament formats and two copies of the event console
    tabs are why this is an endpoint. The interface translates the keys; it
    never holds its own list of what a benefit can be.
    """
    return ok({'benefits': benefits.catalogue_rows(),
               'intervals': [clock.MONTHLY, clock.YEARLY]})


def _read_plan_fields(data):
    """(fields, error). Shared by create and edit so they cannot drift."""
    from vent_auth.views_wallet import NGN_PER_COIN

    name = str(data.get('name') or '').strip()
    if not name:
        return None, error('Give the plan a name.', 'VALIDATION_ERROR')
    if len(name) > 120:
        return None, error('That name is too long.', 'VALIDATION_ERROR')

    interval = str(data.get('interval') or clock.MONTHLY).strip()
    if interval not in clock.INTERVALS:
        return None, error('Say whether this is billed monthly or yearly.',
                           'INVALID_INTERVAL')

    raw_ngn = data.get('price_ngn')
    raw_vc = data.get('price_vc')
    try:
        if raw_ngn not in (None, ''):
            price_ngn = int(raw_ngn)
            if price_ngn < 0:
                raise ValueError
            if price_ngn % NGN_PER_COIN:
                # Refused rather than rounded. Silently repricing what somebody
                # typed is how a plan ends up costing something its organiser
                # never chose, and they would find out from a member.
                lower = (price_ngn // NGN_PER_COIN) * NGN_PER_COIN
                return None, error(
                    'A price has to be a whole number of VENT COINS.',
                    'PRICE_NOT_WHOLE_COINS',
                    http=status.HTTP_400_BAD_REQUEST,
                    nearest_lower=lower, nearest_upper=lower + NGN_PER_COIN,
                    ngn_per_coin=NGN_PER_COIN)
            price_vc = price_ngn // NGN_PER_COIN
        else:
            price_vc = int(raw_vc or 0)
            if price_vc < 0:
                raise ValueError
    except (TypeError, ValueError):
        return None, error('That price is not a number.', 'VALIDATION_ERROR')

    try:
        trial_days = int(data.get('trial_days') or 0)
    except (TypeError, ValueError):
        trial_days = 0
    if trial_days < 0 or trial_days > 90:
        return None, error('A trial can be up to 90 days.', 'INVALID_TRIAL')

    status_value = str(data.get('status') or Plan.STATUS_DRAFT).strip()
    if status_value not in dict(Plan.STATUS_CHOICES):
        return None, error('That is not a state a plan can be in.',
                           'VALIDATION_ERROR')

    return {
        'name': name,
        'tagline': str(data.get('tagline') or '')[:200],
        'description': str(data.get('description') or ''),
        'member_content': str(data.get('member_content') or ''),
        'interval': interval,
        'price_vc': price_vc,
        'trial_days': trial_days,
        'benefits': benefits.clean(data.get('benefits')),
        'status': status_value,
    }, None


@api_view(['POST'])
def create_plan(request):
    """`{org?, name, price_ngn, interval, benefits, ...}`.

    With no `org`, the plan belongs to the person. Not everybody who wants to
    sell a membership has made an organisation, and making them create one
    first is a wall in front of the feature rather than a rule that protects
    anything.
    """
    who, err = require_viewer(request)
    if err:
        return err

    fields, err = _read_plan_fields(request.data)
    if err:
        return err

    org = None
    org_ref = request.data.get('org')
    if org_ref:
        org = _org_by_ref(org_ref)
        if org is None:
            return error('Organization not found.', 'NOT_FOUND',
                         status.HTTP_404_NOT_FOUND)
        if not may_manage_org(who, org):
            return error('Your role in this organization does not allow that.',
                         'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    plan = Plan(org=org, owner=who, **fields)
    plan.save()
    return ok(serializers.plan_row(plan, request=request, include_private=True),
              'Plan created.')


@api_view(['POST'])
def edit_plan(request, ref):
    who, err = require_viewer(request)
    if err:
        return err

    plan, _moved = _plan_by_ref(ref)
    if plan is None:
        return error('That plan could not be found.', 'NOT_FOUND',
                     status.HTTP_404_NOT_FOUND)
    if not may_manage_plan(who, plan):
        return error('Only the organiser can change this plan.', 'FORBIDDEN',
                     status.HTTP_403_FORBIDDEN)

    fields, err = _read_plan_fields(request.data)
    if err:
        return err

    price_changed = fields['price_vc'] != plan.price_vc
    interval_changed = fields['interval'] != plan.interval
    if (price_changed or interval_changed) and plan.live_subscriptions().exists():
        # A price change cannot reach through to people already paying. Their
        # invoices carry the price they agreed to, and rewriting the plan under
        # them would charge a number nobody was told about. A new plan and a
        # message is the honest way to raise a price.
        return error('People are already on this plan, so its price and its '
                     'billing period cannot change. Make a new plan instead.',
                     'PLAN_HAS_MEMBERS')

    for key, value in fields.items():
        setattr(plan, key, value)
    plan.save()
    return ok(serializers.plan_row(plan, request=request, include_private=True),
              'Plan updated.')


@api_view(['GET'])
def members_area(request, ref):
    """What a member is paying for. The API is what enforces it.

    Gate C1. Read the subscription and the clock on THIS request: a lapsed
    member is refused on the next request they make, not at the next deploy and
    not when a screen happens to re-render.
    """
    who, err = require_viewer(request)
    if err:
        return err

    plan, _moved = _plan_by_ref(ref)
    if plan is None:
        return error('That plan could not be found.', 'NOT_FOUND',
                     status.HTTP_404_NOT_FOUND)

    if may_manage_plan(who, plan):
        return ok({'member_content': plan.member_content, 'as': 'organiser'})

    from .lifecycle import existing_subscription
    mine = existing_subscription(who, plan)
    if mine is None or not mine.has_access():
        return error('This is for members of this plan.', 'NOT_A_MEMBER',
                     status.HTTP_403_FORBIDDEN)

    return ok({'member_content': plan.member_content, 'as': 'member',
               'access_until': mine.period_end.isoformat()})
