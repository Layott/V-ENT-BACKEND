"""Every record on the site, for the admin console (inbox 420).

CEO, 8 October 2026: "Let it work as a proper CMS for the entire site, if we
add a new feature let it automatically build the control on the admin
dashboard, so everything on the website can be edited, removed, deleted, or
even restored, should be able to check all info for specific". Decided the same
day: every model appears automatically; secret and sensitive columns never leave
the server; money records are view only and a correction is a new entry with a
reason; delete goes to a bin for 90 days, restore brings it back, purge is for a
super admin; every edit is kept and can be reverted; a new "Edit records"
permission. Why the console builds this rather than using Django's admin:
docs/adr/0001-admin-records-own-view.md.

## Automatic, with the exceptions written down

`listed_models()` is every model Django knows, minus the apps and models named
in EXCLUDED_APPS and EXCLUDED_MODELS, each with the reason. A model added next
month appears in the console the day it is migrated, with no code here. The one
thing a new model can need is a MARK: tests_records.py fails when a model has a
money-looking column and is neither MONEY nor NOT_MONEY, or a secret-looking
column that is neither in SENSITIVE nor NOT_SENSITIVE. The marking is the whole
of the manual work, and the test is what makes it impossible to forget.

## What never leaves the server

SENSITIVE names the columns returned as {"hidden": true} and never as a value:
password hashes, session and admin tokens, wallet PIN hashes, two-factor
secrets, partner secrets, bank account numbers, saved card authorisations. They
cannot be edited here either. The bin keeps full rows (it has to, to restore
them) and never returns them: the bin list is labels and counts.

## Money

A MONEY record cannot be edited or deleted here. What is wrong with money is
corrected by a NEW entry with a reason, through the same `wallets.transfer`
every other movement uses, so the correction lands on the owner's statement like
anything else and the original line still says what happened.
"""
import json
import re
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.core import serializers
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from . import softdelete

BIN_DAYS = 90

# ---------------------------------------------------------------------------
# What is listed
# ---------------------------------------------------------------------------

EXCLUDED_APPS = {
    'admin': "Django's own admin log. The Django admin is off in production, so nothing writes it.",
    'auth': "Django's permission and group tables. V-ENT roles live on Users.admin_role.",
    'contenttypes': 'Django bookkeeping: one row per model, written by migrations.',
    'sessions': "Django's server sessions, which hold sign-in state; the site signs in with its own token.",
    'sites': 'One row naming the domain, used by the sign-in library.',
    'account': "The sign-in library's email address table; a person's email is on their account.",
    'socialaccount': "The sign-in library's provider tokens and app secrets.",
    'authtoken': 'The REST library tokens: credentials, and unused by the site.',
}

EXCLUDED_MODELS = {
    'vent_auth.recordbin': 'The bin itself: it has its own screen, and its rows hold whole deleted records.',
    'vent_auth.recordversion': 'The edit history itself: shown on each record, and reverted from there.',
}

# Money: a record of value that moved or is held. View only; corrected by a new
# entry with a reason. Everything that prices something (a tier, a plan, a
# listing) is NOT money: changing a price moves nothing.
MONEY = {
    'vent_auth.userwallet': 'a balance',
    'vent_auth.teamwallet': 'a balance',
    'vent_auth.orgwallet': 'a balance',
    'vent_auth.transaction': 'a line on a statement',
    'vent_auth.withdrawalrequest': 'money leaving for a bank',
    'vent_auth.premiumpurchase': 'coins paid for premium',
    'vent_auth.foreigncharge': 'a card payment in another currency',
    'vent_auth.savedcard': 'a card kept for paying again',
    'vent_auth.payoutaddress': 'where payouts are sent',
    'vent_anime.chapterpurchase': 'coins paid for a chapter',
    'vent_marketplace.bid': 'coins offered and held',
    'vent_marketplace.purchase': 'a sale and its commission',
    'vent_tournament.prizepayout': 'a prize paid',
    'vent_tournament.prizeschedule': 'prizes that will be paid at a set time',
    'vent_billing.subscription': 'a paid membership with a card on it',
    'vent_billing.invoice': 'a membership charge',
    'vent_billing.billingsettlement': 'membership money paid out',
    'vent_billing.billingledgerentry': 'a membership ledger line',
    'vent_event.ticket': 'a ticket somebody paid for',
    'vent_event.vendorslotpurchase': 'a pitch or stall somebody paid for',
    'vent_event.vendororder': 'an order and its fees',
    'vent_event.vendororderitem': 'a line of an order',
    'vent_event.eventsettlement': 'event money paid out',
    'vent_event.eventledgerentry': 'an event ledger line',
}

