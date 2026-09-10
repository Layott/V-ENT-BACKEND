"""The anime module: what somebody uploads, who reads it, and who they read with.

The spec is long and the tables are not, because most of it is one of four
shapes repeated: a thing an author owns, a thing a reader does to it, a room and
what happens inside it, and a battle.

**A chapter belongs to a series, and a volume is optional.** Not every comic has
volumes and forcing one would mean inventing "Volume 1" for everybody. So
`Chapter.volume` is nullable and the reader orders by `number` either way.

**Pricing lives on the SERIES, not the chapter.** An author who charges does so
for the work, and a per-chapter price that varies chapter by chapter is a
pricing page nobody wants to read. What a chapter carries is its own
`published_at`, which is what early access is about: paying to read something
before its date, rather than paying for something cheaper.

**Nothing here duplicates a table that exists.** Money is `UserWallet` and
`Transaction`. Premium is `vent_auth.premium`. Notifications are
`create_notification`. Reports are `UserReport`. What is genuinely new is a
comic, a chapter, a page, a reading room and a battle.

**Rooms are addressed by an opaque token.** A room has a name, but two friends
can make rooms with the same name in the same minute and the address has to
survive that, so the token is the address and the name is a label. The slug rule
says a nameless thing gets a token rather than its primary key, and this is the
same argument one step along.
"""
from django.db import models
from django.utils import timezone

from vent_auth.models import Users
from vent_auth.slugs import ensure_token, sync_slug

from . import catalogue


class Series(models.Model):
    """One comic. Manga, manhwa or webcomic, by one author."""

    KIND_CHOICES = [(k, v) for k, v in catalogue.KINDS.items()]
    STATUS_CHOICES = [(k, v) for k, v in catalogue.STATUSES.items()]
    PRICING_CHOICES = [(k, v) for k, v in catalogue.PRICING.items()]
    VISIBILITY_CHOICES = [(k, v) for k, v in catalogue.VISIBILITY.items()]

    series_id = models.AutoField(primary_key=True)
    slug = models.SlugField(max_length=180, unique=True, null=True, blank=True,
                            db_index=True)

    author = models.ForeignKey(Users, on_delete=models.CASCADE,
                               related_name='anime_series')

    title = models.CharField(max_length=160)
    synopsis = models.TextField(blank=True, default='')
    kind = models.CharField(max_length=12, choices=KIND_CHOICES,
                            default='manga')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES,
                              default='ongoing')

    cover = models.ImageField(upload_to='anime/covers/', null=True, blank=True)

    #: Genres and free tags are different things and are kept apart. A genre is
    #: from a catalogue everybody filters by; a tag is whatever the author
    #: wants to say. Merging them makes the filter useless within a month.
    genres = models.JSONField(default=list, blank=True)
    tags = models.JSONField(default=list, blank=True)

    visibility = models.CharField(max_length=10, choices=VISIBILITY_CHOICES,
                                  default='private')
    pricing = models.CharField(max_length=14, choices=PRICING_CHOICES,
                               default='free')

    #: What a chapter costs when `pricing` is per_chapter, and what a month of
    #: the series costs when it is subscription. In VENT COINS.
    chapter_price_vc = models.PositiveIntegerField(default=0)
    subscription_price_vc = models.PositiveIntegerField(default=0)

    #: Premium: a boost lifts the series in the browse order until its date.
    #: A date rather than a boolean, so a boost expires on its own rather than
    #: needing somebody to remember to take it off.
    boosted_until = models.DateTimeField(null=True, blank=True)

    #: Counted rather than computed on every list, because "most viewed" is a
    #: sort and sorting by a count of another table does not scale past a page.
    views = models.PositiveIntegerField(default=0)
    rating_total = models.PositiveIntegerField(default=0)
    rating_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['visibility', 'status']),
            models.Index(fields=['author', '-created_at']),
        ]
        verbose_name_plural = 'Series'

    def save(self, *args, **kwargs):
        # The slug follows the title, and every address it has ever had keeps
        # working. `sync_slug` adds `slug` to update_fields itself when it
        # changes, which is the trap: without that, a rename computes the new
        # slug and silently drops it.
        changed = sync_slug(self, self.title, entity_type='anime_series',
                            id_attr='series_id')
        if changed and kwargs.get('update_fields'):
            kwargs['update_fields'] = list(kwargs['update_fields']) + ['slug']
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    @property
    def rating(self):
        """Out of five, to one decimal, or None when nobody has rated it.

        None rather than 0: an unrated comic is not a badly rated one, and a
        list that sorts them together is a list that buries everything new.
        """
        if not self.rating_count:
            return None
        return round(self.rating_total / self.rating_count, 1)

    def is_boosted(self, now=None):
        return bool(self.boosted_until
                    and self.boosted_until > (now or timezone.now()))


