"""Fill an empty database with a platform that looks lived-in.

Why this exists: a fresh V-ENT install has four accounts and nothing else, so
every listing renders its empty state and no flow past "there is nothing here"
can be walked. That makes it impossible to tell a broken page from an unpopulated
one. This command creates enough real-shaped data - tournaments at every point in
their lifecycle, brackets with played matches, an event that pays for a
tournament entry, wallet history, and admin queues with something in them - that
every screen can be exercised end to end.

Everything it writes hangs off accounts whose username starts with `demo_` and
whose email ends `@seed.v-ent.co`, an address that does not resolve, so no seeded
row can ever send mail to a real person. `--wipe` deletes those accounts and
cascades the rest away, which is why nothing here is ever attached to a real
user's record.

    python manage.py seed_demo            # create (idempotent, skips if present)
    python manage.py seed_demo --wipe     # remove everything it created
    python manage.py seed_demo --reset    # wipe then create

Brackets are built by calling the real bracket service and then saving match
scores one at a time, so the advancement signal does the advancing. Seeding
through the same code path the product uses means the data is only valid if the
product is.
"""
import io
import random
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw

from vent_auth.models import (
    Games, KYCDocument, TeamMembers, TeamProfile, Teams, Transaction,
    UserProfile, Users, WithdrawalRequest,
)
from vent_auth.views_helpers import (
    _load_avatar_font, create_default_profile_picture, get_or_create_user_wallet,
)
from vent_event.models import Event, EventTournamentLink, Ticket, TicketTier
from vent_tournament.models import (
    BracketMatch, Tournament, TournamentDispute, TournamentPrizeDistribution,
    TournamentRegistration,
)
from vent_tournament.services import bracket as bracket_service

DEMO_PREFIX = 'demo_'
DEMO_DOMAIN = '@seed.v-ent.co'
DEMO_PASSWORD = 'VentDemo2026!'
DEMO_PIN = '2468'

# Flat brand fills. No gradient, no glow - the same rule the UI follows.
FILLS = {
    'red': (146, 32, 36),
    'ink': (33, 34, 37),
    'slate': (44, 46, 54),
    'moss': (38, 56, 40),
    'clay': (74, 52, 36),
    'steel': (48, 49, 54),
}

PLAYERS = [
    ('Temi Adeyemi', 'temi'), ('Chidi Okeke', 'chidi'), ('Amara Nwosu', 'amara'),
    ('Yusuf Bello', 'yusuf'), ('Zainab Musa', 'zainab'), ('Emeka Obi', 'emeka'),
    ('Funke Ajayi', 'funke'), ('Tobi Balogun', 'tobi'), ('Ifeoma Eze', 'ifeoma'),
    ('Sadiq Aliyu', 'sadiq'), ('Bisi Adeleke', 'bisi'), ('Kelechi Uche', 'kelechi'),
    ('Ngozi Anyanwu', 'ngozi'), ('Segun Ogun', 'segun'), ('Halima Sani', 'halima'),
    ('Uche Nnamdi', 'uche'),
]

TEAMS = [
    ('Lagos Rangers', 'Naija Free Fire Weekly regulars out of Yaba.'),
    ('Abuja Titans', 'Capital city roster. Plays every Vermillion open.'),
    ('Port Harcourt Kings', 'Garden City squad, formed 2024.'),
    ('Kano Falcons', 'Northern circuit team with a scrim-heavy schedule.'),
]


def flat_image(width, height, fill, label='', sub=''):
    """A flat-fill JPEG with the name set in it. Placeholder, never pretending."""
    img = Image.new('RGB', (width, height), FILLS[fill])
    if label:
        draw = ImageDraw.Draw(img)
        font = _load_avatar_font(size=max(16, int(height * 0.13)))
        box = draw.textbbox((0, 0), label, font=font)
        draw.text(
            ((width - (box[2] - box[0])) / 2, (height - (box[3] - box[1])) / 2 - (14 if sub else 0)),
            label, font=font, fill=(236, 236, 240),
        )
        if sub:
            small = _load_avatar_font(size=max(12, int(height * 0.07)))
            sbox = draw.textbbox((0, 0), sub, font=small)
            draw.text(
                ((width - (sbox[2] - sbox[0])) / 2, height / 2 + (box[3] - box[1]) / 2 + 4),
                sub, font=small, fill=(150, 150, 158),
            )
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=86)
    return ContentFile(buf.getvalue())