NOT_MONEY = {
    'vent_auth.currency': 'an exchange rate for display; nobody is charged at it',
    'vent_anime.series': 'the prices a creator asks',
    'vent_anime.chapter': 'the price of early access',
    'vent_marketplace.listing': 'the price a seller asks',
    'vent_marketplace.wishlist': 'the most somebody would pay',
    'vent_tournament.tournament': 'the advertised prize pool and entry fee',
    'vent_tournament.tournamentprizedistribution': 'how the prize pool is split, before anything is paid',
    'vent_tournament.tournamentregistration': 'an entry; the fee itself is on the statement',
    'vent_tournament.runsheetitem': 'minutes in a running order',
    'vent_cards.gamecard': 'a card price in a squad game',
    'vent_cards.squadrules': 'a squad budget in a game',
    'vent_billing.plan': 'the price of a membership plan',
    'vent_event.event': 'the advertised entry fee',
    'vent_event.tickettier': 'the price of a ticket type',
    'vent_event.eventreferral': 'a referral commission rate',
    'vent_event.eventpromo': 'a promo code value',
    'vent_event.vendorslot': 'the price of a pitch or stall type',
    'vent_event.vendor': 'who bears the fee for a stall',
    'vent_event.vendorproduct': 'the price of an item on a stall',
    'vent_event.abandonedcheckout': 'a basket nobody paid for',
    'vent_event.eventattendeeorigin': 'a rounded location; the word "cell" is not money',
    'vent_event.geocodedaddress': 'a map position',
    'vent_auth.feedback': 'a message, not money',
}

MONEY_WORDS = re.compile(
    r'amount|balance|price|fee|paid|payout|prize|refund|wallet|coins|charge|settle|'
    r'ledger|commission|debit|credit|naira|_vc$|_ngn$', re.I)

