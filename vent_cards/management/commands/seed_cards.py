# -*- coding: utf-8 -*-
"""Fill the card catalogue from a file, with no scraper at all.

The scraper needs a desktop, a VPN and sometimes a person to answer Cloudflare.
Everything downstream of the catalogue - the picker, the lineup, the overlay -
needs cards and does not care where they came from. So this exists, and it is
what makes the rest testable today and demonstrable on a server that will never
run Playwright.

    python manage.py seed_cards --file cards.json
    python manage.py seed_cards --demo

The file is the same shape the scraper posts: a `cards` list, or a bare list.
"""

import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from vent_cards.models import GameCard
from vent_cards.views import INGEST_FIELDS, NEVER_BLANK, slugify_name

#: The six numbers a card shows, in the order the game shows them. A keeper's
#: six are different numbers with different names, and printing PAC on a
#: goalkeeper is wrong in a way anybody watching notices immediately.
OUTFIELD_KEYS = ['pac', 'sho', 'pas', 'dri', 'def', 'phy']
KEEPER_KEYS = ['div', 'han', 'kic', 'ref', 'spe', 'pos']

#: Enough of a squad to build a lineup with, so `--demo` is genuinely usable
#: rather than three rows that prove nothing. Nigerians are over-represented
#: on purpose: this is a Nigerian platform and a demo should look like one.
#:
#: The last column is the card's six numbers. They were all `80, 80, 80, 80,
#: 60, 75` until 4 September 2026, which is the "no real content" fault: every
#: card in a demonstration had identical stats, so the stats said nothing and
#: the demo taught a viewer nothing about the feature. These are each player's
#: own shape, roughly, so a defender reads as a defender on the pitch.
DEMO = [
    ('231747', 'Kylian Mbappe', 91, 'ST', 'Real Madrid', 'France', 'gold',
     (97, 90, 80, 92, 36, 78)),
    ('239085', 'Erling Haaland', 91, 'ST', 'Manchester City', 'Norway', 'gold',
     (87, 93, 68, 80, 45, 88)),
    ('192985', 'Kevin De Bruyne', 88, 'CM', 'Napoli', 'Belgium', 'gold',
     (70, 86, 93, 86, 63, 77)),
    ('203376', 'Virgil van Dijk', 89, 'CB', 'Liverpool', 'Netherlands', 'gold',
     (78, 60, 71, 72, 89, 86)),
    ('212831', 'Alisson', 89, 'GK', 'Liverpool', 'Brazil', 'gold',
     (87, 85, 88, 90, 55, 88)),
    ('202126', 'Harry Kane', 90, 'ST', 'Bayern Munchen', 'England', 'gold',
     (68, 93, 84, 83, 49, 83)),
    ('190871', 'Neymar Jr', 86, 'LW', 'Santos', 'Brazil', 'gold',
     (85, 82, 85, 92, 36, 60)),
    ('231866', 'Rodri', 89, 'CDM', 'Manchester City', 'Spain', 'gold',
     (63, 76, 87, 82, 85, 82)),
    ('209331', 'Mohamed Salah', 89, 'RW', 'Liverpool', 'Egypt', 'gold',
     (89, 87, 82, 88, 46, 76)),
    ('200389', 'Jan Oblak', 87, 'GK', 'Atletico Madrid', 'Slovenia', 'gold',
     (86, 84, 76, 89, 50, 87)),
    ('204963', 'Antoine Griezmann', 87, 'CF', 'Atletico Madrid', 'France', 'gold',
     (78, 84, 85, 87, 55, 71)),
    ('177003', 'Luka Modric', 85, 'CM', 'Milan', 'Croatia', 'gold',
     (72, 76, 88, 88, 71, 65)),
    ('20801', 'Cristiano Ronaldo', 86, 'ST', 'Al Nassr', 'Portugal', 'gold',
     (81, 90, 76, 80, 34, 76)),
    ('158023', 'Lionel Messi', 88, 'RW', 'Inter Miami', 'Argentina', 'gold',
     (80, 86, 90, 92, 33, 63)),
    ('1625', 'Pele', 98, 'CAM', 'Icons', 'Brazil', 'icon',
     (95, 96, 93, 96, 60, 76)),
    ('1179', 'Ronaldo Nazario', 97, 'ST', 'Icons', 'Brazil', 'icon',
     (97, 96, 82, 96, 45, 83)),
    ('237692', 'Victor Osimhen', 86, 'ST', 'Galatasaray', 'Nigeria', 'gold',
     (90, 86, 68, 81, 41, 84)),
    ('241486', 'Ademola Lookman', 84, 'LW', 'Atalanta', 'Nigeria', 'gold',
     (88, 80, 76, 86, 40, 68)),
    ('232411', 'Alex Iwobi', 79, 'CM', 'Fulham', 'Nigeria', 'gold',
     (80, 71, 78, 82, 62, 66)),
    ('246296', 'Calvin Bassey', 78, 'CB', 'Fulham', 'Nigeria', 'gold',
     (81, 42, 62, 66, 77, 83)),
    ('212188', 'Wilfred Ndidi', 80, 'CDM', 'Besiktas', 'Nigeria', 'gold',
     (72, 66, 72, 74, 82, 85)),
    ('235243', 'Samuel Chukwueze', 78, 'RW', 'Milan', 'Nigeria', 'gold',
     (89, 71, 71, 84, 34, 61)),
    ('244260', 'Stanley Nwabali', 74, 'GK', 'Chippa United', 'Nigeria', 'silver',
     (73, 72, 66, 75, 44, 71)),
    ('253117', 'Bruno Onyemaechi', 72, 'LB', 'Boavista', 'Nigeria', 'silver',
     (79, 52, 66, 70, 70, 68)),
    ('247635', 'Ola Aina', 79, 'RB', 'Nottingham Forest', 'Nigeria', 'gold',
     (88, 62, 71, 75, 77, 79)),
]


