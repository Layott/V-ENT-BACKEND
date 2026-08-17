"""Signal wiring for the tournament app.

The BracketMatch post_save receiver is the single hook that turns *any* match
completion into a bracket advance - including completions triggered by the
vent_auth admin views (override score / resolve dispute), which we must not edit.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import BracketMatch
from .services.advance import handle_match_saved


@receiver(post_save, sender=BracketMatch, dispatch_uid='vent_tournament_bracketmatch_advance')
def bracket_match_post_save(sender, instance, created, **kwargs):
    handle_match_saved(sender, instance, created, **kwargs)