# Secret or sensitive columns. Never returned, never editable.
SENSITIVE = {
    'vent_auth.users': {
        'password': 'a password hash',
        'login_session_token': 'the sign-in token',
        'admin_session_token': 'the admin sign-in token',
    },
    'vent_auth.verificationtoken': {'token': 'an email verification token'},
    'vent_auth.teams': {'join_password': 'the password to join the team'},
    'vent_auth.orginvite': {'token': 'an invitation link token'},
    'vent_auth.userwallet': {'pin_hash': 'a wallet PIN hash'},
    'vent_auth.teamwallet': {'pin_hash': 'a wallet PIN hash', 'team_wallet_pin': 'an old wallet PIN'},
    'vent_auth.orgwallet': {'pin_hash': 'a wallet PIN hash', 'org_wallet_pin': 'an old wallet PIN'},
    'vent_auth.waitlistreservation': {'claim_token': 'the link that claims a reserved name'},
    'vent_auth.withdrawalrequest': {'account_number': 'a bank account number'},
    'vent_auth.admintotp': {'secret': 'a two-factor secret'},
    'vent_auth.usertotp': {'secret': 'a two-factor secret'},
    'vent_auth.savedcard': {'authorization_code': 'a saved card authorisation'},
    'vent_auth.teaminvite': {'token': 'an invitation link token'},
    'vent_auth.payoutaddress': {'confirm_code': 'the code that confirms a payout address'},
    'vent_anime.readingroom': {'password_hash': 'a room password hash', 'token': 'the room link token'},
    'vent_tournament.bracketmatch': {'room_password': 'an in-game room password'},
    'vent_tournament.brmap': {'room_password': 'an in-game room password'},
    'vent_tournament.tournamentinvite': {'code': 'an invitation code that admits somebody'},
    'vent_tournament.tournamentoverlay': {'token': 'the link a stream reads an overlay from'},
    'vent_tournament.broadcastsession': {'token': 'the link that controls a broadcast'},
    'vent_tournament.runsheet': {'token': 'the link a running order is read from'},
    'vent_partners.partner': {'verification_secret': 'a partner secret',
                              'sso_client_secret_hash': 'a partner secret hash'},
    'vent_partners.partnerapikey': {'secret_hash': 'an API key hash'},
    'vent_partners.oauthauthorizationcode': {'code_hash': 'a sign-in code hash',
                                             'code_challenge': 'a sign-in proof'},
    'vent_partners.oauthaccesstoken': {'token_hash': 'an access token hash'},
    'vent_partners.inboundlogin': {'code_verifier': 'a sign-in proof'},
    'vent_billing.subscription': {'token': 'the link that manages a membership', 'card': 'a saved card'},
    'vent_billing.invoice': {'token': 'the link that pays an invoice'},
    'vent_event.tickettier': {'access_code': 'the code that unlocks a hidden ticket'},
    'vent_event.ticket': {'code': 'the code that gets somebody in the door'},
    'vent_event.tickettransfer': {'old_code': 'a door code', 'new_code': 'a door code'},
    'vent_event.vendororder': {'code': 'the code a stall collects an order with'},
}

# Columns whose NAME looks secret and are not.
NOT_SENSITIVE = {
    'vent_auth.users.login_session_created_at': 'when somebody signed in',
    'vent_auth.users.admin_session_created_at': 'when an admin signed in',
    'vent_auth.users.login_session_2fa_at': 'when a second factor was given',
    'vent_auth.currency.code': 'a currency code like NGN',
    'vent_auth.teams.password_protected': 'whether a team has a join password',
    'vent_auth.userwallet.pin_failures': 'a count of wrong PINs',
    'vent_auth.userwallet.pin_locked_until': 'when a locked PIN opens again',
    'vent_auth.teamwallet.pin_failures': 'a count of wrong PINs',
    'vent_auth.teamwallet.pin_locked_until': 'when a locked PIN opens again',
    'vent_auth.orgwallet.pin_failures': 'a count of wrong PINs',
    'vent_auth.orgwallet.pin_locked_until': 'when a locked PIN opens again',
    'vent_auth.waitlistreservation.referral_code': 'a public referral code',
    'vent_auth.usertotp.code_failures': 'a count of wrong codes',
    'vent_auth.usertotp.code_locked_until': 'when a locked code opens again',
    'vent_auth.club.is_private': 'whether a club is private',
    'vent_auth.thread.is_pinned': 'whether a thread is pinned',
    'vent_auth.scrim.map_code': 'a map name',
    'vent_marketplace.listing.discount_code': 'a public discount code a seller advertises',
    'vent_tournament.bracketmatch.running_order': 'a position in the running order',
    'vent_tournament.bracketmatch.room_code': 'the room number shown to both sides',
    'vent_tournament.brmap.room_code': 'the room number shown to every team',
    'vent_tournament.tournamentmetric.key': 'a stat name',
    'vent_tournament.matchplayerstat.key': 'a stat name',
    'vent_tournament.broadcastelement.session': 'which broadcast an element belongs to',
    'vent_tournament.broadcastslot.session': 'which broadcast a slot belongs to',
    'vent_tournament.brocrjob.error_code': 'why a screenshot could not be read',
    'vent_partners.partnerapikey.key_id': 'the public half of an API key',
    'vent_partners.oauthauthorizationcode.code_challenge_method': 'the name of a hashing method',
    'vent_cards.squadrules.max_card_rating': 'a rating limit',
    'vent_cards.lineupslot.card': 'which player card is in a slot',
    'vent_billing.subscription.dunning_attempt': 'a count of payment retries',
    'vent_billing.invoice.failure_code': 'why a payment failed',
    'vent_event.eventreferral.code': 'a public referral code',
    'vent_event.eventpromo.code': 'a promo code an organiser advertises',
    'vent_event.eventsession.session_id': 'an anonymous visit id for the sales funnel',
    'vent_event.shortlink.token': 'a public short link',
    'vent_event.eventpresence.pings_open': 'whether somebody accepts meet-up requests',
}