class Command(BaseCommand):
    help = 'Fill the EAFC card catalogue from a file, or with a demo squad.'

    def add_arguments(self, parser):
        parser.add_argument('--file', help='JSON in the ingest shape.')
        parser.add_argument('--demo', action='store_true',
                            help='A ready-made set big enough to pick an XI.')

    def handle(self, *args, **options):
        if options.get('demo'):
            rows = [{
                'source_id': sid,
                'name': name,
                'rating': rating,
                'position': position,
                'club': club,
                'nation': nation,
                'item_type': item_type,
                'stats': dict(zip(
                    KEEPER_KEYS if position == 'GK' else OUTFIELD_KEYS, six)),
                # DELIBERATELY EMPTY, like the frame below.
                #
                # CEO, 4 September 2026: "Are the player cards on the website
                # the actual cards from futbin?" They are not, and the portraits
                # made that worse rather than better. The seed built a Futbin
                # CDN address from an EA player id, and the ids in this table
                # are hand-written. Rendered side by side on 4 September, the
                # famous ones were right and the ones I had guessed were not:
                # "Victor Osimhen" showed Phil Foden, "Wilfred Ndidi" and
                # "Samuel Chukwueze" showed white players, and Bruno Onyemaechi
                # answered 404.
                #
                # A demo that shows one player's face under another player's
                # name is worse than one with no faces at all, so this table
                # ships none. `FutCard` draws initials, which is a designed
                # state. REAL PORTRAITS COME FROM THE SCRAPER, which reads both
                # image addresses off the page instead of building them.
                'image_url': '',
                # DELIBERATELY EMPTY. `cdn.futbin.com/design/img/cards/tiny/`
                # answers 404, checked on 4 September 2026, so every seeded
                # card fell back to a plain band and the CEO reported the cards
                # as carrying no design. FutCard draws the whole card from the
                # data now, and a URL that is known not to resolve is worse
                # than none: it makes the card wait on a request that fails.
                'frame_url': '',
            } for sid, name, rating, position, club, nation, item_type, six
              in DEMO]
        elif options.get('file'):
            try:
                with open(options['file'], encoding='utf-8') as handle:
                    body = json.load(handle)
            except OSError as caught:
                raise CommandError('Could not read that file: %s' % caught)
            except ValueError as caught:
                raise CommandError('That file is not JSON: %s' % caught)
            rows = body.get('cards') if isinstance(body, dict) else body
            if not isinstance(rows, list):
                raise CommandError('The file needs a list of cards.')
        else:
            raise CommandError('Give me --file or --demo.')

        added = changed = unchanged = skipped = 0
        for row in rows:
            if not isinstance(row, dict):
                skipped += 1
                continue
            source_id = str(row.get('source_id') or '').strip()
            name = str(row.get('name') or '').strip()
            rating = row.get('rating')
            if not source_id or not name or not rating:
                skipped += 1
                continue

            # The same rule the ingest endpoint uses: an absent field leaves
            # what is stored alone, so a thin file cannot erase good data.
            values = {f: row[f] for f in INGEST_FIELDS
                      if f in row and not (row[f] is None and f in NEVER_BLANK)}
            values['name'] = name
            values['slug'] = slugify_name(name)
            values['rating'] = int(rating)

            card = GameCard.objects.filter(source='futbin',
                                           source_id=source_id).first()
            if card is None:
                GameCard.objects.create(source='futbin', source_id=source_id,
                                        last_seen_at=timezone.now(), **values)
                added += 1
                continue

            moved = [f for f, v in values.items() if getattr(card, f) != v]
            if not moved:
                unchanged += 1
                continue
            for field in moved:
                setattr(card, field, values[field])
            card.last_seen_at = timezone.now()
            card.save()
            changed += 1

        self.stdout.write(self.style.SUCCESS(
            '%d added, %d changed, %d unchanged, %d skipped. %d cards in all.'
            % (added, changed, unchanged, skipped, GameCard.objects.count())))
