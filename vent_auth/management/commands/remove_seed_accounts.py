"""Remove named accounts, and nothing else, after saying exactly what will go.

CEO, 7 September 2026: "please remove all fake users and accounts that you are
not using for testing that is just mock ui." Then, shown the counts: "show me
let me decide."

## What changed on 8 September, and why

The first version of this command decided its own list from a set of email
domains and deleted whatever that produced. Two things are wrong with that.

The first is that the CEO asked to decide. A command that picks its own targets
cannot carry a decision somebody else made; it can only carry the opinion of
whoever wrote the domain list.

The second is worse and is a fact about this schema rather than a matter of
taste. Deleting an account is not a small delete:

  * `UserWallet.user` is CASCADE and `Transaction.wallet` is CASCADE, so the
    balance and every ledger line against it go with the account. Money that
    moved between two people is one row; deleting one side deletes the record
    both sides had.
  * `Teams.team_owner` is CASCADE, so deleting an owner deletes the TEAM, its
    wallet, its members and every tournament registration it ever made.
  * `Ticket.user` is CASCADE, so a live seat at a real event stops existing.
  * `Tournament.tournament_creator` is SET_NULL, so tournaments are NOT
    deleted. They are left with no organiser, which is not tidier than a
    seeded organiser, it is worse.
  * `MatchScore.submitted_by` is PROTECT, so an account that ever submitted a
    score REFUSES to delete. The old command deleted in a bare loop, so that
    refusal would have landed halfway through, some accounts gone and some not,
    with no record of which.

So this version does four things the old one did not: it takes the list from
the caller, it computes the real cascade with the same collector the Django
admin uses, it writes every row it is about to destroy to a file first, and it
deletes inside one transaction so a PROTECT refusal takes nothing with it.

## Using it

    # Build a list. Reports, never deletes, and picks nobody.
    python manage.py remove_seed_accounts --suggest

    # What would happen to these three. Still deletes nothing.
    python manage.py remove_seed_accounts --user demo_tobi --user demo_uche

    # Do it. Both flags, and an export is written before anything is destroyed.
    python manage.py remove_seed_accounts --from-file kill.txt --delete --yes-i-mean-it

`--from-file` takes one username per line; blank lines and lines starting `#`
are ignored, so the decision sheet's own rows can be pasted straight in.
"""
import json
import os
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers import serialize
from django.db import transaction
from django.db.models import ProtectedError

#: Accounts that run the live door at a real event. rivalryops1 and rivalryops2
#: are what the scanner phones sign in as, and an event day that starts with
#: those missing is an event day with a queue outside and no way in. They are
#: refused by name rather than left to a reviewer to notice.
DOOR_ACCOUNTS = ('rivalryops1', 'rivalryops2')

#: Only used by --suggest, which prints a starting point for a human list and
#: never deletes anything.
SEED_DOMAINS = ('@seed.v-ent.co', '@vent.test', '@example.com', '@example.org')