SECRET_WORDS = re.compile(
    r'pass|secret|token|pin|totp|hash|key|otp|code|session|salt|cvv|card|'
    r'account_number|bvn|nin\b|iban|private', re.I)


def key_of(model):
    return model._meta.label_lower


def listed_models():
    """Every model the console shows, in a stable order."""
    out = []
    for model in apps.get_models():
        meta = model._meta
        if meta.app_label in EXCLUDED_APPS or key_of(model) in EXCLUDED_MODELS:
            continue
        if meta.proxy:
            continue
        out.append(model)
    return sorted(out, key=lambda m: (m._meta.app_label, m._meta.model_name))


def model_for(key):
    key = str(key or '').strip().lower()
    for model in listed_models():
        if key_of(model) == key:
            return model
    return None


def is_money(model):
    return key_of(model) in MONEY


def hidden_fields(model):
    return set(SENSITIVE.get(key_of(model), {}))


# ---------------------------------------------------------------------------
# The catchers' reading of a model (tests_records.py)
# ---------------------------------------------------------------------------

def money_looking_fields(model):
    return [f.name for f in model._meta.concrete_fields
            if MONEY_WORDS.search(f.name)
            or isinstance(f, models.DecimalField)]


def secret_looking_fields(model):
    return [f.name for f in model._meta.concrete_fields if SECRET_WORDS.search(f.name)]


def unmarked_money(models_=None):
    out = []
    for model in models_ or listed_models():
        k = key_of(model)
        if k in MONEY or k in NOT_MONEY:
            continue
        if money_looking_fields(model):
            out.append('%s (%s)' % (k, ', '.join(money_looking_fields(model))))
    return out


def unmarked_secrets(models_=None):
    out = []
    for model in models_ or listed_models():
        k = key_of(model)
        for name in secret_looking_fields(model):
            if name in SENSITIVE.get(k, {}) or '%s.%s' % (k, name) in NOT_SENSITIVE:
                continue
            out.append('%s.%s' % (k, name))
    return out


# ---------------------------------------------------------------------------
# Reading a record
# ---------------------------------------------------------------------------

LABEL_FIELDS = ('name', 'title', 'username', 'full_name', 'email', 'slug', 'org_name',
                'team_name', 'tournament_title', 'game_title', 'label', 'code_name')


def label_of(obj):
    try:
        text = str(obj)
    except Exception:  # noqa: BLE001 - a broken __str__ must not break the console
        text = ''
    if not text or text.startswith('%s object' % obj.__class__.__name__):
        for name in LABEL_FIELDS:
            value = getattr(obj, name, None)
            if value:
                text = str(value)
                break
    return (text or '%s %s' % (obj._meta.verbose_name, obj.pk))[:160]


def search_fields(model):
    """The text columns a search reads: names first, at most six."""
    hidden = hidden_fields(model)
    texts = [f for f in model._meta.concrete_fields
             if isinstance(f, (models.CharField, models.TextField, models.EmailField, models.SlugField))
             and not f.choices and f.name not in hidden]
    named = [f.name for f in texts if f.name in LABEL_FIELDS]
    rest = [f.name for f in texts if f.name not in LABEL_FIELDS
            and not isinstance(f, models.TextField)]
    return (named + rest)[:6]


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return json.loads(json.dumps(value, default=str))
    return str(value)


