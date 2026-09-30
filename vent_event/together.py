"""Going together at an event: who may do what, decided in one place.

Inbox 305, 305a to 305c (CEO, 28 September 2026) and the age table proposed the
same day ("build it", 29 September 2026). Spec:
`tasks/specs/stats-and-going-together-2026-09-29.md`.

Every door asks this module; none of them decides on its own. The rules:

* Only a signed-in person holding a live ticket to the event, in their own
  name, takes part.
* Age comes from the profile's date of birth. No date: nothing until it is
  added. Under 13: nothing. 13 to 15: attendance to mutuals at most, no
  departure area, no pings. 16 to 17: attendance to mutuals or the event,
  departure to mutuals, pings only between mutual 16 to 17s. 18+: everything.
* An adult never pings a minor and never sees a minor's departure area.
* An event marked 18+ (min_age 18) closes all three to minors.
* A block either way hides two people from each other completely.
* After the event ends, nobody sees a departure area and nobody can set one.
  Admins holding `view_location_history` still can, and every look is logged.
"""
from datetime import date

from django.db.models import Q
from django.utils import timezone

from vent_auth.models import Follow, UserBlock

UNKNOWN, UNDER_13, YOUNG, TEEN, ADULT = 'unknown', 'under_13', '13_15', '16_17', 'adult'

ATTENDANCE_BY_BAND = {
    UNKNOWN: ['off'], UNDER_13: ['off'],
    YOUNG: ['off', 'mutuals'],
    TEEN: ['off', 'mutuals', 'event'],
    ADULT: ['off', 'mutuals', 'event'],
}
DEPARTURE_BY_BAND = {
    UNKNOWN: ['off'], UNDER_13: ['off'], YOUNG: ['off'],
    TEEN: ['off', 'mutuals'],
    ADULT: ['off', 'mutuals', 'approved'],
}
PINGS_BY_BAND = {UNKNOWN: False, UNDER_13: False, YOUNG: False, TEEN: True, ADULT: True}

PINGS_PER_DAY = 10


def age_of(user, today=None):
    """Whole years from the profile's date of birth, or None when it is unset."""
    profile = user.userprofile_set.order_by('profile_id').first()
    born = getattr(profile, 'date_of_birth', None)
    if born is None:
        return None
    today = today or date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def band_of(user, today=None):
    age = age_of(user, today)
    if age is None:
        return UNKNOWN
    if age < 13:
        return UNDER_13
    if age < 16:
        return YOUNG
    if age < 18:
        return TEEN
    return ADULT


def is_minor(band):
    return band in (UNDER_13, YOUNG, TEEN)


def holds_ticket(event, user):
    return event.tickets.filter(user=user, status__in=('valid', 'checked_in')).exists()


def event_over(event, now=None):
    ends = event.ends_at() if hasattr(event, 'ends_at') else None
    return bool(ends and (now or timezone.now()) > ends)


def refusal(event, user):
    """Why this person cannot take part at all, as a code, or ''."""
    if user is None:
        return 'SIGN_IN'
    if not holds_ticket(event, user):
        return 'NO_TICKET'
    band = band_of(user)
    if band == UNKNOWN:
        return 'NEEDS_BIRTHDAY'
    if band == UNDER_13:
        return 'TOO_YOUNG'
    if is_minor(band) and (event.min_age or 0) >= 18:
        return 'ADULTS_ONLY_EVENT'
    return ''


def allowed(event, user):
    """The options this person may choose from, by band and event."""
    if refusal(event, user):
        return {'attendance': ['off'], 'departure': ['off'], 'pings': False}
    band = band_of(user)
    return {
        'attendance': ATTENDANCE_BY_BAND[band],
        'departure': [] if event_over(event) else DEPARTURE_BY_BAND[band],
        'pings': PINGS_BY_BAND[band],
    }


def blocked_either_way(a, b):
    return UserBlock.objects.filter(
        Q(blocker=a, blocked=b) | Q(blocker=b, blocked=a)).exists()


def mutual(a, b):
    return (Follow.objects.filter(follower=a, kind='user', target_id=b.user_id).exists()
            and Follow.objects.filter(follower=b, kind='user', target_id=a.user_id).exists())


def may_see_attendance(presence, viewer):
    owner = presence.user
    if viewer is None or viewer.user_id == owner.user_id:
        return viewer is not None
    if refusal(presence.event, owner) or blocked_either_way(owner, viewer):
        return False
    if presence.attendance_visibility == 'event':
        return holds_ticket(presence.event, viewer) or mutual(owner, viewer)
    if presence.attendance_visibility == 'mutuals':
        return mutual(owner, viewer)
    return False


def may_see_departure(presence, viewer):
    owner = presence.user
    if viewer is None:
        return False
    if viewer.user_id == owner.user_id:
        return True
    if (not presence.departure_area or event_over(presence.event)
            or refusal(presence.event, owner) or blocked_either_way(owner, viewer)):
        return False
    if is_minor(band_of(owner)) and not is_minor(band_of(viewer)):
        return False   # an adult never sees a minor's departure area
    vis = presence.departure_visibility
    if vis not in allowed(presence.event, owner)['departure']:
        return False   # a setting the owner is no longer allowed (an age change, an 18+ switch)
    if vis == 'mutuals':
        return mutual(owner, viewer)
    if vis == 'approved':
        return mutual(owner, viewer) or presence.approvals.filter(approved=viewer).exists()
    return False


def ping_refusal(event, sender, recipient, recipient_presence):
    """Why `sender` may not ping `recipient` here, as a code, or ''."""
    if sender.user_id == recipient.user_id:
        return 'PING_SELF'
    if refusal(event, sender):
        return refusal(event, sender)
    if not allowed(event, sender)['pings']:
        return 'PINGS_NOT_FOR_YOU'
    if recipient_presence is None or not recipient_presence.pings_open or refusal(event, recipient):
        return 'PINGS_CLOSED'
    if blocked_either_way(sender, recipient):
        return 'PINGS_CLOSED'
    s, r = band_of(sender), band_of(recipient)
    if is_minor(s) != is_minor(r):
        return 'PING_AGE'      # never between an adult and a minor
    if is_minor(s) and not mutual(sender, recipient):
        return 'PING_MUTUALS_ONLY'
    if not may_see_attendance(recipient_presence, sender):
        return 'PINGS_CLOSED'
    return ''


def pings_today(event, sender, now=None):
    now = now or timezone.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return event.pings.filter(sender=sender, created_at__gte=start).count()


def shared_taste(a, b):
    """Favourite games and interests the two have in common, by name."""
    from vent_auth.models import FavoriteGames, UserInterests
    games_a = set(FavoriteGames.objects.filter(user=a).values_list('game__game_title', flat=True))
    games_b = set(FavoriteGames.objects.filter(user=b).values_list('game__game_title', flat=True))
    likes_a = {x.strip().lower(): x.strip() for x in
               UserInterests.objects.filter(user=a).values_list('interests', flat=True) if x}
    likes_b = {x.strip().lower() for x in
               UserInterests.objects.filter(user=b).values_list('interests', flat=True) if x}
    return {
        'games': sorted(games_a & games_b),
        'interests': sorted(likes_a[k] for k in likes_a if k in likes_b),
    }
