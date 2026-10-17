"""How much a studio's game is used, counted from the first session.

Design section 14, item 8: "Usage (monthly active players, sessions) is
counted from day one so either pricing model can be chosen later." Decision 9:
pricing is decided later. So nothing here charges anybody; it only keeps the
two numbers either model would need: linked players who played in a month, and
game sessions created in it.
"""
from datetime import date

from django.db.models import Count
from django.db.models.functions import TruncMonth
from django.utils import timezone


def month_of(when):
    when = timezone.localtime(when) if timezone.is_aware(when) else when
    return date(when.year, when.month, 1)


def count_players(game, user_ids, when=None):
    """Note each linked player once for the month of `when`."""
    from .models import StudioMonthlyPlayer

    month = month_of(when or timezone.now())
    for user_id in set(u for u in user_ids if u):
        StudioMonthlyPlayer.objects.get_or_create(game=game, month=month, user_id=user_id)


def monthly(game, months=6):
    """The last `months` months that had any use, newest first:
    [{month, active_players, sessions}]."""
    from .models import StudioMonthlyPlayer, StudioSession

    players = dict(StudioMonthlyPlayer.objects.filter(game=game)
                   .values('month').annotate(n=Count('user', distinct=True))
                   .values_list('month', 'n'))
    sessions = {}
    for row in (StudioSession.objects.filter(game=game)
                .annotate(m=TruncMonth('created_at')).values('m').annotate(n=Count('id'))):
        key = month_of(row['m']) if row['m'] else None
        if key:
            sessions[key] = sessions.get(key, 0) + row['n']
    keys = sorted(set(players) | set(sessions), reverse=True)[:months]
    return [{'month': key.isoformat(), 'active_players': players.get(key, 0),
             'sessions': sessions.get(key, 0)} for key in keys]