def field_kind(field):
    if isinstance(field, models.ForeignKey) or isinstance(field, models.OneToOneField):
        return 'link'
    if isinstance(field, models.BooleanField):
        return 'boolean'
    if isinstance(field, (models.IntegerField, models.BigIntegerField, models.SmallIntegerField,
                          models.PositiveIntegerField, models.PositiveSmallIntegerField)):
        return 'integer'
    if isinstance(field, (models.DecimalField, models.FloatField)):
        return 'number'
    if isinstance(field, models.DateTimeField):
        return 'datetime'
    if isinstance(field, models.DateField):
        return 'date'
    if isinstance(field, models.TimeField):
        return 'time'
    if isinstance(field, models.JSONField):
        return 'json'
    if isinstance(field, models.FileField):
        return 'file'
    if isinstance(field, models.TextField):
        return 'longtext'
    return 'text'


def editable(model, field):
    """May an admin change this column here?"""
    if is_money(model) or field.primary_key or not field.editable or field.auto_created:
        return False
    if field.name in hidden_fields(model):
        return False
    if getattr(field, 'auto_now', False) or getattr(field, 'auto_now_add', False):
        return False
    if isinstance(field, models.FileField):
        return False      # uploads go through the uploads rules, on their own screens
    return True


def value_of(obj, field):
    model = obj.__class__
    if field.name in hidden_fields(model):
        return {'hidden': True}
    if isinstance(field, (models.ForeignKey, models.OneToOneField)):
        target_pk = getattr(obj, field.attname)
        if target_pk is None:
            return None
        related = field.related_model
        shown = model_for(key_of(related)) is not None
        try:
            label = label_of(getattr(obj, field.name)) if shown else str(target_pk)
        except related.DoesNotExist:
            label = str(target_pk)
        return {'model': key_of(related) if shown else None, 'pk': str(target_pk), 'label': label}
    if isinstance(field, models.FileField):
        file = getattr(obj, field.name)
        return {'file': file.name} if file else None
    return _jsonable(getattr(obj, field.attname))


def describe_field(model, field):
    out = {
        'name': field.name,
        'label': str(field.verbose_name),
        'kind': field_kind(field),
        'editable': editable(model, field),
        'hidden': field.name in hidden_fields(model),
        'null': bool(field.null),
    }
    if field.choices:
        out['choices'] = [[str(k), str(v)] for k, v in field.flatchoices]
    if out['kind'] == 'link':
        out['target'] = key_of(field.related_model)
    return out


def row(obj):
    return {'pk': str(obj.pk), 'label': label_of(obj)}


def detail(obj, limit=10):
    model = obj.__class__
    fields = []
    for field in model._meta.concrete_fields:
        entry = describe_field(model, field)
        entry['value'] = value_of(obj, field)
        fields.append(entry)

    linked = []
    for rel in model._meta.related_objects:
        related = rel.related_model
        if model_for(key_of(related)) is None:
            continue
        accessor = rel.get_accessor_name()
        try:
            if rel.one_to_one:
                target = getattr(obj, accessor)
                items, count = [target], 1
            else:
                manager = getattr(obj, accessor)
                qs = manager.all()
                count = qs.count()
                items = list(qs[:limit])
        except (related.DoesNotExist, ValueError, AttributeError):
            continue
        if not count:
            continue
        linked.append({
            'model': key_of(related),
            'name': str(related._meta.verbose_name_plural),
            'field': rel.field.name if hasattr(rel, 'field') else accessor,
            'count': count,
            'items': [row(i) for i in items],
        })
    for field in model._meta.many_to_many:
        related = field.related_model
        if model_for(key_of(related)) is None:
            continue
        qs = getattr(obj, field.name).all()
        count = qs.count()
        if count:
            linked.append({'model': key_of(related), 'name': str(field.verbose_name),
                           'field': field.name, 'count': count,
                           'items': [row(i) for i in qs[:limit]]})
    return {'fields': fields, 'linked': linked}


# ---------------------------------------------------------------------------
# Editing, with history
# ---------------------------------------------------------------------------

