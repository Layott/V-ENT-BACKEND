"""May this person open this chapter, and what do they need if not.

ONE function answers it, and every screen asks the same one. The alternative is
the fault this codebase has produced most often: the reader and the API
disagreeing about whether something is readable, which shows up as a page that
opens and then refuses to turn.

## The five ways in

1. It is your own comic.
2. The series is free and the chapter is out.
3. The series is paid per chapter and you bought this one.
4. The series is a subscription and yours is live.
5. The chapter is still ahead of its date and you paid for early access.

Anything else is a refusal that NAMES what is missing, as a code, because the
screen showing it may be in French.

## Early access is a date, not a second price list

A chapter with `published_at` in the future is not a different kind of chapter.
It is the same chapter, ahead of its date, and paying moves ONE reader across
that line. On the date it opens on the series' ordinary terms, and the person
who paid is not charged again, which is what makes it early access rather than a
paywall with a countdown.
"""
from django.utils import timezone

from vent_auth import premium

from .models import ChapterPurchase, SeriesSubscription


class Refusal:
    """Why not, as a code and a number, never a sentence.

    `needs_coins` is what it would cost to fix it right now, so the screen can
    say "25 VENT COINS" rather than sending somebody to find out.
    """

    def __init__(self, code, needs_coins=0):
        self.code = code
        self.needs_coins = needs_coins

    def as_dict(self):
        return {'code': self.code, 'needs_coins': self.needs_coins}


def owns(user, series):
    return bool(user and user.is_authenticated
                and series.author_id == user.user_id)


def is_subscribed(user, series, now=None):
    if not (user and user.is_authenticated):
        return False
    row = SeriesSubscription.objects.filter(user=user, series=series).first()
    return bool(row and row.is_live(now))


def has_bought(user, chapter):
    if not (user and user.is_authenticated):
        return False
    return ChapterPurchase.objects.filter(user=user, chapter=chapter).exists()


def may_read(user, chapter, now=None):
    """None if they may read it, or a `Refusal` saying what is missing."""
    now = now or timezone.now()
    series = chapter.series

    # The author, always, including their own private drafts.
    if owns(user, series):
        return None

    if series.visibility != 'public':
        # A private series does not exist as far as anybody else is concerned.
        # NOT_FOUND rather than a refusal that admits it is there.
        return Refusal('NOT_FOUND')

    early = chapter.is_early(now)
    if early:
        if has_bought(user, chapter):
            return None
        if not chapter.early_access_vc:
            # Ahead of its date and not for sale early. Nobody can open it.
            return Refusal('NOT_OUT_YET')
        return Refusal('EARLY_ACCESS_REQUIRED', chapter.early_access_vc)

    if series.pricing == 'free':
        return None

    if series.pricing == 'per_chapter':
        if has_bought(user, chapter):
            return None
        return Refusal('CHAPTER_REQUIRED', series.chapter_price_vc)

    if series.pricing == 'subscription':
        if is_subscribed(user, series, now):
            return None
        return Refusal('SUBSCRIPTION_REQUIRED', series.subscription_price_vc)

    # An unknown pricing mode is a refusal rather than a way in. A typo in a
    # column must never open a paid chapter.
    return Refusal('NOT_AVAILABLE')


def may_see_series(user, series):
    """Whether the series itself is visible at all."""
    return series.visibility == 'public' or owns(user, series)


def shows_ads(user):
    """Whether this reader is shown advertisements.

    Premium removes them, which is the whole of the spec line. Signed out is
    not premium, so a stranger sees them too.
    """
    if not (user and user.is_authenticated):
        return True
    return not premium.has_premium(user)


def may_use_theme(user, theme):
    """Reader themes are premium; the free one is always allowed.

    The modes and font sizes are NOT gated: they are how somebody reads at all.
    Only the look is, which is the one line the spec marks PREMIUM.
    """
    from . import catalogue
    if theme == catalogue.FREE_THEME:
        return True
    return bool(user and user.is_authenticated and premium.has_premium(user))
