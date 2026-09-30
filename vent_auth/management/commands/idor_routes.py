"""Every address that takes an id, with a real record in it (owner rule R88, inbox 398).

    python manage.py idor_routes > routes.json

Walks Django's own URL tree (so include() prefixes are right) and, for each
address with an id or slug in it, fills every parameter with a real record
that user A (walk_organiser, tools/walk_event.py --setup) owns, or failing that
one that user B (walk_buyer_ada) does not. tools/idor-probe.py then tries each
address as B and as a visitor with no session, by every method the view takes.

A parameter with no record to put in it is filled with a word that names
nothing and listed under "missing", so an address is never silently skipped.
"""
import json
import re

from django.apps import apps
from django.core.management.base import BaseCommand
from django.urls import URLPattern, URLResolver, get_resolver

ID_NAME = re.compile(r'(^|_)(id|slug|pk|uuid|handle|ref)$|^(id|pk|slug|uuid|handle|ref)$')
CAPABILITY = re.compile(r'token|signature|sig$|hmac|^uidb\d+$', re.I)
SKIP_PREFIXES = ('admin/', 'auth/accounts/', 'auth/dj-rest-auth/')

# Parameter name -> (app.Model, the field the address is read by). A value from
# a row under A's tournament or event is preferred; see _pick.
MODELS = {
    'match_id': ('vent_tournament.BracketMatch', 'pk'),
    'stage_id': ('vent_tournament.TournamentStage', 'pk'),
    'session_id': ('vent_tournament.BroadcastSession', 'pk'),
    'overlay_id': ('vent_tournament.TournamentOverlay', 'pk'),
    'layer_id': ('vent_tournament.OverlayLayer', 'pk'),
    'team_id': ('vent_auth.Teams', 'pk'),
    'partner_id': ('vent_partners.Partner', 'pk'),
    'key_id': ('vent_partners.PartnerApiKey', 'pk'),
    'vendor_id': ('vent_event.Vendor', 'slug'),
    'scrim_id': ('vent_auth.Scrim', 'pk'),
    'game_id': ('vent_auth.Games', 'pk'),
    'map_id': ('vent_tournament.BRMap', 'pk'),
    'squad_id': ('vent_tournament.TournamentSquad', 'pk'),
    'job_id': ('vent_tournament.BROcrJob', 'pk'),
    'topic_id': ('vent_auth.ClubTopic', 'pk'),
    'club_ref': ('vent_auth.Club', 'slug'),
    'club_id': ('vent_auth.Club', 'pk'),
    'message_id': ('vent_auth.ClubMessage', 'pk'),
    'post_id': ('vent_auth.Post', 'pk'),
    'thread_id': ('vent_auth.Thread', 'pk'),
    'reply_id': ('vent_auth.ThreadReply', 'pk'),
    'withdrawal_id': ('vent_auth.WithdrawalRequest', 'pk'),
    'kyc_id': ('vent_auth.KYCDocument', 'pk'),
    'document_id': ('vent_auth.KYCDocument', 'pk'),
    'notification_id': ('vent_auth.Notification', 'pk'),
    'card_id': ('vent_auth.SavedCard', 'pk'),
    'day_id': ('vent_tournament.RunSheetDay', 'pk'),
    'item_id': ('vent_tournament.RunSheetItem', 'pk'),
    'asset_id': ('vent_tournament.StudioAsset', 'pk'),
    'lobby_id': ('vent_tournament.BRLobby', 'pk'),
    'link_id': ('vent_event.ShortLink', 'pk'),
    'invitation_id': ('vent_tournament.TournamentInvitation', 'pk'),
    'tie_id': ('vent_tournament.TieFixture', 'pk'),
    'slot_id': ('vent_event.VendorSlot', 'pk'),
    'hold_id': ('vent_event.TicketHold', 'pk'),
    'tier_id': ('vent_event.TicketTier', 'pk'),
    'poll_id': ('vent_event.EventPoll', 'pk'),
    'invite_id': ('vent_auth.TeamInvite', 'pk'),
    'request_id': ('vent_auth.OrgJoinRequest', 'pk'),
    'conversation_id': ('vent_auth.Conversation', 'pk'),
    'report_id': ('vent_auth.UserReport', 'pk'),
    'mode_id': ('vent_auth.GameMode', 'pk'),
    'series_id': ('vent_auth.GameSeries', 'pk'),
    'dispute_id': ('vent_tournament.TournamentDispute', 'pk'),
    'hook_id': ('vent_auth.DiscordWebhook', 'pk'),
    'server_id': ('vent_auth.DiscordServer', 'pk'),
    'requirement_id': ('vent_tournament.EntryRequirement', 'pk'),
    'submission_id': ('vent_tournament.EntrySubmission', 'pk'),
    'reminder_id': ('vent_tournament.ScheduledReminder', 'pk'),
    'product_id': ('vent_event.VendorProduct', 'pk'),
    'sponsor_id': ('vent_event.Sponsor', 'pk'),
    'ping_id': ('vent_event.EventPing', 'pk'),
    'referral_id': ('vent_event.EventReferral', 'pk'),
    'promo_id': ('vent_event.EventPromo', 'pk'),
    'manager_id': ('vent_event.EventManager', 'pk'),
    'bid_id': ('vent_marketplace.Bid', 'pk'),
    'row_id': ('vent_marketplace.Wishlist', 'pk'),
    'device_id': ('vent_auth.LoginEvent', 'pk'),
    'org_ref': ('vent_auth.Organization', 'slug'),
    'team_ref': ('vent_auth.Teams', 'slug'),
    'org_id': ('vent_auth.Organization', 'slug'),
}