class RecordError(Exception):
    def __init__(self, message, code, field=None, data=None):
        super().__init__(message)
        self.code = code
        self.field = field
        self.data = data or {}


def _parse(field, raw):
    """A value from the console, turned into what the column holds."""
    if raw is None or (raw == '' and field.null):
        return None
    if isinstance(field, (models.ForeignKey, models.OneToOneField)):
        target = field.related_model
        try:
            return target._default_manager.get(pk=raw)
        except (target.DoesNotExist, ValueError, ValidationError):
            raise RecordError('Nothing with that number to link to.', 'LINK_NOT_FOUND', field.name)
    if isinstance(field, models.JSONField):
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except ValueError:
                raise RecordError('That is not valid JSON.', 'INVALID_JSON', field.name)
        return raw
    try:
        return field.to_python(raw)
    except ValidationError:
        raise RecordError('That value does not fit this field.', 'INVALID_VALUE', field.name)


def apply_edit(obj, values, admin, reason, reverts=None):
    """Change some columns of one record. Returns the RecordVersion.

    Every changed column is written to the version as [before, after] in the
    console's own JSON form, so a revert can put the before back exactly.
    Validated with the model's own field rules before saving.
    """
    from .models import AdminAction, RecordVersion
    model = obj.__class__
    if is_money(model):
        raise RecordError('Money records are view only. Correct them with a new entry.',
                          'MONEY_IS_VIEW_ONLY')
    if not isinstance(values, dict) or not values:
        raise RecordError('Say what to change.', 'NOTHING_TO_CHANGE')
    reason = str(reason or '').strip()
    if not reason:
        raise RecordError('Say why this is being changed.', 'REASON_REQUIRED')

    by_name = {f.name: f for f in model._meta.concrete_fields}
    changes = {}
    with transaction.atomic():
        locked = model._base_manager.select_for_update().get(pk=obj.pk)
        for name, raw in values.items():
            field = by_name.get(name)
            if field is None:
                raise RecordError('There is no field called %s.' % name, 'UNKNOWN_FIELD', name)
            if not editable(model, field):
                raise RecordError('%s cannot be changed here.' % name, 'NOT_EDITABLE', name)
            before = value_of(locked, field)
            new = _parse(field, raw)
            setattr(locked, field.name, new)
            after = value_of(locked, field)
            if before != after:
                changes[name] = [before, after]
        if not changes:
            raise RecordError('Nothing is different.', 'NOTHING_TO_CHANGE')
        names = list(changes)
        attnames = [by_name[n].attname for n in names]
        try:
            locked.clean_fields(exclude=[f.name for f in model._meta.concrete_fields
                                         if f.name not in names])
        except ValidationError as exc:
            first = next(iter(exc.message_dict.items()), (None, ['That value does not fit.']))
            raise RecordError(str(first[1][0]), 'INVALID_VALUE', first[0])
        try:
            locked.save(update_fields=attnames)
        except IntegrityError:
            raise RecordError('Another record already has that value.', 'NOT_UNIQUE')
        version = RecordVersion.objects.create(
            model_label=key_of(model), object_pk=str(obj.pk), label=label_of(locked)[:160],
            changes=changes, reason=reason[:500], changed_by=admin, reverts=reverts)
        AdminAction.objects.create(
            admin=admin, action_type='record_revert' if reverts else 'record_edit',
            target_model=key_of(model)[:50], target_id=str(obj.pk)[:100], reason=reason,
            metadata={'fields': names, 'version': version.pk})
    return version


def revert(version, admin, reason=''):
    """Put the 'before' of one version back, as a new version."""
    model = model_for(version.model_label)
    if model is None:
        raise RecordError('That kind of record is no longer listed.', 'UNKNOWN_MODEL')
    obj = model._base_manager.filter(pk=version.object_pk).first()
    if obj is None:
        raise RecordError('That record no longer exists.', 'NOT_FOUND')
    values = {}
    for name, (before, _after) in version.changes.items():
        if isinstance(before, dict) and 'pk' in before:
            values[name] = before['pk']
        else:
            values[name] = before
    return apply_edit(obj, values, admin,
                      reason or 'Undo of change %s' % version.pk, reverts=version)


