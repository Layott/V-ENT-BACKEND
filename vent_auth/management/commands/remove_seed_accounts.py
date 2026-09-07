"""Remove the accounts that only ever existed to fill a screen.

CEO, 7 September 2026: "please remove all fake users and accounts that you are
not using for testing that is just mock ui."

## Why this is a command and not a migration

Deleting an account cannot be undone and takes its tickets, its wallet history
and its team memberships with it. A migration would do that on every box it
ran on, at deploy time, with nobody watching. This asks first: it prints what
it would remove and removes nothing unless told twice.

## What counts as seeded

An address in one of the seeding domains AND nothing real attached. Both
halves matter. `demo_organizer` is a seeded address that has sold tickets and
holds a wallet balance, so it is NOT removed by this - somebody has been using
it, and the CEO's own instruction is to leave what is being used for testing.

Anything with a real email domain is never touched, whatever else is true.
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

SEED_DOMAINS = ('@seed.v-ent.co', '@vent.test', '@example.com', '@example.org')


class Command(BaseCommand):
    help = 'List, and optionally delete, seeded demo accounts with no real activity.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete', action='store_true',
            help='Actually delete. Without this it only reports.')
        parser.add_argument(
            '--yes-i-mean-it', action='store_true',
            help='Required alongside --delete. Two flags, because this cannot '
                 'be undone.')
        parser.add_argument(
            '--keep', action='append', default=[],
            help='A username to keep whatever else is true. Repeatable.')

    def handle(self, *args, **options):
        from vent_auth.models import Transaction, Users, UserWallet
        from vent_event.models import Ticket, Vendor
        from vent_tournament.models import Tournament, TournamentRegistration

        keep = {k.lower() for k in options['keep']}

        seeded = Users.objects.none()
        for domain in SEED_DOMAINS:
            seeded = seeded | Users.objects.filter(email__iendswith=domain)
        seeded = seeded.distinct()

        removable, kept = [], []
        for user in seeded.order_by('username'):
            reasons = []
            if user.is_staff or user.is_superuser:
                reasons.append('staff')
            if user.username.lower() in keep:
                reasons.append('asked to keep')
            if Ticket.objects.filter(user=user).exists():
                reasons.append('holds tickets')
            if Tournament.objects.filter(tournament_creator=user).exists():
                reasons.append('created a tournament')
            if TournamentRegistration.objects.filter(user=user).exists():
                reasons.append('registered for a tournament')
            if Vendor.objects.filter(owner=user).exists():
                reasons.append('runs a stall')
            # A BALANCE is not evidence of use: the seeder grants one to every
            # demo account, so counting it kept 27 of 49 and the list read as
            # "almost everybody is real", which is the opposite of the truth.
            # Money that MOVED is the honest signal - a transaction exists only
            # because somebody did something.
            wallet = UserWallet.objects.filter(user=user).first()
            if wallet and Transaction.objects.filter(wallet=wallet).exists():
                reasons.append('has moved money')

            (kept if reasons else removable).append((user, reasons))

        self.stdout.write('%d seeded address(es) found.' % (len(removable) + len(kept)))
        self.stdout.write('')
        self.stdout.write('KEEPING %d, because something real is attached:' % len(kept))
        for user, reasons in kept:
            self.stdout.write('  %-22s %s' % (user.username, ', '.join(reasons)))
        self.stdout.write('')
        self.stdout.write('WOULD REMOVE %d, which have nothing attached:' % len(removable))
        for user, _ in removable:
            self.stdout.write('  %-22s %s' % (user.username, user.email))

        if not options['delete']:
            self.stdout.write('')
            self.stdout.write('Nothing was deleted. Add --delete --yes-i-mean-it to do it.')
            return

        if not options['yes_i_mean_it']:
            self.stdout.write('')
            self.stdout.write('--delete needs --yes-i-mean-it as well. Nothing was deleted.')
            return

        count = 0
        for user, _ in removable:
            user.delete()
            count += 1
        self.stdout.write('')
        self.stdout.write('Deleted %d account(s).' % count)