class Volume(models.Model):
    """A group of chapters. Optional, because not every comic has them."""

    volume_id = models.AutoField(primary_key=True)
    series = models.ForeignKey(Series, on_delete=models.CASCADE,
                               related_name='volumes')
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=160, blank=True, default='')

    class Meta:
        ordering = ['number']
        unique_together = [('series', 'number')]

    def __str__(self):
        return '%s vol %s' % (self.series.title, self.number)


class Chapter(models.Model):
    """One chapter, its pages in order, and the date it opens to everybody."""

    chapter_id = models.AutoField(primary_key=True)
    slug = models.SlugField(max_length=200, unique=True, null=True, blank=True,
                            db_index=True)

    series = models.ForeignKey(Series, on_delete=models.CASCADE,
                               related_name='chapters')
    volume = models.ForeignKey(Volume, on_delete=models.SET_NULL, null=True,
                               blank=True, related_name='chapters')

    number = models.DecimalField(max_digits=7, decimal_places=2)
    title = models.CharField(max_length=200, blank=True, default='')

    #: When it opens to everybody on the series' ordinary terms. A date in the
    #: FUTURE is what early access is: a reader can pay coins to open it now,
    #: and on the date it costs nothing extra. NULL means it is out already.
    published_at = models.DateTimeField(null=True, blank=True)

    #: What early access costs, in VENT COINS. Zero means the author is not
    #: selling early access on this chapter even if it has a future date.
    early_access_vc = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['number']
        unique_together = [('series', 'number')]
        indexes = [models.Index(fields=['series', 'number'])]

    def save(self, *args, **kwargs):
        # The address carries the series, because "chapter 12" is not unique on
        # a platform and a reader pasting a link means a chapter of something.
        name = '%s %s %s' % (self.series.title, self.number, self.title or '')
        changed = sync_slug(self, name.strip(), entity_type='anime_chapter',
                            id_attr='chapter_id')
        if changed and kwargs.get('update_fields'):
            kwargs['update_fields'] = list(kwargs['update_fields']) + ['slug']
        super().save(*args, **kwargs)

    def __str__(self):
        return '%s #%s' % (self.series.title, self.number)

    def is_early(self, now=None):
        """Whether this is still ahead of its release date."""
        return bool(self.published_at
                    and self.published_at > (now or timezone.now()))


class Page(models.Model):
    """One image in a chapter, in its place."""

    page_id = models.AutoField(primary_key=True)
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE,
                                related_name='pages')
    number = models.PositiveIntegerField()
    image = models.ImageField(upload_to='anime/pages/')

    #: What is on it, for a reader who cannot see it and for a model reading
    #: the page. Empty is allowed and says so rather than inventing one.
    alt = models.CharField(max_length=300, blank=True, default='')

    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['number']
        unique_together = [('chapter', 'number')]

    def __str__(self):
        return '%s p%s' % (self.chapter, self.number)


class ChapterPurchase(models.Model):
    """Somebody paid to open a chapter: early, or because the series is paid."""

    purchase_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(Users, on_delete=models.CASCADE,
                             related_name='anime_chapter_purchases')
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE,
                                related_name='purchases')
    coins = models.PositiveIntegerField()

    #: Which of the two it was. Both open the chapter; they are different sales
    #: and an author's earnings screen should not have to guess.
    REASONS = (('early', 'Early access'), ('chapter', 'A paid chapter'))
    reason = models.CharField(max_length=10, choices=REASONS, default='chapter')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [('user', 'chapter')]


class SeriesSubscription(models.Model):
    """A paid subscription to one series, which runs out."""

    subscription_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(Users, on_delete=models.CASCADE,
                             related_name='anime_subscriptions')
    series = models.ForeignKey(Series, on_delete=models.CASCADE,
                               related_name='subscribers')
    until = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-until']
        unique_together = [('user', 'series')]

    def is_live(self, now=None):
        return self.until > (now or timezone.now())


