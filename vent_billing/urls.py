"""Addresses for subscriptions and memberships.

Slugs and opaque tokens, never a primary key. A plan has a name so it carries a
slug that follows it; a subscription and an invoice have no name, so they carry
`sub_<hex>` and `inv_<hex>`, which are stable, not enumerable, and not the
database key. Sequential ids in an address let anybody walk the whole table by
counting, and this one holds what people pay for.
"""
from django.urls import path

from . import switch, views_org, views_plans, views_subs

urlpatterns = [
    # Reading. Public where the plan is public.
    path('catalogue/', switch.gated(views_plans.catalogue), name='billing_catalogue'),
    path('plans/', switch.gated(views_plans.plan_list), name='billing_plan_list'),
    path('plans/create/', switch.gated(views_plans.create_plan), name='billing_create_plan'),
    path('plans/public/', switch.gated(views_plans.public_plans), name='billing_public_plans'),
    path('plan/<str:ref>/', switch.gated(views_plans.plan_detail), name='billing_plan_detail'),
    path('plan/<str:ref>/edit/', switch.gated(views_plans.edit_plan), name='billing_edit_plan'),
    path('plan/<str:ref>/members-area/', switch.gated(views_plans.members_area),
         name='billing_members_area'),

    # The subscriber.
    path('subscriptions/', switch.gated(views_subs.my_subscriptions), name='billing_my_subs'),
    path('invoices/', switch.gated(views_subs.my_invoices), name='billing_my_invoices'),
    path('entitlements/', switch.gated(views_subs.entitlements), name='billing_entitlements'),
    path('plan/<str:ref>/subscribe/', switch.gated(views_subs.subscribe), name='billing_subscribe'),
    path('subscription/<str:token>/', switch.gated(views_subs.subscription_detail),
         name='billing_sub_detail'),
    path('subscription/<str:token>/cancel/', switch.gated(views_subs.cancel),
         name='billing_cancel'),
    path('subscription/<str:token>/resume/', switch.gated(views_subs.resume),
         name='billing_resume'),
    path('subscription/<str:token>/change/', switch.gated(views_subs.change_plan),
         name='billing_change_plan'),

    # The organiser.
    path('org-overview/', switch.gated(views_org.org_overview), name='billing_org_overview'),
    path('plan/<str:ref>/members/', switch.gated(views_org.members), name='billing_members'),
    path('plan/<str:ref>/earnings/', switch.gated(views_org.earnings), name='billing_earnings'),
    path('plan/<str:ref>/invoices/', switch.gated(views_org.plan_invoices),
         name='billing_plan_invoices'),
    path('plan/<str:ref>/settle/', switch.gated(views_org.settle), name='billing_settle'),
    path('invoice/<str:token>/refund/', switch.gated(views_org.refund), name='billing_refund'),
    path('subscription/<str:token>/end/', switch.gated(views_org.end_membership),
         name='billing_end_membership'),
]
