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

#: Enough of a squad to build a lineup with, so `--demo` is genuinely usable
#: rather than three rows that prove nothing. Nigerians are over-represented
#: on purpose: this is a Nigerian platform and a demo should look like one.
DEMO = [
    ('231747', 'Kylian Mbappe', 91, 'ST', 'Real Madrid', 'France', 'gold'),
    ('239085', 'Erling Haaland', 91, 'ST', 'Manchester City', 'Norway', 'gold'),
    ('192985', 'Kevin De Bruyne', 88, 'CM', 'Napoli', 'Belgium', 'gold'),
    ('203376', 'Virgil van Dijk', 89, 'CB', 'Liverpool', 'Netherlands', 'gold'),
    ('212831', 'Alisson', 89, 'GK', 'Liverpool', 'Brazil', 'gold'),
    ('202126', 'Harry Kane', 90, 'ST', 'Bayern Munchen', 'England', 'gold'),
    ('190871', 'Neymar Jr', 86, 'LW', 'Santos', 'Brazil', 'gold'),
    ('231866', 'Rodri', 89, 'CDM', 'Manchester City', 'Spain', 'gold'),
    ('209331', 'Mohamed Salah', 89, 'RW', 'Liverpool', 'Egypt', 'gold'),
    ('200389', 'Jan Oblak', 87, 'GK', 'Atletico Madrid', 'Slovenia', 'gold'),
    ('204963', 'Antoine Griezmann', 87, 'CF', 'Atletico Madrid', 'France', 'gold'),
    ('177003', 'Luka Modric', 85, 'CM', 'Milan', 'Croatia', 'gold'),
    ('20801', 'Cristiano Ronaldo', 86, 'ST', 'Al Nassr', 'Portugal', 'gold'),
    ('158023', 'Lionel Messi', 88, 'RW', 'Inter Miami', 'Argentina', 'gold'),
    ('1625', 'Pele', 98, 'CAM', 'Icons', 'Brazil', 'icon'),
    ('1179', 'Ronaldo Nazario', 97, 'ST', 'Icons', 'Brazil', 'icon'),
    ('237692', 'Victor Osimhen', 86, 'ST', 'Galatasaray', 'Nigeria', 'gold'),
    ('241486', 'Ademola Lookman', 84, 'LW', 'Atalanta', 'Nigeria', 'gold'),
    ('232411', 'Alex Iwobi', 79, 'CM', 'Fulham', 'Nigeria', 'gold'),
    ('246296', 'Calvin Bassey', 78, 'CB', 'Fulham', 'Nigeria', 'gold'),
    ('212188', 'Wilfred Ndidi', 80, 'CDM', 'Besiktas', 'Nigeria', 'gold'),
    ('235243', 'Samuel Chukwueze', 78, 'RW', 'Milan', 'Nigeria', 'gold'),
    ('244260', 'Stanley Nwabali', 74, 'GK', 'Chippa United', 'Nigeria', 'silver'),
    ('253117', 'Bruno Onyemaechi', 72, 'LB', 'Boavista', 'Nigeria', 'silver'),
    ('247635', 'Ola Aina', 79, 'RB', 'Nottingham Forest', 'Nigeria', 'gold'),
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
                'stats': {'pac': 80, 'sho': 80, 'pas': 80,
                          'dri': 80, 'def': 60, 'phy': 75},
                # Futbin's own CDN, the same paths the scraper reads, so a
                # demo card and a scraped one draw identically.
                'image_url': ('https://cdn.futbin.com/content/fifa24/img/'
                              'players/%s.png' % sid),
                'frame_url': ('https://cdn.futbin.com/design/img/cards/tiny/'
                              '%s.png' % item_type),
            } for sid, name, rating, position, club, nation, item_type in DEMO]
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