class Command(BaseCommand):
    help = 'Seed (or wipe) a full set of demo tournaments, events, teams and wallet history.'

    def add_arguments(self, parser):
        parser.add_argument('--wipe', action='store_true', help='Delete every demo_* account and everything hanging off it.')
        parser.add_argument('--reset', action='store_true', help='Wipe, then seed again.')

    def handle(self, *args, **options):
        random.seed(20260818)  # stable output across runs
        if options['wipe'] or options['reset']:
            self.wipe()
        if options['wipe'] and not options['reset']:
            return
        with transaction.atomic():
            self.seed()

    # ---------------------------------------------------------------- wipe

    def wipe(self):
        users = Users.objects.filter(username__startswith=DEMO_PREFIX)
        count = users.count()
        # Tournaments and events use SET_NULL on their creator, so deleting the
        # account would leave them behind as orphans. Take them out explicitly.
        Tournament.objects.filter(tournament_creator__in=users).delete()
        # Ticket -> TicketTier is PROTECT, so a tier cannot go while a ticket
        # points at it. Sold tickets come out first, then the event.
        events = Event.objects.filter(creator__in=users)
        Ticket.objects.filter(event__in=events).delete()
        events.delete()
        Teams.objects.filter(team_owner__in=users).delete()
        users.delete()
        self.stdout.write(self.style.WARNING(f'wiped {count} demo accounts and their data'))

    # ---------------------------------------------------------------- seed

    def seed(self):
        now = timezone.now()
        games = {g.game_title: g for g in Games.objects.all()}
        if not games:
            self.stdout.write(self.style.ERROR('No games in the database. Seed games first.'))
            return

        ff = games.get('Free Fire')
        fc = games.get('EA FC 24')
        codm = games.get('Call of Duty: Mobile')
        pubg = games.get('PUBG Mobile')

        self.give_games_logos(games)

        organizer = self.make_user('Vermillion Events', 'organizer', coins=0)
        players = [self.make_user(name, handle, coins=random.choice([1200, 2400, 5000, 800]))
                   for name, handle in PLAYERS]
        star = players[0]  # demo_temi - the account meant for clicking through
        self.stdout.write(f'accounts: organizer + {len(players)} players')

        teams = self.make_teams(players, ff)
        self.stdout.write(f'teams: {len(teams)}')

        # 1. Registration open, free, individual, half full.
        weekly = self.make_tournament(
            organizer, ff, 'Naija Free Fire Weekly #12',
            'Open weekly ladder. Solo queue, best of one until the semi finals.',
            starts=now + timedelta(days=5), hours=4, fill='red',
            entry='Free', price=0, access='individual', status='registration_open',
            prizes=[(1, 12000), (2, 6000), (3, 2000)], max_teams=32,
        )
        self.register(weekly, users=players[:11], paid=False)

        # 2. Registration open, paid entry, winner takes all.
        showdown = self.make_tournament(
            organizer, fc, 'Vermillion EA FC Showdown',
            'Paid entry, single elimination, two legs from the quarter finals.',
            starts=now + timedelta(days=12), hours=6, fill='slate',
            entry='Paid', price=500, access='individual', status='registration_open',
            prizes=[(1, 30000)], max_teams=16,
        )
        self.register(showdown, users=players[1:7], paid=True)

        # 3. Live right now, team bracket, round one played.
        clash = self.make_tournament(
            organizer, codm, 'Lagos CODM Clash',
            'Four-team invitational. Search and Destroy, first to six rounds.',
            starts=now - timedelta(hours=2), hours=5, fill='ink',
            entry='Paid', price=250, access='team', status='live',
            prizes=[(1, 20000), (2, 8000)], max_teams=4, team_size=4,
        )
        self.register(clash, teams=teams, paid=True)
        clash_matches = self.build_bracket(clash, organizer)
        self.play(clash_matches, rounds=[1])
        live_match = BracketMatch.objects.filter(
            tournament=clash, round_number=2).order_by('match_number').first()
        if live_match:
            live_match.status = 'in_progress'
            live_match.save(update_fields=['status'])

        # 4. Finished, full bracket, winner decided.
        openup = self.make_tournament(
            organizer, pubg, 'PUBG Mobile Naija Open',
            'Sixteen players, single elimination. Ran over one Saturday in July.',
            starts=now - timedelta(days=14), hours=8, fill='moss',
            entry='Paid', price=300, access='individual', status='completed',
            prizes=[(1, 25000), (2, 12000), (3, 5000)], max_teams=16,
        )
        self.register(openup, users=players[:16], paid=True)
        open_matches = self.build_bracket(openup, organizer)
        self.play(open_matches, rounds=[1, 2, 3, 4])
        openup.status = 'completed'
        openup.completed_at = now - timedelta(days=14) + timedelta(hours=8)
        openup.save(update_fields=['status', 'completed_at'])

        # 5. A draft, so the drafts screen has something in it.
        self.make_tournament(
            organizer, ff, 'Anime Fighters Invitational',
            'Draft. Format still being decided with the venue.',
            starts=now + timedelta(days=30), hours=6, fill='clay',
            entry='Free', price=0, access='individual', status='draft',
            prizes=[], max_teams=8, draft=True,
        )

        # 6. Private, so the "hidden from the listing, reachable by link" rule has
        #    something to prove itself against.
        scrim = self.make_tournament(
            organizer, fc, 'V-ENT Private Scrim', 'Invite only practice bracket.',
            starts=now + timedelta(days=3), hours=3, fill='steel',
            entry='Free', price=0, access='individual', status='registration_open',
            prizes=[], max_teams=8, visibility='private',
        )
        self.register(scrim, users=players[2:6], paid=False)

        self.stdout.write('tournaments: 6 (open, paid, live, completed, draft, protected)')

        events = self.make_events(organizer, now, ff, codm)
        # The event pays the tournament entry. This is the linking feature.
        EventTournamentLink.objects.get_or_create(
            event=events[0], tournament=weekly,
            defaults={'shared_ticketing': True, 'linked_by': organizer},
        )
        EventTournamentLink.objects.get_or_create(
            event=events[0], tournament=showdown,
            defaults={'shared_ticketing': False, 'linked_by': organizer},
        )
        self.stdout.write(f'events: {len(events)} (1 carries 2 tournaments)')

        self.make_tickets(events[0], players[:5])
        self.make_wallet_history(star, weekly, openup)
        self.make_admin_queues(players)
        self.make_dispute(clash, players[1])

        self.stdout.write(self.style.SUCCESS(
            f'\nSeeded. Sign in as {star.username} / {DEMO_PASSWORD} '
            f'({star.email}) to walk an account with history.'
        ))

    # ------------------------------------------------------------- people

    def make_user(self, full_name, handle, coins=0):
        username = f'{DEMO_PREFIX}{handle}'
        user = Users.objects.filter(username=username).first()
        if user is None:
            user = Users.objects.create(
                username=username, full_name=full_name,
                email=f'{handle}{DEMO_DOMAIN}', password=make_password(DEMO_PASSWORD),
                country='Nigeria', state='Lagos', is_active=True,
            )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if not profile.profile_picture:
            profile.profile_picture.save(
                f'{username}_profile.png', create_default_profile_picture(full_name), save=False)
            profile.description = f'{full_name.split()[0]} plays out of Lagos.'
            profile.save()
        wallet = get_or_create_user_wallet(user)
        fields = []
        if coins and wallet.wallet_balance == 0:
            wallet.wallet_balance = coins
            fields.append('wallet_balance')
        # Paid registration, sending coins and buying a ticket all stop at the
        # PIN prompt. Without one the demo account cannot walk any flow that
        # moves money, which is most of what is worth testing.
        if not wallet.pin_hash:
            wallet.pin_hash = make_password(DEMO_PIN)
            fields.append('pin_hash')
        if fields:
            wallet.save(update_fields=fields)
        return user

    def make_teams(self, players, game):
        made = []
        for index, (name, blurb) in enumerate(TEAMS):
            roster = players[index * 4:index * 4 + 4]
            if len(roster) < 4:
                break
            owner = roster[0]
            team = Teams.objects.filter(team_name=name).first()
            if team is None:
                team = Teams(
                    team_name=name, game=game, description=blurb,
                    team_creator=owner, team_owner=owner,
                    penalty_points=0, number_of_members=len(roster),
                )
                team.team_logo.save(f'{name}_logo.jpg',
                                    flat_image(320, 320, 'ink', name.split()[0]), save=False)
                team.team_banner.save(f'{name}_banner.jpg',
                                      flat_image(1200, 400, 'slate'), save=False)
                team.save()
                TeamProfile.objects.get_or_create(team=team)
                for slot, member in enumerate(roster):
                    TeamMembers.objects.get_or_create(
                        team=team, user=member, defaults={'is_captain': slot == 0})
            made.append(team)
        return made

    # -------------------------------------------------------- tournaments

    def make_tournament(self, creator, game, title, description, *, starts, hours,
                        fill, entry, price, access, status, prizes, max_teams,
                        team_size=1, draft=False, visibility='public'):
        existing = Tournament.objects.filter(tournament_title=title).first()
        if existing:
            return existing
        tournament = Tournament(
            tournament_title=title, tournament_game=game, tournament_creator=creator,
            tournament_description=description,
            tournament_rules=(
                'One account per player. Screenshots settle every disputed score. '
                'Miss check-in and your slot goes to a substitute.'
            ),
            bracket_type='Single Elimination',
            start_date_and_time=starts, end_date_and_time=starts + timedelta(hours=hours),
            tournament_visibility=visibility, tournament_type='online',
            team_size=team_size, player_size=max_teams,
            min_number_of_teams=2, max_number_of_teams=max_teams,
            prize_type='distributed' if len(prizes) > 1 else ('winner_takes_all' if prizes else 'no_prize'),
            tournament_access=access, entry_fee=entry, entry_fee_price=price,
            is_draft=draft, status=status,
            score_confirmation_mode='organizer_only',
        )
        short = title.split()[0]
        tournament.tournament_logo.save(f'{short}_logo.jpg',
                                        flat_image(400, 400, fill, short), save=False)
        # No text in the banner: the card draws the title over it, and baking the
        # name into the image just prints it twice.
        tournament.tournament_banner.save(
            f'{short}_banner.jpg', flat_image(1440, 480, fill), save=False)
        tournament.save()
        for position, prize in prizes:
            TournamentPrizeDistribution.objects.create(
                tournament=tournament, position=position, prize=prize, extras='')
        return tournament

    def register(self, tournament, *, users=None, teams=None, paid=False):
        for index, entrant in enumerate(users or []):
            TournamentRegistration.objects.get_or_create(
                tournament=tournament, user=entrant,
                defaults={'status': 'confirmed', 'entry_fee_paid': paid,
                          'payment_reference': f'DEMO-{tournament.pk}-{index}' if paid else '',
                          'seed': index + 1},
            )
        for index, team in enumerate(teams or []):
            TournamentRegistration.objects.get_or_create(
                tournament=tournament, team=team,
                defaults={'status': 'confirmed', 'entry_fee_paid': paid,
                          'payment_reference': f'DEMO-{tournament.pk}-T{index}' if paid else '',
                          'seed': index + 1},
            )

    def build_bracket(self, tournament, organizer):
        if BracketMatch.objects.filter(tournament=tournament).exists():
            return list(BracketMatch.objects.filter(tournament=tournament))
        bracket_service.generate(tournament, organizer, seed_strategy='ranked')
        return list(BracketMatch.objects.filter(tournament=tournament))

    def play(self, matches, rounds):
        """Score the given rounds one match at a time so advancement cascades."""
        for round_number in rounds:
            live = BracketMatch.objects.filter(
                tournament=matches[0].tournament, round_number=round_number,
            ).order_by('match_number')
            for match in live:
                if match.status in ('completed', 'bye') or not (match.participant_1 and match.participant_2):
                    continue
                p1_wins = random.random() < 0.5
                match.score_p1 = 6 if p1_wins else random.choice([2, 3, 4])
                match.score_p2 = random.choice([2, 3, 4]) if p1_wins else 6
                match.winner = match.participant_1 if p1_wins else match.participant_2
                match.status = 'completed'
                match.completed_at = timezone.now()
                match.save()

    # ------------------------------------------------------------- events

    def make_events(self, creator, now, ff, codm):
        specs = [
            dict(name='V-ENT Lagos Meetup 2026', game=ff, kind='physical', category='esports',
                 desc='A full day of live brackets, an artist alley, and the Free Fire weekly final '
                      'played on stage. Doors at 10am.',
                 start=now + timedelta(days=21), hours=9, fill='red',
                 location='Landmark Centre, Victoria Island, Lagos', capacity=600,
                 tiers=[('General Admission', 5000, 500), ('VIP', 20000, 80)]),
            dict(name='Anime Night Lagos', game=None, kind='physical', category='anime',
                 desc='Screening, cosplay contest and a manga swap. Bring something to trade.',
                 start=now + timedelta(days=40), hours=6, fill='clay',
                 location='Alliance Francaise, Ikoyi, Lagos', capacity=250,
                 tiers=[('Entry', 3000, 250)]),
            dict(name='CODM Online Cup Stream', game=codm, kind='virtual', category='esports',
                 desc='The stream that carried the June CODM cup. Replay is on the channel.',
                 start=now - timedelta(days=30), hours=5, fill='steel',
                 location=None, capacity=None, tiers=[]),
        ]
        made = []
        for spec in specs:
            event = Event.objects.filter(name=spec['name']).first()
            if event is None:
                start = spec['start']
                end = start + timedelta(hours=spec['hours'])
                event = Event(
                    name=spec['name'], game=spec['game'], creator=creator,
                    event_type=spec['kind'], category=spec['category'], desc=spec['desc'],
                    entry_fee=0, start_date=start, end_date=end,
                    reg_start_date=start - timedelta(days=20), reg_end_date=start - timedelta(hours=6),
                    event_date=start.date(), start_time=start.time(), end_time=end.time(),
                    location=spec['location'], capacity=spec['capacity'],
                    event_link='https://twitch.tv/vent' if spec['kind'] == 'virtual' else None,
                    # is_active means "not cancelled", not "not finished" -
                    # the serializer derives upcoming/live/ended from the dates.
                    # Setting it from the start date hid every past event.
                    is_active=True,
                    is_featured=spec['name'].startswith('V-ENT'),
                )
                short = spec['name'].split()[0]
                event.logo.save(f'{short}_logo.jpg',
                                flat_image(400, 400, spec['fill'], short), save=False)
                event.banner.save(f'{short}_banner.jpg',
                                  flat_image(1440, 480, spec['fill']), save=False)
                event.save()
                for tier_name, price, quantity in spec['tiers']:
                    TicketTier.objects.create(
                        event=event, name=tier_name, price=price, quantity=quantity,
                        perks='Entry all day' if tier_name != 'VIP' else 'Front row, backstage, merch pack')
            made.append(event)
        return made

    def make_tickets(self, event, buyers):
        tier = event.ticket_tiers.order_by('price').first()
        if tier is None or Ticket.objects.filter(event=event).exists():
            return
        for index, buyer in enumerate(buyers):
            Ticket.objects.create(
                event=event, tier=tier, user=buyer,
                code=f'VT-{event.pk:02d}{index:02d}{random.randint(1000, 9999)}',
                status='valid', price_vc=int(tier.price / 10), price_ngn=tier.price,
                attendee_name=buyer.full_name, attendee_email=buyer.email,
            )
        tier.sold = len(buyers)
        tier.save(update_fields=['sold'])

    # ------------------------------------------------------ money + admin

    def make_wallet_history(self, user, joined, won):
        wallet = get_or_create_user_wallet(user)
        if wallet.transactions.exists():
            return
        now = timezone.now()
        # `reference` is unique and must be NULL, never '', when there is no
        # gateway reference - MySQL lets NULL repeat but not the empty string.
        rows = [
            ('top_up', 2000, 'Top up via Paystack', 'completed', 'DEMO-PSK-4471', None, 26),
            ('deduction', -300, f'Entry fee: {won.tournament_title}', 'completed', None, won, 14),
            ('prize', 12000, f'Runner up prize: {won.tournament_title}', 'completed', None, won, 7),
            ('send', -1500, 'Sent to demo_chidi', 'completed', None, None, 5),
            ('receive', 800, 'Received from demo_amara', 'completed', None, None, 4),
            ('top_up', 3000, 'Top up via Paystack', 'completed', 'DEMO-PSK-5520', None, 2),
            ('withdrawal', -4000, 'Withdrawal to GTBank 0123456789', 'pending', None, None, 1),
        ]
        for kind, amount, note, state, reference, tournament, days_ago in rows:
            txn = Transaction.objects.create(
                wallet=wallet, type=kind, amount=amount, description=note,
                status=state, reference=reference, tournament=tournament)
            Transaction.objects.filter(pk=txn.pk).update(created_at=now - timedelta(days=days_ago))
        wallet.wallet_balance = sum(r[1] for r in rows if r[3] == 'completed')
        wallet.kyc_verified = True
        wallet.save(update_fields=['wallet_balance', 'kyc_verified'])

        WithdrawalRequest.objects.get_or_create(
            wallet=wallet, amount=4000,
            defaults={'bank_name': 'GTBank', 'account_number': '0123456789',
                      'account_name': user.full_name, 'status': 'pending'},
        )

    def make_admin_queues(self, players):
        """Give the admin payout and KYC screens something real to act on."""
        for user in players[1:4]:
            wallet = get_or_create_user_wallet(user)
            if wallet.wallet_balance < 2500:
                wallet.wallet_balance = 2500
                wallet.save(update_fields=['wallet_balance'])
            WithdrawalRequest.objects.get_or_create(
                wallet=wallet, amount=1500,
                defaults={'bank_name': random.choice(['Access Bank', 'Zenith Bank', 'UBA']),
                          'account_number': str(random.randint(10 ** 9, 10 ** 10 - 1)),
                          'account_name': user.full_name, 'status': 'pending'},
            )
        for user, kind in zip(players[4:7], ['national_id', 'passport', 'drivers_license']):
            if KYCDocument.objects.filter(user=user).exists():
                continue
            document = KYCDocument(user=user, document_type=kind, status='pending')
            document.document_image.save(
                f'{user.username}_{kind}.jpg',
                flat_image(900, 560, 'steel', kind.replace('_', ' ').title(), 'Sample document'),
                save=False)
            document.save()

    def make_dispute(self, tournament, raised_by):
        if TournamentDispute.objects.filter(tournament=tournament).exists():
            return
        match = BracketMatch.objects.filter(
            tournament=tournament, status='completed').order_by('match_number').first()
        TournamentDispute.objects.create(
            tournament=tournament, match=match, raised_by=raised_by,
            description='Opponent left after round four and the score was recorded as a win. '
                        'Screenshot of the scoreboard attached.',
            evidence=[], status='open',
        )

    # -------------------------------------------------------------- misc

    def give_games_logos(self, games):
        for title, game in games.items():
            if not game.logo:
                game.logo.save(f'{game.pk}_logo.jpg',
                               flat_image(320, 320, 'ink', title.split()[0]), save=False)
                game.save()