# ---------------------------------------------------------------------------
# Deleting into the bin, restoring, purging
# ---------------------------------------------------------------------------

def _collector(obj):
    from django.contrib.admin.utils import NestedObjects
    collector = NestedObjects(using=obj._state.db or 'default')
    collector.collect([obj])
    return collector


def delete_preview(obj):
    """What deleting this would take with it, and whether it may be deleted."""
    model = obj.__class__
    if is_money(model):
        return {'allowed': False, 'code': 'MONEY_IS_VIEW_ONLY', 'counts': {}, 'money': [key_of(model)]}
    if _is_soft(model):
        # The organiser's own delete decides this, so the console answers the
        # same: somebody paid, so cancel (which refunds) before deleting.
        paid, unpaid = _soft_counts(obj)
        if paid:
            return {'allowed': False, 'code': 'PAID_ENTRANTS', 'soft': True,
                    'counts': {key_of(model): 1}, 'money': [], 'paid': paid, 'unpaid': unpaid}
        return {'allowed': True, 'code': None, 'soft': True, 'counts': {key_of(model): 1},
                'money': [], 'paid': 0, 'unpaid': unpaid}
    collector = _collector(obj)
    if collector.protected:
        names = sorted({key_of(p.__class__) for p in collector.protected})
        return {'allowed': False, 'code': 'PROTECTED', 'counts': {}, 'money': [], 'protected': names}
    counts = {}
    for m, instances in collector.data.items():
        counts[key_of(m)] = counts.get(key_of(m), 0) + len(instances)
    money = sorted(k for k in counts if k in MONEY)
    return {'allowed': not money, 'code': 'CASCADE_TOUCHES_MONEY' if money else None,
            'counts': counts, 'money': money}


def _is_soft(model):
    """Tournaments and events delete softly already (vent_auth/softdelete.py)."""
    names = {f.name for f in model._meta.concrete_fields}
    return {'deleted_at', 'deleted_by'} <= names and hasattr(model, 'all_objects')


def _soft_counts(obj):
    """(paid, unpaid) entrants, from the same counter the organiser's delete uses."""
    from importlib import import_module
    counters = {
        'vent_event.event': 'vent_event.views_delete',
        'vent_tournament.tournament': 'vent_tournament.views_delete',
    }
    module = counters.get(key_of(obj.__class__))
    if module is None:
        return 0, 0
    return import_module(module)._counts(obj)


def move_to_bin(obj, admin, reason):
    """Delete a record and everything that goes with it, keeping all of it.

    The rows are written to a RecordBin entry first, in one transaction with the
    delete, so either both happen or neither does. Columns other rows set to
    NULL on this delete are remembered and put back on restore.
    """
    from .models import AdminAction, RecordBin
    model = obj.__class__
    reason = str(reason or '').strip()
    if not reason:
        raise RecordError('Say why this is being deleted.', 'REASON_REQUIRED')
    preview = delete_preview(obj)
    if not preview['allowed']:
        raise RecordError('This cannot be deleted here.', preview['code'], data=preview)

    with transaction.atomic():
        if preview.get('soft'):
            softdelete.mark_deleted(obj, by=admin, reason=reason)
            snapshot = {'soft': True}
        else:
            collector = _collector(obj)
            collector.sort()
            objects = []
            for m, instances in collector.data.items():
                objects.extend(json.loads(serializers.serialize('json', list(instances))))
            nulls = []
            for (field, value), batches in collector.field_updates.items():
                pks = []
                for batch in batches:
                    if hasattr(batch, 'values_list'):
                        pks.extend(str(p) for p in batch.values_list('pk', flat=True))
                    else:
                        pks.extend(str(i.pk) for i in batch)
                originals = {}
                for pk in pks:
                    row_ = field.model._base_manager.filter(pk=pk).values_list(field.attname, flat=True).first()
                    if row_ is not None:
                        originals[pk] = str(row_)
                if originals:
                    nulls.append({'model': key_of(field.model), 'field': field.attname, 'rows': originals})
            snapshot = {'objects': objects, 'nulls': nulls}
            obj.delete()
        entry = RecordBin.objects.create(
            model_label=key_of(model), object_pk=str(obj.pk), label=label_of(obj)[:160],
            counts=preview['counts'], snapshot=snapshot, reason=reason[:500],
            deleted_by=admin, purge_after=timezone.now() + timedelta(days=BIN_DAYS))
        AdminAction.objects.create(
            admin=admin, action_type='record_delete', target_model=key_of(model)[:50],
            target_id=str(obj.pk)[:100], reason=reason,
            metadata={'bin': entry.pk, 'counts': preview['counts']})
    return entry


