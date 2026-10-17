from django.apps import AppConfig


class VentSitesConfig(AppConfig):
    """Tournaments and events on the organiser's own website (P8): proven
    sites, the embeds locked to them, and views counted per site."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'vent_sites'