class SeriesFollow(models.Model):
    """Following a series, which is free and is about being told, not access.

    Deliberately separate from `SeriesSubscription`: one is "tell me when a
    chapter lands" and the other is "I have paid to read it". Somebody can do
    either without the other, and a single table with a flag would make
    "everybody to notify" and "everybody with access" the same query, which they
    are not.
    """

    follow_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(Users, on_delete=models.CASCADE,
                             related_name='anime_follows')
    series = models.ForeignKey(Series, on_delete=models.CASCADE,
                               related_name='followers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('user', 'series')]


class ReadingProgress(models.Model):
    """Where somebody got to. One row per reader per series, not per chapter.

    Per series, because "continue reading" is a question about the WORK: a row
    per chapter would leave the reader to work out which chapter they were on.
    """

    progress_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(Users, on_delete=models.CASCADE,
                             related_name='anime_progress')
    series = models.ForeignKey(Series, on_delete=models.CASCADE,
                               related_name='progress')
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE,
                                related_name='progress')
    page_number = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        unique_together = [('user', 'series')]


class Bookmark(models.Model):
    """A page somebody marked on purpose, which is not where they got to."""

    bookmark_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(Users, on_delete=models.CASCADE,
                             related_name='anime_bookmarks')
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE,
                                related_name='bookmarks')
    page_number = models.PositiveIntegerField(default=1)
    note = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [('user', 'chapter', 'page_number')]


class ChapterComment(models.Model):
    """A comment on a chapter, or a reply to one."""

    comment_id = models.AutoField(primary_key=True)
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE,
                                related_name='comments')
    author = models.ForeignKey(Users, on_delete=models.CASCADE,
                               related_name='anime_comments')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True,
                               blank=True, related_name='replies')
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_removed = models.BooleanField(default=False)

    class Meta:
        ordering = ['created_at']


class SeriesRating(models.Model):
    """One reader's rating of one series, out of five, changeable."""

    rating_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(Users, on_delete=models.CASCADE,
                             related_name='anime_ratings')
    series = models.ForeignKey(Series, on_delete=models.CASCADE,
                               related_name='ratings')
    stars = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('user', 'series')]


class PromoMessage(models.Model):
    """A premium author writing to the people subscribed to their work.

    Kept as a row rather than fired and forgotten, because "did they get it" and
    "how many did that reach" are the two questions asked afterwards, and a
    notification fan-out answers neither.
    """

    promo_id = models.AutoField(primary_key=True)
    series = models.ForeignKey(Series, on_delete=models.CASCADE,
                               related_name='promos')
    author = models.ForeignKey(Users, on_delete=models.CASCADE,
                               related_name='anime_promos')
    subject = models.CharField(max_length=140)
    body = models.TextField()
    sent_to = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class ReaderSettings(models.Model):
    """How one reader likes to read. One row each, made on first save."""

    MODE_CHOICES = [(k, v) for k, v in catalogue.READING_MODES.items()]
    THEME_CHOICES = [(k, v) for k, v in catalogue.THEMES.items()]

    settings_id = models.AutoField(primary_key=True)
    user = models.OneToOneField(Users, on_delete=models.CASCADE,
                                related_name='anime_reader_settings')
    mode = models.CharField(max_length=10, choices=MODE_CHOICES,
                            default='single')
    theme = models.CharField(max_length=10, choices=THEME_CHOICES,
                             default=catalogue.FREE_THEME)
    font_scale = models.PositiveSmallIntegerField(default=100)
    updated_at = models.DateTimeField(auto_now=True)


class AnimeAd(models.Model):
    """A real advertisement an admin created, shown to readers without premium.

    This exists so "premium removes the ads" means something. The alternative
    was a grey box labelled "ad", which is a placeholder shipped to users, and
    the design rules ban exactly that. Where there are no rows, nobody sees an
    empty slot: the reader renders nothing at all.
    """

    ad_id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=140)
    image = models.ImageField(upload_to='anime/ads/', null=True, blank=True)
    url = models.URLField(blank=True, default='')
    weight = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-weight', '-created_at']


# ---------------------------------------------------------------------------
# Reading rooms
# ---------------------------------------------------------------------------