class Command(BaseCommand):
    help = ('Delete the accounts you name, after printing the full cascade and '
            'exporting every row it will destroy.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--user', action='append', default=[], metavar='USERNAME',
            help='An account to remove. Repeatable. Required unless --suggest.')
        parser.add_argument(
            '--from-file', default=None, metavar='PATH',
            help='A file of usernames, one per line. Blank lines and lines '
                 'starting # are ignored.')
        parser.add_argument(
            '--suggest', action='store_true',
            help='Print candidates to help build a list. Deletes nothing and '
                 'chooses nobody.')
        parser.add_argument(
            '--delete', action='store_true',
            help='Actually delete. Without it this only reports.')
        parser.add_argument(
            '--yes-i-mean-it', action='store_true',
            help='Required alongside --delete. Two flags, because a wallet, a '
                 'team and a ticket go with the account.')
        parser.add_argument(
            '--export-dir', default=None, metavar='PATH',
            help='Where to write the export. Defaults to '
                 'var/account-removals/<timestamp>/ under the project.')
        parser.add_argument(
            '--override-door-guard', action='store_true',
            help='Allow a door account to be removed. This breaks the ticket '
                 'scanner at a live event. Do not pass it without being told '
                 'to, in writing, by the person running that event.')

    # ------------------------------------------------------------------ list

    def _names(self, options):
        names = list(options['user'])
        path = options['from_file']
        if path:
            if not os.path.exists(path):
                raise CommandError('No such file: %s' % path)
            with open(path, encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        names.append(line)
        seen, ordered = set(), []
        for n in names:
            key = n.lower()
            if key not in seen:
                seen.add(key)
                ordered.append(n)
        return ordered

    # -------------------------------------------------------------- suggest

    def _suggest(self):
        from vent_auth.models import Transaction, Users, UserWallet
        from vent_event.models import Ticket, Vendor
        from vent_tournament.models import Tournament, TournamentRegistration

        seeded = Users.objects.none()
        for domain in SEED_DOMAINS:
            seeded = seeded | Users.objects.filter(email__iendswith=domain)
        seeded = seeded.distinct()

        quiet, busy = [], []
        for user in seeded.order_by('username'):
            reasons = []
            if user.is_staff or user.is_superuser:
                reasons.append('staff')
            if user.username.lower() in [d.lower() for d in DOOR_ACCOUNTS]:
                reasons.append('runs the live door')
            if Ticket.objects.filter(user=user).exists():
                reasons.append('holds tickets')
            if Tournament.objects.filter(tournament_creator=user).exists():
                reasons.append('created a tournament')
            if TournamentRegistration.objects.filter(user=user).exists():
                reasons.append('registered for a tournament')
            if Vendor.objects.filter(owner=user).exists():
                reasons.append('runs a stall')
            wallet = UserWallet.objects.filter(user=user).first()
            if wallet and Transaction.objects.filter(wallet=wallet).exists():
                reasons.append('has moved money')
            (busy if reasons else quiet).append((user, reasons))

        self.stdout.write('%d address(es) in a seeding domain.'
                          % (len(quiet) + len(busy)))
        self.stdout.write('')
        self.stdout.write('%d have something attached:' % len(busy))
        for user, reasons in busy:
            self.stdout.write('  %-22s %s' % (user.username, ', '.join(reasons)))
        self.stdout.write('')
        self.stdout.write('%d have nothing attached:' % len(quiet))
        for user, _ in quiet:
            self.stdout.write('  %-22s %s' % (user.username, user.email))
        self.stdout.write('')
        self.stdout.write('This is a starting point for a list, not a list. '
                          'Nothing was deleted and nobody was chosen.')

    # -------------------------------------------------------------- cascade

    def _collect(self, users):
        """Every row that would go, using the collector the admin uses."""
        from django.contrib.admin.utils import NestedObjects
        from django.db import router

        collector = NestedObjects(using=router.db_for_write(users[0].__class__))
        collector.collect(users)

        rows = []
        for model, instances in collector.data.items():
            rows.extend(instances)
        protected = list(getattr(collector, 'protected', []))
        return rows, protected

    def _money_and_seats(self, user):
        """The two things nobody can put back, said in plain numbers."""
        from vent_auth.models import Transaction, UserWallet, WithdrawalRequest
        from vent_event.models import Ticket

        wallet = UserWallet.objects.filter(user=user).first()
        return {
            'wallet_balance_vc': wallet.wallet_balance if wallet else None,
            'ledger_lines': (Transaction.objects.filter(wallet=wallet).count()
                             if wallet else 0),
            'withdrawal_requests': (
                WithdrawalRequest.objects.filter(wallet=wallet).count()
                if wallet else 0),
            'tickets_live': Ticket.objects.filter(
                user=user, status__in=('valid', 'checked_in')).count(),
            'tickets_total': Ticket.objects.filter(user=user).count(),
        }

    # ----------------------------------------------------------------- main

    def handle(self, *args, **options):
        from vent_auth.models import Users

        if options['suggest']:
            self._suggest()
            return

        names = self._names(options)
        if not names:
            raise CommandError(
                'No accounts named. This command removes what you list and '
                'nothing else.\n'
                'Use --user <username> (repeatable) or --from-file <path>, '
                'or --suggest to see candidates.')

        found, missing = [], []
        for name in names:
            user = Users.objects.filter(username__iexact=name).first()
            (found.append(user) if user else missing.append(name))

        if missing:
            raise CommandError(
                'These names are not accounts on this database, so the list is '
                'wrong and nothing was touched:\n  %s'
                % '\n  '.join(missing))

        blocked = [u for u in found
                   if u.username.lower() in [d.lower() for d in DOOR_ACCOUNTS]]
        if blocked and not options['override_door_guard']:
            raise CommandError(
                'These accounts run the live door and the ticket scanner signs '
                'in as them:\n  %s\n'
                'Removing one means a queue outside a real event with no way '
                'in. Nothing was touched. Pass --override-door-guard only if '
                'the person running that event has said to, in writing.'
                % '\n  '.join(u.username for u in blocked))

        rows, protected = self._collect(found)

        by_model = {}
        for obj in rows:
            label = obj._meta.label
            by_model[label] = by_model.get(label, 0) + 1

        total_before = Users.objects.count()

        self.stdout.write('%d account(s) named, out of %d on this database.'
                          % (len(found), total_before))
        self.stdout.write('')
        for user in found:
            m = self._money_and_seats(user)
            self.stdout.write('  %-22s %s' % (user.username, user.email))
            self.stdout.write(
                '      wallet %s VC, %d ledger line(s), %d withdrawal '
                'request(s), %d live ticket(s) of %d'
                % (m['wallet_balance_vc'], m['ledger_lines'],
                   m['withdrawal_requests'], m['tickets_live'],
                   m['tickets_total']))
        self.stdout.write('')
        self.stdout.write('%d row(s) would be destroyed, across %d table(s):'
                          % (len(rows), len(by_model)))
        for label in sorted(by_model):
            self.stdout.write('  %-46s %d' % (label, by_model[label]))

        if protected:
            self.stdout.write('')
            self.stdout.write(
                'REFUSED. %d row(s) are protected and a delete raises rather '
                'than running. A submitted match score is the usual one:'
                % len(protected))
            for obj in protected[:20]:
                self.stdout.write('  %s %s' % (obj._meta.label, obj.pk))
            if len(protected) > 20:
                self.stdout.write('  ... and %d more' % (len(protected) - 20))

        self.stdout.write('')
        self.stdout.write(
            'Tournaments are NOT in that list: Tournament.tournament_creator is '
            'SET_NULL, so any tournament these accounts created survives with '
            'no organiser on it. Reassign those before removing the account, or '
            'the listing shows a tournament nobody runs.')

        if not options['delete']:
            self.stdout.write('')
            self.stdout.write('Nothing was deleted. Add --delete '
                              '--yes-i-mean-it to do it.')
            return

        if not options['yes_i_mean_it']:
            self.stdout.write('')
            self.stdout.write('--delete needs --yes-i-mean-it as well. Nothing '
                              'was deleted.')
            return

        if protected:
            raise CommandError(
                'Refusing to delete while %d protected row(s) stand. Django '
                'would raise partway through. Nothing was touched.'
                % len(protected))

        # The export comes first, and a failure to write it stops the delete.
        # An export written afterwards is not an export, it is a hope.
        export_dir = options['export_dir'] or os.path.join(
            str(settings.BASE_DIR), 'var', 'account-removals',
            datetime.now().strftime('%Y-%m-%d-%H%M%S'))
        try:
            os.makedirs(export_dir, exist_ok=True)
            payload = serialize('json', rows, indent=1,
                                use_natural_foreign_keys=False)
            with open(os.path.join(export_dir, 'rows.json'), 'w',
                      encoding='utf-8') as fh:
                fh.write(payload)
            with open(os.path.join(export_dir, 'manifest.json'), 'w',
                      encoding='utf-8') as fh:
                json.dump({
                    'written_at': datetime.now().isoformat(timespec='seconds'),
                    'database': str(settings.DATABASES['default'].get('NAME')),
                    'accounts': [
                        {'username': u.username, 'email': u.email,
                         'user_id': u.user_id,
                         **self._money_and_seats(u)} for u in found],
                    'row_count': len(rows),
                    'by_model': by_model,
                }, fh, indent=1)
        except Exception as exc:
            raise CommandError(
                'Could not write the export to %s (%s). Nothing was deleted: '
                'a removal with no way back is not one this command performs.'
                % (export_dir, exc))

        self.stdout.write('')
        self.stdout.write('Export written to %s' % export_dir)
        self.stdout.write('  rows.json      every row about to be destroyed')
        self.stdout.write('  manifest.json  the accounts, their money and their seats')
        self.stdout.write('Put it back with: manage.py loaddata %s'
                          % os.path.join(export_dir, 'rows.json'))

        try:
            with transaction.atomic():
                for user in found:
                    user.delete()
        except ProtectedError as exc:
            raise CommandError(
                'A protected row refused the delete and the whole batch was '
                'rolled back, so nothing was removed: %s' % exc)

        total_after = Users.objects.count()
        self.stdout.write('')
        self.stdout.write('Accounts before: %d' % total_before)
        self.stdout.write('Accounts after:  %d' % total_after)
        self.stdout.write('Removed:         %d' % (total_before - total_after))
