"""Test fixtures, in one place so every test builds the same shapes.

Not `tests_*`, so the runner does not collect it, and importable by every test
module here. A test that builds its own user and its own wallet is a test that
eventually builds one slightly differently from the code under test - which is
how a hand-written payload ends up passing against a request nothing makes.
"""
from django.utils import timezone

from vent_auth.models import (AdminSetting, Organization, OrgMember, SavedCard,
                              UserWallet, Users)

from .models import Plan


def a_user(name, coins=0):
    """A user with a wallet and a bearer token, as every V-ENT test builds one."""
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=('b-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    UserWallet.objects.create(user_wallet_id=name[:10], user=user,
                              wallet_balance=coins,
                              pin_hash='pbkdf2_sha256$dummy')
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def balance(user):
    return UserWallet.objects.get(user=user).wallet_balance


def an_org(owner, name='Vermillion Test'):
    org = Organization.objects.create(org_name=name, org_creator=owner,
                                      org_owner=owner)
    OrgMember.objects.create(org=org, user=owner, role=OrgMember.ROLE_OWNER)
    return org


def a_plan(owner, *, org=None, name='Inner Circle', price_vc=5,
           interval='monthly', status=Plan.STATUS_PUBLIC, trial_days=0,
           benefits=None, member_content='Discord: vent.test/inner'):
    plan = Plan(owner=owner, org=org, name=name, price_vc=price_vc,
                interval=interval, status=status, trial_days=trial_days,
                benefits=benefits if benefits is not None else [],
                member_content=member_content)
    plan.save()
    return plan


def a_card(user, code='AUTH_test123'):
    return SavedCard.objects.create(
        user=user, authorization_code=code, signature='sig-%s' % user.pk,
        brand='Visa', last4='4081', exp_month='12', exp_year='2030',
        bank='Test Bank', is_default=True)


def set_fee(pct):
    row = AdminSetting.load()
    data = dict(row.data or {})
    fees = dict(data.get('platform_fees') or {})
    fees['subscription_fee_pct'] = pct
    data['platform_fees'] = fees
    row.data = data
    row.save()