class ReadingRoom(models.Model):
    """A room where people read the same chapter at the same time."""

    PRIVACY_CHOICES = [(k, v) for k, v in catalogue.ROOM_PRIVACY.items()]
    CONTROL_CHOICES = [(k, v) for k, v in catalogue.ROOM_CONTROL.items()]

    room_id = models.AutoField(primary_key=True)
    #: The address. A token rather than a slug: two people can name a room the
    #: same thing in the same minute, and the address has to survive that.
    token = models.CharField(max_length=24, unique=True, null=True, blank=True,
                             db_index=True)

    host = models.ForeignKey(Users, on_delete=models.CASCADE,
                             related_name='anime_rooms_hosted')
    name = models.CharField(max_length=120)

    series = models.ForeignKey(Series, on_delete=models.CASCADE,
                               related_name='rooms')
    chapter = models.ForeignKey(Chapter, on_delete=models.SET_NULL, null=True,
                                blank=True, related_name='rooms')

    privacy = models.CharField(max_length=10, choices=PRIVACY_CHOICES,
                               default='private')
    #: Hashed, never the password itself, through Django's own hasher. A room
    #: password is a low-value secret and it is still somebody's password.
    password_hash = models.CharField(max_length=128, blank=True, default='')

    control = models.CharField(max_length=8, choices=CONTROL_CHOICES,
                               default='host')
    #: Who may turn the page right now. The host by default, and handed over
    #: rather than fought over.
    driver = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True,
                               blank=True, related_name='anime_rooms_driving')

    page_number = models.PositiveIntegerField(default=1)

    #: Bumped on every change anybody makes. The feed is "everything since this
    #: number", which is one integer rather than five timestamps to compare.
    version = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if ensure_token(self, 'rr', field='token', length=12) \
                and kwargs.get('update_fields'):
            kwargs['update_fields'] = list(kwargs['update_fields']) + ['token']
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @property
    def is_open(self):
        return self.closed_at is None

    def bump(self):
        """One version, one save. Every writer here calls this and nothing else."""
        self.version += 1
        self.save(update_fields=['version'])
        return self.version


class RoomMember(models.Model):
    """Somebody in a room, and whether they are actually still there."""

    member_id = models.AutoField(primary_key=True)
    room = models.ForeignKey(ReadingRoom, on_delete=models.CASCADE,
                             related_name='members')
    user = models.ForeignKey(Users, on_delete=models.CASCADE,
                             related_name='anime_room_memberships')

    joined_at = models.DateTimeField(auto_now_add=True)
    #: Touched by the feed, which is how "who is actually engaged" is answered
    #: without asking anybody to press anything.
    last_seen_at = models.DateTimeField(auto_now=True)
    left_at = models.DateTimeField(null=True, blank=True)
    was_removed = models.BooleanField(default=False)

    #: Counted for the session analytics the spec asks for.
    pages_seen = models.PositiveIntegerField(default=0)
    messages_sent = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['joined_at']
        unique_together = [('room', 'user')]

    def is_active(self, now=None, window_seconds=90):
        """Whether they were here recently enough to count as present."""
        if self.left_at or self.was_removed:
            return False
        gap = (now or timezone.now()) - self.last_seen_at
        return gap.total_seconds() <= window_seconds


class RoomMessage(models.Model):
    """Chat, and reactions, in one table because they are one stream.

    A reaction that arrives out of order with the chat around it reads as a
    reaction to the wrong thing, and two tables cannot be ordered against each
    other without a merge on every poll.
    """

    KINDS = (('chat', 'Said something'), ('reaction', 'Reacted'))

    message_id = models.AutoField(primary_key=True)
    room = models.ForeignKey(ReadingRoom, on_delete=models.CASCADE,
                             related_name='messages')
    author = models.ForeignKey(Users, on_delete=models.CASCADE,
                               related_name='anime_room_messages')
    kind = models.CharField(max_length=8, choices=KINDS, default='chat')
    body = models.CharField(max_length=1000, blank=True, default='')
    #: For a reaction: the emoji, and the page it was aimed at. "Most reacted-to
    #: scene" is a count over this, which is why the page is on the row.
    emoji = models.CharField(max_length=16, blank=True, default='')
    page_number = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['message_id']
        indexes = [models.Index(fields=['room', 'message_id'])]


class RoomAnnotation(models.Model):
    """A highlight, a sticky note or a drawing, on a page, seen by everybody."""

    KIND_CHOICES = [(k, v) for k, v in catalogue.ANNOTATION_KINDS.items()]

    annotation_id = models.AutoField(primary_key=True)
    room = models.ForeignKey(ReadingRoom, on_delete=models.CASCADE,
                             related_name='annotations')
    author = models.ForeignKey(Users, on_delete=models.CASCADE,
                               related_name='anime_annotations')
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE,
                                related_name='annotations')
    page_number = models.PositiveIntegerField(default=1)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES,
                            default='note')

    #: Where it sits, as fractions of the page rather than pixels, because the
    #: same annotation has to land in the same place on a phone and a laptop.
    x = models.FloatField(default=0)
    y = models.FloatField(default=0)
    w = models.FloatField(default=0)
    h = models.FloatField(default=0)

    text = models.CharField(max_length=500, blank=True, default='')
    #: A drawing, as a list of points in the same fractional space.
    path = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    is_removed = models.BooleanField(default=False)

    class Meta:
        ordering = ['annotation_id']


