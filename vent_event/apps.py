from django.apps import AppConfig


class VentEventConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'vent_event'

    def ready(self):
        # A ticket that is refunded or voided leaves its party games, on every
        # path that saves one (game_entries.drop_ticket).
        from django.db.models.signals import post_save

        from .game_entries import on_ticket_saved
        from .models import Ticket

        post_save.connect(on_ticket_saved, sender=Ticket,
                          dispatch_uid='vent_event.ticket_leaves_games')