def _routes():
    out = []

    def walk(patterns, prefix=''):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern):
                full = prefix + str(p.pattern)
                params = re.findall(r'<(?:\w+:)?(\w+)>', full)
                if not any(ID_NAME.search(x) for x in params) or any(CAPABILITY.search(x) for x in params):
                    continue
                if full.startswith(SKIP_PREFIXES):
                    continue
                cls = getattr(p.callback, 'cls', None)
                methods = [m.upper() for m in ('get', 'post', 'put', 'patch', 'delete')
                           if cls is not None and hasattr(cls, m)] or ['GET']
                out.append({'route': full, 'params': params, 'methods': methods,
                            'view': '%s.%s' % (p.callback.__module__, p.callback.__name__)})
    walk(get_resolver().url_patterns)
    return out


class Command(BaseCommand):
    help = 'Every id-taking address with real records in it, as JSON (R88).'

    def handle(self, *args, **options):
        Users = apps.get_model('vent_auth', 'Users')
        a = Users.objects.get(username='walk_organiser')
        b = Users.objects.get(username='walk_buyer_ada')
        Tournament = apps.get_model('vent_tournament', 'Tournament')
        Event = apps.get_model('vent_event', 'Event')
        tournament = Tournament.objects.filter(tournament_creator=a, slug='walk-cup-fee').first() \
            or Tournament.objects.filter(tournament_creator=a).first()
        event = Event.objects.filter(creator=a).exclude(slug__startswith='walk-org').first() \
            or Event.objects.filter(creator=a).first()

        def pick(name):
            label, field = MODELS[name]
            model = apps.get_model(label)
            names = {f.name for f in model._meta.get_fields()}
            qs = model.objects.all()
            for owner_filter in (('tournament', tournament), ('event', event)):
                if owner_filter[0] in names and qs.filter(**{owner_filter[0]: owner_filter[1]}).exists():
                    qs = qs.filter(**{owner_filter[0]: owner_filter[1]})
                    break
            for owned in ('user', 'owner', 'creator', 'buyer', 'bidder'):
                if owned in names:
                    qs = qs.exclude(**{owned: b})
            row = qs.order_by('pk').first()
            if row is None:
                return None
            value = getattr(row, field, None) if field != 'pk' else row.pk
            return str(value if value not in (None, '') else row.pk)

        fixed = {
            'tournament_id': tournament.slug,
            'tournament_ref': tournament.slug,
            'event_id': event.slug,
            'event_ref': event.slug,
            'user_id': str(a.pk),
            'username': a.username,
            'element_kind': 'scorebar',
            'role': 'full',
            'index': '0',
            'provider': 'discord',
        }
        routes, missing = [], set()
        for r in _routes():
            values = {}
            for p in r['params']:
                if p in fixed:
                    values[p] = fixed[p]
                elif p == 'kind':
                    values[p] = ('user' if 'follow' in r['route'] else
                                 'tournament' if any(w in r['route'] for w in ('discord', 'hooks'))
                                 else 'scorebar')
                elif p in ('ref', 'slug'):
                    # A follow names a user or a team; everything else by `ref` is the tournament.
                    values[p] = a.username if 'follow' in r['route'] else tournament.slug
                elif p == 'code':
                    values[p] = 'R88MISSING'
                elif p in MODELS:
                    try:
                        values[p] = pick(p)
                    except LookupError:
                        values[p] = None
                else:
                    values[p] = None
                if values[p] is None:
                    missing.add(p)
                    values[p] = 'r88-none'
            path = '/' + re.sub(r'<(?:\w+:)?(\w+)>', lambda m: values[m.group(1)], r['route'])
            routes.append({**r, 'path': path, 'values': values})
        self.stdout.write(json.dumps({'a': a.username, 'b': b.username,
                                      'tournament': tournament.slug, 'event': event.slug,
                                      'missing': sorted(missing), 'routes': routes}, indent=1))