class RoomInvite(models.Model):
    """An invitation to a room, by link, by member or by email."""

    invite_id = models.AutoField(primary_key=True)
    room = models.ForeignKey(ReadingRoom, on_delete=models.CASCADE,
                             related_name='invites')
    invited_by = models.ForeignKey(Users, on_delete=models.CASCADE,
                                   related_name='anime_invites_sent')
    #: Exactly one of these, or neither for a plain link.
    user = models.ForeignKey(Users, on_delete=models.CASCADE, null=True,
                             blank=True, related_name='anime_invites')
    email = models.EmailField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']


class RoomSignal(models.Model):
    """One WebRTC signalling message, addressed to one member of the room.

    Voice chat is a mesh: each pair of participants negotiates directly, and all
    this table does is carry the offer, the answer and the ICE candidates
    between them until they connect. Nothing about the audio itself passes
    through V-ENT.

    Rows are consumed and deleted by the receiver's feed, so this stays small.
    """

    signal_id = models.AutoField(primary_key=True)
    room = models.ForeignKey(ReadingRoom, on_delete=models.CASCADE,
                             related_name='signals')
    from_user = models.ForeignKey(Users, on_delete=models.CASCADE,
                                  related_name='anime_signals_sent')
    to_user = models.ForeignKey(Users, on_delete=models.CASCADE,
                                related_name='anime_signals_received')
    kind = models.CharField(max_length=12)          # offer | answer | ice | bye
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['signal_id']
        indexes = [models.Index(fields=['room', 'to_user', 'signal_id'])]


# ---------------------------------------------------------------------------
# Official anime battles
# ---------------------------------------------------------------------------


class Battle(models.Model):
    """A character battle: nominations, then votes, then a decided winner."""

    STATE_CHOICES = [(k, v) for k, v in catalogue.BATTLE_STATES.items()]

    battle_id = models.AutoField(primary_key=True)
    slug = models.SlugField(max_length=180, unique=True, null=True, blank=True,
                            db_index=True)

    title = models.CharField(max_length=160)
    description = models.TextField(blank=True, default='')
    state = models.CharField(max_length=12, choices=STATE_CHOICES,
                             default='nominating')

    opens_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='anime_battles')
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        changed = sync_slug(self, self.title, entity_type='anime_battle',
                            id_attr='battle_id')
        if changed and kwargs.get('update_fields'):
            kwargs['update_fields'] = list(kwargs['update_fields']) + ['slug']
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class BattleCharacter(models.Model):
    """A character somebody nominated, and whether it made the battle."""

    character_id = models.AutoField(primary_key=True)
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE,
                               related_name='characters')
    name = models.CharField(max_length=140)
    source = models.CharField(max_length=140, blank=True, default='')
    image = models.ImageField(upload_to='anime/characters/', null=True,
                              blank=True)

    nominated_by = models.ForeignKey(Users, on_delete=models.SET_NULL,
                                     null=True, blank=True,
                                     related_name='anime_nominations')
    #: An admin decides what enters. A nomination nobody approved is visible to
    #: the person who made it and to nobody else.
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        unique_together = [('battle', 'name')]

    def __str__(self):
        return self.name


class AttributeVote(models.Model):
    """One person's score for one attribute of one character.

    A row per attribute rather than five columns, because the set of attributes
    is a catalogue and a column per entry means a migration every time it
    changes. Changeable until voting closes, which is why it is unique on the
    three and updated in place.
    """

    vote_id = models.AutoField(primary_key=True)
    character = models.ForeignKey(BattleCharacter, on_delete=models.CASCADE,
                                  related_name='votes')
    user = models.ForeignKey(Users, on_delete=models.CASCADE,
                             related_name='anime_votes')
    attribute = models.CharField(max_length=16)
    score = models.PositiveSmallIntegerField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('character', 'user', 'attribute')]
        indexes = [models.Index(fields=['character', 'attribute'])]


class BattleComment(models.Model):
    """The argument afterwards, which is most of the point."""

    comment_id = models.AutoField(primary_key=True)
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE,
                               related_name='comments')
    author = models.ForeignKey(Users, on_delete=models.CASCADE,
                               related_name='anime_battle_comments')
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_removed = models.BooleanField(default=False)

    class Meta:
        ordering = ['created_at']
