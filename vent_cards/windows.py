# -*- coding: utf-8 -*-
"""Whether lineups are open right now, and when they close.

CEO, 3 September 2026: "The submission time should be a feature and something
the tournament organizers should be able to set."

One function answers it, and everything asks that function: the save endpoint,
the player's screen, the organiser's list and the overlay. A deadline computed
in two places is a deadline that refuses a save while the page still shows the
form open, which is the worst version of this feature.

The answer is a small object rather than a boolean, because "no" needs a reason
and a time. A refusal that only says no leaves somebody staring at a form
wondering whether it is them.
"""

from datetime import datetime, timedelta

from django.utils import timezone


class Window(object):
    """What a person may do with their lineup at this moment, and until when."""

    def __init__(self, state, closes_at=None, opens_at=None,
                 changes_allowed=0, reason=''):
        #: 'open', 'closed', 'changes_only', 'not_yet', or 'off'.
        self.state = state
        self.closes_at = closes_at
        self.opens_at = opens_at
        self.changes_allowed = changes_allowed
        self.reason = reason

    @property
    def can_edit(self):
        return self.state in ('open', 'changes_only')

    @property
    def limited(self):
        return self.state == 'changes_only'

    def payload(self):
        return {
            'state': self.state,
            'can_edit': self.can_edit,
            'limited': self.limited,
            'changes_allowed': self.changes_allowed if self.limited else None,
            'opens_at': self.opens_at,
            'closes_at': self.closes_at,
            'reason': self.reason,
        }


def _next_weekly(day, at_time, now):
    """The next occurrence of `day` at `at_time`, at or after `now`.

    A weekly deadline is the whole point of a league: "lineups in by Thursday
    ten" is a standing rule and nobody wants to retype a date every week.
    """
    if day is None or at_time is None:
        return None
    ahead = (int(day) - now.weekday()) % 7
    candidate = datetime.combine((now + timedelta(days=ahead)).date(), at_time)
    if timezone.is_naive(candidate):
        candidate = timezone.make_aware(candidate, timezone.get_current_timezone())
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def rules_for(tournament):
    """The tournament's lineup rules, or None when it has none."""
    from .models import LineupRules
    return LineupRules.objects.filter(tournament=tournament).first()


def window_for(tournament, now=None):
    """Whether lineups are open for this tournament right now."""
    now = now or timezone.now()
    rules = rules_for(tournament)

    # A tournament that has never been given rules has lineups switched off.
    # Silently defaulting to "open for ever" would put a picker on every
    # tournament on the platform, most of which are not EAFC.
    if rules is None or not rules.enabled:
        return Window('off', reason='LINEUPS_OFF')

    # The organiser's hand beats the clock, both ways, and is checked first
    # for exactly that reason.
    if rules.locked_by_hand:
        return Window('closed', reason='LOCKED_BY_ORGANISER')
    if rules.reopened_by_hand:
        return Window('open', reason='REOPENED_BY_ORGANISER')

    if rules.opens_at and now < rules.opens_at:
        return Window('not_yet', opens_at=rules.opens_at,
                      closes_at=rules.closes_at, reason='NOT_OPEN_YET')

    closes_at = rules.closes_at or _next_weekly(
        rules.weekly_day, rules.weekly_time, now)

    # No deadline set at all: open, and honest about having no end.
    if closes_at is None:
        return Window('open', opens_at=rules.opens_at)

    if now < closes_at:
        return Window('open', opens_at=rules.opens_at, closes_at=closes_at)

    # Past the deadline. The second window is the only way back in.
    if (rules.changes_open_at and rules.changes_close_at
            and rules.changes_open_at <= now < rules.changes_close_at):
        return Window('changes_only',
                      closes_at=rules.changes_close_at,
                      changes_allowed=rules.changes_allowed,
                      reason='CHANGES_WINDOW')

    return Window('closed', closes_at=closes_at, reason='DEADLINE_PASSED')
