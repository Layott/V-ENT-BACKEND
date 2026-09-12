"""Give every event that has an address but no pin a pin.

CEO, 8 September 2026: "what I want is that when an event organizer puts an
address it should be located on the map and shown. that flow of it showing on
map to all users should be fixed."

New events geocode themselves on save. This is for the ones that already
exist, which is every event created before 8 September - including the Lagos
meetup the CEO screenshotted, whose page reads "There is no map of this venue"
under a perfectly findable address.

Rate limited to one request a second, which is what OpenStreetMap's usage
policy asks for. Dry by default, because it writes to real events.
"""
import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Fill latitude and longitude on events that have an address but no pin.'

    def add_arguments(self, parser):
        parser.add_argument('--write', action='store_true',
                            help='Actually save. Without this it only reports.')
        parser.add_argument('--limit', type=int, default=200)
        parser.add_argument('--slug', help='Just this one event.')

    def handle(self, *args, **options):
        from vent_event.geo import geocode
        from vent_event.models import Event

        events = Event.objects.filter(latitude__isnull=True)
        if options['slug']:
            events = events.filter(slug=options['slug'])
        events = events.exclude(location='', venue_name='')[:options['limit']]

        found = missed = 0
        for event in events:
            where = ', '.join(p for p in (event.venue_name, event.location) if p)
            point = geocode(event.venue_name, event.location)
            if point:
                found += 1
                self.stdout.write('  FOUND   %-38s %s  %s'
                                  % (event.slug or event.event_id,
                                     where[:38], point))
                if options['write']:
                    event.latitude, event.longitude = point
                    # `update_fields` so nothing else on the event moves. The
                    # save() hook would try to geocode again otherwise, and it
                    # would hit the cache, but writing only what changed is
                    # the honest thing on somebody else's event.
                    event.save(update_fields=['latitude', 'longitude'])
            else:
                missed += 1
                self.stdout.write('  no pin  %-38s %s'
                                  % (event.slug or event.event_id, where[:38]))
            # OpenStreetMap asks for no more than one request a second, and a
            # cached address costs nothing, so this only actually waits when
            # something was looked up.
            time.sleep(1.1)

        self.stdout.write('')
        self.stdout.write('%d found, %d without a pin.' % (found, missed))
        if not options['write']:
            self.stdout.write('Nothing was saved. Add --write to save.')
