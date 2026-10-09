"""Tell everybody who asked that a module is open (inbox 421).

    python manage.py notify_module_open anime

The roadmap pages promise "Tell me when it opens". This keeps it: run it the day
a module's switch goes on (ANIME_ENABLED, the marketplace opening, the shop or
wagers going live). Each person is told once, in the app and by email through
the ordinary notification path, and `notified_at` stops a second run telling
them again.
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from vent_auth.models import ModuleInterest

NAMES = {
    'shop': ('The V-ENT Shop is open', '/shop'),
    'wager': ('Wagers are open on V-ENT', '/wager'),
    'marketplace': ('Vermillion City is open', '/marketplace'),
    'anime': ('The anime hub is open', '/anime'),
}


class Command(BaseCommand):
    help = 'Notify everybody who asked to be told when a module opens.'

    def add_arguments(self, parser):
        parser.add_argument('module', choices=sorted(NAMES))

    def handle(self, *args, **options):
        from vent_auth.views_notifications import create_notification

        module = options['module']
        title, link = NAMES[module]
        rows = ModuleInterest.objects.filter(module=module, notified_at__isnull=True).select_related('user')
        told = 0
        for row in rows:
            created = create_notification(
                row.user, 'roadmap', title,
                'You asked to be told when this opened. It is open now.',
                link=link, metadata={'module': module})
            if created is None:
                continue
            row.notified_at = timezone.now()
            row.save(update_fields=['notified_at'])
            told += 1
        self.stdout.write('%d told that %s is open.' % (told, module))
        if told == 0 and not ModuleInterest.objects.filter(module=module).exists():
            self.stdout.write('Nobody had asked.')