def restore(entry, admin):
    """Put a deleted record, and what went with it, back exactly as it was."""
    from .models import AdminAction
    if entry.restored_at:
        raise RecordError('That has already been restored.', 'ALREADY_RESTORED')
    model = apps.get_model(entry.model_label)
    with transaction.atomic():
        if entry.snapshot.get('soft'):
            obj = model._base_manager.filter(pk=entry.object_pk).first()
            if obj is None:
                raise RecordError('That record no longer exists.', 'NOT_FOUND')
            softdelete.restore(obj)
        else:
            objects = entry.snapshot.get('objects') or []
            # Deleted children first, so restored parents first.
            try:
                for item in reversed(list(serializers.deserialize('json', json.dumps(objects)))):
                    item.save(save_m2m=False, force_insert=True)
            except IntegrityError:
                raise RecordError('Something now uses the same number or name, so it cannot go back as it was.',
                                  'RESTORE_CONFLICT')
            for group in entry.snapshot.get('nulls') or []:
                target = apps.get_model(group['model'])
                for pk, original in group['rows'].items():
                    target._base_manager.filter(pk=pk, **{group['field']: None}).update(
                        **{group['field']: original})
        entry.restored_at = timezone.now()
        entry.restored_by = admin
        entry.save(update_fields=['restored_at', 'restored_by'])
        AdminAction.objects.create(
            admin=admin, action_type='record_restore', target_model=entry.model_label[:50],
            target_id=entry.object_pk[:100], metadata={'bin': entry.pk})
    return entry


def purge(entry, admin):
    """Throw a bin entry away for good. Its rows were already deleted."""
    from .models import AdminAction
    AdminAction.objects.create(
        admin=admin, action_type='record_purge', target_model=entry.model_label[:50],
        target_id=entry.object_pk[:100], metadata={'bin': entry.pk, 'label': entry.label})
    entry.delete()


def bin_row(entry):
    return {
        'id': entry.pk,
        'model': entry.model_label,
        'pk': entry.object_pk,
        'label': entry.label,
        'counts': entry.counts,
        'reason': entry.reason,
        'deleted_by': entry.deleted_by.username if entry.deleted_by_id else None,
        'deleted_at': entry.deleted_at.isoformat(),
        'purge_after': entry.purge_after.isoformat(),
        'restored_at': entry.restored_at.isoformat() if entry.restored_at else None,
        'soft': bool(entry.snapshot.get('soft')),
    }


def version_row(version):
    return {
        'id': version.pk,
        'changes': version.changes,
        'reason': version.reason,
        'changed_by': version.changed_by.username if version.changed_by_id else None,
        'changed_at': version.changed_at.isoformat(),
        'reverts': version.reverts_id,
    }


# ---------------------------------------------------------------------------
# Money corrections
# ---------------------------------------------------------------------------

def wallet_behind(obj):
    """The wallet a money record belongs to, for a correction; None if none."""
    from .models import OrgWallet, TeamWallet, Transaction, UserWallet
    if isinstance(obj, (UserWallet, TeamWallet, OrgWallet)):
        return obj
    if isinstance(obj, Transaction):
        return obj.wallet or obj.team_wallet or obj.org_wallet
    return None
