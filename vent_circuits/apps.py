from django.apps import AppConfig


class VentCircuitsConfig(AppConfig):
    """Circuits (P5): a season of tournaments ranked as one competition, by
    points from their final standings or by Elo, in tiers, with public
    adjustments and divisions."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'vent_circuits'
