from django.apps import AppConfig


class VentTournamentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "vent_tournament"

    def ready(self):
        # Register the bracket auto-advance signal.
        from . import signals  # noqa: F401
