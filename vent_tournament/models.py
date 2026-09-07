from django.db import models
from django.utils import timezone
from vent_auth.models import Users, Games, Teams, Organization
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

class Tournament(models.Model):
    TOURNAMENT_VISIBILITY_CHOICES = [
        ('public', 'Public'),
        ('private', 'Private'),
        ('protected', 'Protected'),
    ]

    # How match scores get confirmed. Organizer picks this at tournament creation
    # (locked CEO decision 2026-05-26).
    SCORE_CONFIRMATION_MODE_CHOICES = [
        ('organizer_only', 'Organizer records results'),
        ('both_players_confirm', 'Both players confirm'),
        ('screenshot_required', 'Screenshot required'),
    ]

    # Lifecycle status. Kept in sync with `is_draft` (is_draft=True <=> status='draft')
    # and driven forward by the bracket/prize lifecycle flows.
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('registration_open', 'Registration open'),
        ('registration_closed', 'Registration closed'),
        ('live', 'Live'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    TOURNAMENT_ACCESS_CHOICES = [
        ('team', 'Team'),
        ('individual', 'Individual'),
        ('team_and_individual', 'Team and Individual'),
    ]

    TOURNAMENT_TYPE_CHOICES = [
        ('online', 'Online'),
        ('physical', 'Physical'),
        ('hybrid', 'Hybrid'),
    ]

    ENTRY_FEE_CHOICES = [
        ('Paid', 'Paid'),
        ('Free', 'Free'),
    ]

    PRIZE_TYPE_CHOICES = [
        ('distributed', 'Distributed'),
        ('winner_takes_all', 'Winner Takes All'),
        ('no_prize', 'No Prize'),
    ]


    tournament_id = models.AutoField(primary_key=True)
    tournament_title = models.CharField(max_length=148, null=False)
    # The readable half of the address. Generated once on creation and left
    # alone afterwards, so renaming a tournament cannot break a link somebody
    # has already shared.
    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True, db_index=True)
    tournament_game = models.ForeignKey(Games, on_delete=models.SET_NULL, null=True, blank=True)
    # Which edition of that game. Optional: plenty of titles have no editions,
    # and an older tournament predates the list existing.
    tournament_series = models.ForeignKey(
        'vent_auth.GameSeries', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tournaments')
    game_mode = models.CharField(max_length=50, null=True, blank=True)  # Game Mode

    # When entries open and close.
    #
    # The wizard has asked for these since it was written and sent them as
    # `reg_start_date_and_time` / `reg_end_date_and_time`. There were no columns
    # to put them in, so every organiser who filled them in had them silently
    # discarded and had to type them again on the next visit. Nothing raised,
    # because a field nobody stores is indistinguishable from a field nobody
    # sent.
    #
    # Nullable: a tournament that never says is open until it starts, which is
    # the behaviour everything already assumes.
    registration_opens_at = models.DateTimeField(null=True, blank=True)
    registration_closes_at = models.DateTimeField(null=True, blank=True)
    tournament_logo = models.ImageField(upload_to='tournament_logos/', null=True, blank=True)
    tournament_banner = models.ImageField(upload_to='tournament_banners/', null=True, blank=True)
    tournament_description = models.TextField(null=True)
    tournament_rules = models.TextField(null=True, blank=True)

    # The rules as a document, beside the typed version rather than instead of
    # it. CEO: "It should also allow uploading of documents, so people can
    # download the rule document also."
    #
    # Real rulesets run to pages, arrive as a PDF somebody already wrote, and
    # get argued over during a dispute - so an entrant needs the exact file the
    # organiser published, not a retyped summary of it. The typed field stays
    # because a reader on a phone should not have to download anything to see
    # whether substitutes are allowed.
    rules_document = models.FileField(upload_to='tournament_rules/', null=True, blank=True)
    bracket_type = models.CharField(max_length=50, default='Single Elimination')
    tournament_creator = models.ForeignKey(Users, on_delete=models.SET_NULL, null=True, blank=True, related_name='tournament_creator')
    tournament_organization = models.ForeignKey(Organization, on_delete=models.CASCADE, null=True, blank=True)

    start_date_and_time = models.DateTimeField()
    end_date_and_time = models.DateTimeField()
    tournament_visibility = models.CharField(max_length=9, choices=TOURNAMENT_VISIBILITY_CHOICES, default='public')
    tournament_type = models.CharField(max_length=8, choices=TOURNAMENT_TYPE_CHOICES)
    tournament_location = models.CharField(max_length=255, null=True, blank=True)
    virtual_link = models.URLField(null=True, blank=True)  # Virtual Link
    team_size = models.PositiveIntegerField(default=1)  # Default to 1 for individuals

    player_size = models.IntegerField(null=True, blank=True)
    min_number_of_teams = models.IntegerField(null=True, blank=True)
    max_number_of_teams = models.IntegerField(null=True, blank=True)

    # The pool as announced, and the currency it was announced in. The
    # per-position figures are what actually pay; this is what the organiser
    # said the whole thing was worth.
    prize_currency = models.CharField(max_length=3, blank=True, default='VC')
    prize_pool_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    prize_pool_total_vc = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    prize_type = models.CharField(max_length=20, choices=PRIZE_TYPE_CHOICES, default='no_prize')


    tournament_access = models.CharField(max_length=20, choices=TOURNAMENT_ACCESS_CHOICES)
    entry_fee = models.CharField(max_length=5, choices=ENTRY_FEE_CHOICES)
    entry_fee_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # Social Links
    facebook_link = models.URLField(null=True, blank=True)
    twitter_link = models.URLField(null=True, blank=True)
    instagram_link = models.URLField(null=True, blank=True)
    youtube_link = models.URLField(null=True, blank=True)
    twitch_link = models.URLField(null=True, blank=True)
    kick_link = models.URLField(null=True, blank=True)
    tiktok_link = models.URLField(null=True, blank=True)
    bigolive_link = models.URLField(null=True, blank=True)


    # Sponsors
    sponsors = models.ManyToManyField('Sponsors', blank=True)

    # Interaction Count
    interaction_count = models.PositiveIntegerField(default=0)  # To track user interactions

    # Check if its a draft
    is_draft = models.BooleanField(default=True)

    # Whether the organiser waves entrants through or looks at each one.
    #
    # PRD: "Automated or manual procedure to accept or decline teams from your
    # tournament (select yes or no)."
    #
    # Off means a confirmed registration the moment somebody pays or presses
    # join, which is what almost every open tournament wants. On means every
    # registration lands as `pending` and waits for the organiser, which is what
    # an invitational needs.
    approve_registrations = models.BooleanField(default=False)

    # --- M1 lifecycle additions --------------------------------------------
    score_confirmation_mode = models.CharField(
        max_length=20,
        choices=SCORE_CONFIRMATION_MODE_CHOICES,
        default='both_players_confirm',
        help_text='Who confirms match scores. Chosen by the organizer at creation.',
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='draft',
        help_text="Lifecycle status. 'draft' mirrors is_draft=True.",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.TextField(blank=True, default='')

    # Everything an organiser configures beyond the headline fields: who may
    # enter, how seeding is drawn, whether there is a check-in window, how long
    # a match is, whether there is a group stage. One column because these are
    # read and written together and the set keeps growing; see options.py for
    # the shape and the validation.
    options = models.JSONField(default=dict, blank=True)

    # When the organiser drew the line under check-in. Once this is set the
    # window is shut regardless of the clock: an entrant checking in after the
    # no-shows were forfeited would change a roster the organiser had already
    # signed off.
    check_in_closed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.tournament_title

    @property
    def prize_pool_coins(self):
        """Sum of every prize-distribution position, in VENT COINS."""
        total = self.prize_distributions.aggregate(models.Sum('prize'))['prize__sum']
        return int(total) if total else 0

    @property
    def is_paid_entry(self):
        """A tournament is 'paid' (KYC-gated) if it charges entry or awards a prize."""
        entry = int(self.entry_fee_price) if self.entry_fee_price else 0
        return self.entry_fee == 'Paid' and entry > 0 or self.prize_pool_coins > 0

    def save(self, *args, **kwargs):
        # The slug follows the name. Whatever it replaces is kept in SlugHistory
        # and redirects here, so a renamed tournament keeps every link ever shared.
        from vent_auth.slugs import sync_slug

        changed = sync_slug(
            self, self.tournament_title, entity_type='tournament', id_attr='tournament_id',
        )
        # A caller that named its fields (edit_tournament does) would otherwise
        # compute the new slug and never write it, which is the whole rename path.
        if changed and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})
        super().save(*args, **kwargs)


# What can separate two teams level on points. The organiser picks the order;
# these are the names they pick from.
#
# `head_to_head` is deliberately available but not a default: it is undefined
# for three-way ties and for teams that have not met yet, so a league that leans
# on it early has a table that cannot be computed. Goal difference always can be.
TIEBREAKERS = {
    'goal_difference': 'Goal difference',
    'goals_for': 'Goals scored',
    'goals_against': 'Fewest goals conceded',
    'wins': 'Most wins',
    'head_to_head': 'Result between the tied teams',
    'fixtures_won': 'Individual games won',
}

DEFAULT_TIEBREAKERS = ['goal_difference', 'goals_for', 'wins']


class LeagueRules(models.Model):
    """How a league table is scored, per tournament.

    One row per tournament rather than columns on Tournament, so a tournament
    that is not a league carries nothing, and so the defaults live in one place
    rather than being repeated at every read site.
    """

    tournament = models.OneToOneField(
        'Tournament', on_delete=models.CASCADE, related_name='league_rules',
    )

    points_win = models.IntegerField(default=3)
    points_draw = models.IntegerField(default=1)
    points_loss = models.IntegerField(default=0)

    # Ordered. The first one that separates two teams decides them.
    tiebreakers = models.JSONField(default=list, blank=True)

    # How many players each team fields in a tie. 2 for the EAFC format asked
    # for; the maths does not care, so a 3v3 or 5v5 league needs no code change.
    players_per_team = models.PositiveSmallIntegerField(default=2)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Rules for tournament {self.tournament_id}: {self.points_win}/{self.points_draw}/{self.points_loss}"

    def ordered_tiebreakers(self):
        """The organiser's order, ignoring anything unrecognised.

        An unknown name is dropped rather than raising: a tiebreaker removed in
        a later version must not make every existing league table uncomputable.
        """
        chosen = [t for t in (self.tiebreakers or []) if t in TIEBREAKERS]
        return chosen or list(DEFAULT_TIEBREAKERS)



class TournamentRuleset(models.Model):
    """The organiser's own rules for one tournament.

    A copy of a preset, then edited. Held on the tournament rather than looked up
    from a shared table, so changing a preset later cannot silently change the
    rules of an event that is already being played - which is the version of this
    that goes wrong publicly, halfway through a group stage.

    Everything a result is scored by lives in `data`: points for a win, the
    placement table, points per kill, and the tie-breakers IN ORDER. That order
    is the setting, not an implementation detail - round robin reads the meeting
    before the goals, and an organiser is allowed to disagree.

    JSON rather than columns because the shape genuinely differs by format: a
    battle royale has a placement table and no draw, a knockout has a best-of and
    neither. Columns for all of it would be mostly nulls, and every new format
    would be a migration.
    """

    tournament = models.OneToOneField(
        'Tournament', on_delete=models.CASCADE, related_name='ruleset')
    data = models.JSONField(default=dict)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tournament_rulesets_edited')

    def __str__(self):
        return f'Rules for tournament {self.tournament_id}'

    @property
    def format_key(self):
        return (self.data or {}).get('format') or self.tournament.bracket_type



class EntryRequirement(models.Model):
    """One thing an organiser demands before somebody may register.

    A row rather than a column, because four booleans cannot express "follow
    these three accounts and give me your Riot ID". `config` differs by kind -
    a country list, a minimum age, a set of links, the label of a field the
    organiser named - so it is JSON rather than twelve mostly-null columns and a
    migration for every new kind.

    `order` is the order they are shown and checked in, and it is the
    organiser's to arrange.
    """

    tournament = models.ForeignKey(
        'Tournament', on_delete=models.CASCADE, related_name='entry_requirements')
    kind = models.CharField(max_length=40)
    config = models.JSONField(default=dict, blank=True)
    # A requirement that is not required is shown and collected but does not
    # stop anybody: useful for asking a question without turning it into a gate.
    required = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f'{self.kind} for tournament {self.tournament_id}'


class EntrySubmission(models.Model):
    """What somebody gave, for a requirement a person has to check.

    Kept per user rather than per registration, because the whole point is that
    it is checked BEFORE they are registered - and for a team entry every member
    has their own.
    """

    STATUS_CHOICES = [
        ('pending', 'Waiting to be checked'),
        ('approved', 'Accepted'),
        ('refused', 'Not accepted'),
    ]

    requirement = models.ForeignKey(
        EntryRequirement, on_delete=models.CASCADE, related_name='submissions')
    user = models.ForeignKey(
        Users, on_delete=models.CASCADE, related_name='entry_submissions')
    # What they typed: a username per link, an id, an answer.
    value = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending')
    note = models.TextField(blank=True, default='')

    reviewed_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='entry_submissions_reviewed')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # One answer per person per requirement. Sending it again replaces it.
        unique_together = ('requirement', 'user')
        ordering = ['submitted_at']

    def __str__(self):
        return f'{self.user_id} for requirement {self.requirement_id}: {self.status}'


class TournamentPrizeDistribution(models.Model):
    id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='prize_distributions')
    position = models.IntegerField(null=False)
    prize = models.DecimalField(
        max_digits=10, decimal_places=2, null=False,
        help_text='Amount in VENT COINS. Always the converted figure - this is what pays out.',
    )
    # What the organiser actually typed, kept beside the converted figure so a
    # pool announced as "₦500,000" can still be displayed that way, and so a
    # rate change later cannot silently rewrite history.
    amount_original = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default='VC')

    extras = models.CharField(max_length=120, blank=True)
    extras_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    extras_prize = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='A bonus in VENT COINS, converted the same way as the prize.',
    )

    def __str__(self):
        return f"{self.tournament.tournament_title} - Position {self.position}"


class Sponsors(models.Model):
    sponsor_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    
    # Generic relation to support multiple models
    sponsor_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True)
    sponsor_id_object = models.PositiveIntegerField(null=True)
    sponsor = GenericForeignKey('sponsor_type', 'sponsor_id_object')

    logo = models.ImageField(upload_to='sponsor_logos/', null=True, blank=True)
    website = models.URLField(null=True, blank=True)

    def __str__(self):
        return self.name

class RegisteredTeams(models.Model):
    tournament_id = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='registered_teams')
    team_id = models.ForeignKey(Teams, on_delete=models.CASCADE)


class Match(models.Model):
    match_id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='matches')
    match_check_in_time = models.TimeField()
    match_check_in_date = models.DateField()
    match_check_in_started = models.BooleanField(default=False)
    match_check_in_ended = models.BooleanField(default=False)


class UnconfirmedTeams(models.Model):
    id = models.AutoField(primary_key=True)
    team_id = models.ForeignKey(Teams, on_delete=models.CASCADE)

    def __str__(self):
        return f"UnconfirmedTeam {self.id} - {self.team_id.team_name}"


class TournamentRegistration(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('disqualified', 'Disqualified'),
        ('withdrawn', 'Withdrawn'),
    ]

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='registrations')
    # Either team or individual - one will be null
    team = models.ForeignKey(Teams, on_delete=models.CASCADE, null=True, blank=True, related_name='tournament_registrations')
    user = models.ForeignKey(Users, on_delete=models.CASCADE, null=True, blank=True, related_name='tournament_registrations')
    # A side assembled for this one tournament out of people from anywhere.
    # See TournamentSquad. Exactly one of team, user and squad is set.
    squad = models.ForeignKey(
        'TournamentSquad', on_delete=models.CASCADE, null=True, blank=True,
        related_name='registrations')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    registered_at = models.DateTimeField(auto_now_add=True)
    entry_fee_paid = models.BooleanField(default=False)
    payment_reference = models.CharField(max_length=255, blank=True)
    # Seed set during bracket generation; final_position set when tournament completes (1 = winner).
    seed = models.PositiveIntegerField(null=True, blank=True)
    final_position = models.PositiveIntegerField(null=True, blank=True)
    # When this entrant confirmed they were actually there. Null after the
    # window closes means a no-show, which is what the forfeit reads.
    checked_in_at = models.DateTimeField(null=True, blank=True)
    # Set when a no-show is forfeited, so the reason survives on the record.
    forfeited_reason = models.CharField(max_length=120, blank=True, default='')

    class Meta:
        unique_together = [
            ('tournament', 'team'),
            ('tournament', 'user'),
            ('tournament', 'squad'),
        ]

    def __str__(self):
        return f"{self.entrant_name} @ {self.tournament.tournament_title}"

    @property
    def entrant(self):
        """The side this registration is, whichever kind it happens to be.

        Three kinds now: a club, a lone player, and a squad assembled for this
        tournament. Everything that reads a registration goes through here, so
        adding the third did not mean editing every caller and missing one.
        """
        return self.squad or self.team or self.user

    @property
    def entrant_kind(self):
        """'team', 'user', 'squad', or 'unknown'. One answer, not five copies."""
        if self.squad_id:
            return 'squad'
        if self.team_id:
            return 'team'
        if self.user_id:
            return 'user'
        return 'unknown'

    @property
    def entrant_id(self):
        """The id of whichever kind of side this is."""
        return self.squad_id or self.team_id or self.user_id

    @property
    def people(self):
        """Every user behind this side.

        A club registration reaches every member, not the captain: the captain
        is not reliably the person who turns up, and one member reading a
        reminder is what prevents the forfeit. A squad reaches its members for
        the same reason.

        Anything that has to tell a side something, or pay it, goes through
        here. Written once because it was written four times and every copy
        answered nothing at all for a squad: no reminders, no refund, no prize
        label.
        """
        if self.squad_id:
            return [m.user for m in self.squad.members.select_related('user')
                    if m.user_id]
        if self.team_id:
            from vent_auth.models import TeamMembers
            return [m.user for m in TeamMembers.objects
                    .filter(team_id=self.team_id).select_related('user')
                    if m.user_id]
        return [self.user] if self.user_id else []

    @property
    def acting_user(self):
        """The one person who acts for this side, and whose wallet it uses.

        A lone entrant is themselves. A club is its owner, because entering and
        being paid commit the club. A squad has no owner - the organiser
        assembled it - so it is the captain, and the first member added when
        nobody has been made captain. An organiser who wants somebody else paid
        makes them captain, which is a visible decision rather than a hidden
        rule.
        """
        if self.user_id:
            return self.user
        if self.team_id:
            return getattr(self.team, 'team_owner', None)
        if self.squad_id:
            member = (self.squad.members.select_related('user')
                      .order_by('-is_captain', 'added_at', 'pk').first())
            return member.user if member else None
        return None

    @property
    def entrant_name(self):
        side = self.entrant
        if side is None:
            return ''
        return (getattr(side, 'name', None)
                or getattr(side, 'team_name', None)
                or getattr(side, 'username', ''))


class TournamentSquad(models.Model):
    """A side assembled for one tournament out of people from anywhere.

    CEO, 3 September 2026: "each player for team nigeria in the rivalry series
    is registered to a different team, but both nigerian players will be working
    together as a team for nigeria... so we can invite players from different
    orgs and then they play as a team on the site, while still representing
    their individual teams or orgs on the site."

    Before this a tournament accepted two kinds of entrant: a `Teams` row, or a
    lone player. Team Nigeria is neither. Entering it as a club meant inventing
    a club called Nigeria and throwing away the fact that its two players play
    for two different ones; entering the players separately meant throwing away
    the fact that they are one side. Both lose something that was true.

    So: a squad belongs to ONE tournament, is named by the organiser, and its
    members each keep the club or organisation they actually play for. National
    sides, all-star sides, and any mixed-club side are the same shape.

    It is a third kind of entrant on the same `TournamentRegistration`, not a
    second tournament model. Everything that reads an entrant reads
    `registration.entrant`, so a squad plays exactly as a club does: it seeds,
    it appears in the bracket, it is scored, it stands in the table.

    It is deliberately NOT a `Teams` row. A club has an owner, a wallet, a
    membership list people join and leave, and a life beyond any one event. A
    squad has none of that and should not inherit it.
    """

    id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name='squads')
    name = models.CharField(max_length=80)
    #: The short form a broadcast uses. `NGA` on a scorebar.
    tag = models.CharField(max_length=8, blank=True, default='')
    logo = models.ImageField(upload_to='squad_logos/', null=True, blank=True)
    created_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='squads_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        unique_together = [('tournament', 'name')]

    def __str__(self):
        return '%s (squad in tournament %s)' % (self.name, self.tournament_id)


class SquadMember(models.Model):
    """One player in a squad, and who they represent while playing in it.

    `represents_team` and `represents_org` are a SNAPSHOT, taken when the player
    is added. A player transferring to another club in October must not rewrite
    who they played for in September: the record of an event is a record of what
    was true at the time, and a live foreign key would quietly falsify it.
    """

    id = models.AutoField(primary_key=True)
    squad = models.ForeignKey(
        TournamentSquad, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(
        Users, on_delete=models.CASCADE, related_name='squad_memberships')
    represents_team = models.ForeignKey(
        Teams, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='squad_representations')
    represents_org = models.ForeignKey(
        Organization, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='squad_representations')
    #: The name as it was on the day, kept even if the club is renamed or gone.
    represents_name = models.CharField(max_length=148, blank=True, default='')
    is_captain = models.BooleanField(default=False)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_captain', 'user__username']
        unique_together = [('squad', 'user')]

    def __str__(self):
        return '%s in %s' % (self.user.username, self.squad_id)


class BracketMatch(models.Model):
    STATUS_CHOICES = [
        ('scheduled', 'Scheduled'),
        ('in_progress', 'In Progress'),
        ('pending_opponent_confirm', 'Pending opponent confirm'),
        ('completed', 'Completed'),
        ('disputed', 'Disputed'),
        ('bye', 'Bye'),
        # A match nobody turned up for, and one that will not be played.
        #
        # Every real league has both and a bracket with neither forces an
        # organiser to invent a scoreline, which then counts as a genuine
        # result in the goal columns for ever. The CADE spreadsheet models
        # them explicitly, with the walkover carrying configurable points and
        # notional goals, and a cancelled match counting for nothing anywhere.
        ('walkover_p1', 'Walkover to the first participant'),
        ('walkover_p2', 'Walkover to the second participant'),
        ('cancelled', 'Cancelled'),
    ]

    # Which sub-bracket the match lives in. Single elim / round robin only use 'winners'.
    BRACKET_SIDE_CHOICES = [
        ('winners', 'Winners bracket'),
        ('losers', 'Losers bracket'),
        ('grand_final', 'Grand final'),
    ]

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='bracket_matches')
    round_number = models.PositiveIntegerField()
    match_number = models.PositiveIntegerField()
    bracket_side = models.CharField(max_length=12, choices=BRACKET_SIDE_CHOICES, default='winners')
    participant_1 = models.ForeignKey(
        TournamentRegistration, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='matches_as_p1'
    )
    participant_2 = models.ForeignKey(
        TournamentRegistration, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='matches_as_p2'
    )
    winner = models.ForeignKey(
        TournamentRegistration, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='matches_won'
    )
    score_p1 = models.IntegerField(default=0)
    score_p2 = models.IntegerField(default=0)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='scheduled')
    scheduled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Who entered this result and when. Results can now be recorded by a
    # scorekeeper the organiser named, so "who put this score in" is a
    # question with more than one answer, and it gets asked.
    recorded_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='results_recorded')
    recorded_at = models.DateTimeField(null=True, blank=True)

    # The running order, which belongs to the organiser and is not derived.
    #
    # CEO, of the Rivalry Series schedule: "Given, not generated. Layo set it.
    # Do not reorder it to optimise something without asking."
    #
    # A day and a position within that day, rather than a clock time: an
    # organiser building a three-day event thinks "Nigeria v Ghana opens Friday",
    # not "10:00". Times can be derived from the order and a slot length; the
    # order cannot be derived from anything.
    #
    # Null day means unscheduled, which is the state every fixture starts in and
    # a real thing to show: it is the list of what still needs a slot.
    day = models.DateField(null=True, blank=True)
    running_order = models.PositiveIntegerField(default=0)

    # Advancement graph, wired at bracket-generation time. When this match
    # resolves, the winner (and, for double elim, the loser) is routed into the
    # target match/slot below. NULL target => this side ends here.
    winner_to_match = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    winner_to_slot = models.PositiveSmallIntegerField(null=True, blank=True)  # 1 or 2
    loser_to_match = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    loser_to_slot = models.PositiveSmallIntegerField(null=True, blank=True)  # 1 or 2
    is_final = models.BooleanField(default=False)  # decides tournament completion

    class Meta:
        ordering = ['round_number', 'match_number']

    def __str__(self):
        return f"{self.tournament.tournament_title} R{self.round_number} M{self.match_number}"

    def participant_owned_by(self, user):
        """Return 1 or 2 if `user` controls that participant slot, else None.

        A user controls a registration when it is their solo registration, or
        when they own the registered team (M1 team-scope simplification).
        """
        for slot, reg in ((1, self.participant_1), (2, self.participant_2)):
            if reg is None:
                continue
            # `acting_user` is the one person who acts for a side, whichever
            # kind it is. Written by hand this knew a club owner and a lone
            # player, so nobody could act for a squad: its captain could not
            # confirm a score in their own tie.
            person = reg.acting_user
            if person is not None and person.user_id == user.user_id:
                return slot
        return None


class TournamentDispute(models.Model):
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('under_review', 'Under Review'),
        ('resolved', 'Resolved'),
        ('dismissed', 'Dismissed'),
    ]

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='disputes')
    match = models.ForeignKey(BracketMatch, on_delete=models.SET_NULL, null=True, blank=True, related_name='disputes')
    raised_by = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='disputes_raised')
    description = models.TextField()
    evidence = models.JSONField(default=list, blank=True)  # list of image URLs / notes
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    resolution_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Dispute by {self.raised_by.username} on {self.tournament.tournament_title}"


class MatchScore(models.Model):
    """Audit trail of every score submission on a match (not just current state)."""
    match = models.ForeignKey(BracketMatch, on_delete=models.CASCADE, related_name='score_submissions')
    submitted_by = models.ForeignKey(Users, on_delete=models.PROTECT, related_name='match_scores_submitted')
    score_p1 = models.IntegerField()
    score_p2 = models.IntegerField()
    evidence_url = models.CharField(max_length=500, blank=True)
    confirmed = models.BooleanField(default=False)
    confirmed_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True, related_name='match_scores_confirmed'
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    superseded_by = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='supersedes'
    )

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"Score {self.score_p1}-{self.score_p2} on match {self.match_id} by {self.submitted_by_id}"


class TieFixture(models.Model):
    """One player-versus-player game inside a team tie.

    The aggregate format the CEO described: a tie between two teams is not one
    game, it is one game per player slot, and the tie is decided by the TOTAL
    goals across them rather than by who won more of them.

        team A player 1 beats team B player 1     3-0
        team A player 2 loses to team B player 2  0-2
        aggregate                                 A 3-2 B, A wins

    Each team won a fixture, and A still wins the tie. Counting fixtures won
    would call that a draw, which is why the aggregate is stored rather than
    derived from the individual results.

    `slot` pairs the players: slot 1 of one team plays slot 1 of the other. It
    is the roster position, not a seeding.
    """

    STATUS_CHOICES = [
        ('scheduled', 'Scheduled'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('disputed', 'Disputed'),
        ('forfeit', 'Forfeit'),
    ]

    tie = models.ForeignKey(
        BracketMatch, on_delete=models.CASCADE, related_name='fixtures',
    )
    slot = models.PositiveSmallIntegerField()

    # The two people actually playing. Nullable because a tie is scheduled
    # before both rosters are necessarily locked, and a forfeited slot has a
    # score with nobody behind it.
    player_1 = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tie_fixtures_as_p1',
    )
    player_2 = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tie_fixtures_as_p2',
    )

    goals_1 = models.IntegerField(default=0)
    goals_2 = models.IntegerField(default=0)

    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='scheduled')
    completed_at = models.DateTimeField(null=True, blank=True)

    # Who entered this seat's score, and when. Results can be recorded by a
    # scorekeeper the organiser named, so "who put this in" has more than one
    # answer and it gets asked. The knockout path stored it from the day
    # scorekeepers existed; this one did not, and a live walk on 3 September
    # found a settled tie whose author was nobody.
    recorded_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='fixtures_recorded')
    recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['tie_id', 'slot']
        constraints = [
            models.UniqueConstraint(fields=['tie', 'slot'], name='one_fixture_per_slot'),
        ]

    def __str__(self):
        return f"Tie {self.tie_id} slot {self.slot}: {self.goals_1}-{self.goals_2}"

    @property
    def decided(self):
        return self.status == 'completed'

    @property
    def winner_slot(self):
        """1, 2, or None for a draw. About this fixture only, not the tie."""
        if not self.decided or self.goals_1 == self.goals_2:
            return None
        return 1 if self.goals_1 > self.goals_2 else 2


class BracketGeneration(models.Model):
    """Audit row capturing how a tournament's bracket was seeded/generated."""
    SEED_STRATEGY_CHOICES = [
        ('random', 'Random'),
        ('ranked', 'Ranked'),
        ('manual_order', 'Manual order'),
    ]

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='bracket_generations')
    generated_by = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='bracket_generations')
    seed_strategy = models.CharField(max_length=20, choices=SEED_STRATEGY_CHOICES, default='random')
    seed_payload = models.JSONField(default=dict)  # frozen list of registration_ids in seeded order
    match_count = models.PositiveIntegerField(default=0)
    rounds_count = models.PositiveIntegerField(default=0)
    generated_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Bracket gen #{self.id} for {self.tournament.tournament_title}"


class PrizePayout(models.Model):
    """Audit + idempotency record for a single prize-position payout."""
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='prize_payouts')
    winner_registration = models.ForeignKey(
        TournamentRegistration, on_delete=models.PROTECT, related_name='prize_payouts'
    )
    position = models.PositiveIntegerField()
    amount = models.IntegerField()  # VENT COINS credited
    # Lazy string ref - Transaction lives in vent_auth.
    transaction = models.ForeignKey(
        'vent_auth.Transaction', on_delete=models.PROTECT, related_name='+', null=True, blank=True
    )
    paid_at = models.DateTimeField(auto_now_add=True)
    paid_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True, related_name='prize_payouts_made'
    )
    auto_distributed = models.BooleanField(default=False)

    class Meta:
        unique_together = [('tournament', 'position')]

    def __str__(self):
        return f"Payout pos {self.position} - {self.amount} VC ({self.tournament.tournament_title})"


class TournamentStage(models.Model):
    """One phase of a tournament that runs in more than one.

    A major is not one format: it is groups and then a playoff, or Swiss and
    then a top cut. `Format.can_feed_into` recorded which combinations are
    possible and nothing read it, so every tournament was one format start to
    finish and anybody running a real event made two tournaments and copied the
    names across by hand.

    `rules` is the stage's own scoring. A group phase and the playoff after it
    are frequently scored differently, and an organiser who cannot say so has to
    pick one and be wrong for half the event. Null means the format's standard
    rules, which is what most stages want.
    """

    STATUSES = (
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('complete', 'Complete'),
    )

    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name='stages')
    order = models.PositiveIntegerField(default=0)
    label = models.CharField(max_length=60)
    format = models.CharField(max_length=40)

    # How many leave this stage. With groups it is how many leave EACH group,
    # which is what an organiser means by "top two from each group".
    advances = models.PositiveIntegerField(default=0)
    groups = models.PositiveIntegerField(default=0)

    rules = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUSES, default='pending')

    # Who came out of it, recorded when the organiser advances the stage rather
    # than recomputed later. A standing recomputed after a dispute is resolved
    # would silently change who was already sent through.
    advanced = models.JSONField(default=list, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['order']
        unique_together = ('tournament', 'order')

    def __str__(self):
        return '%s: %s' % (self.tournament_id, self.label)


class TournamentInvite(models.Model):
    """A code that lets somebody register for a tournament they could not otherwise.

    Two things the PRD asks for, and they are the same object seen twice:

      "generate unique URLs or codes for the tournament that can be shared with
      specific groups or teams (generate up to 64 codes for free users)"

      "automated (a system where a couple of codes can be automatically created
      and then, only if the teams/players that want to register input those
      codes will they then be able to register) or manually (where the user
      inputs the different codes or uploads the codes in a document)"

    So: a row per code. Generated in a batch or typed in one at a time, and the
    URL is just the code on the end of the tournament's address.

    A code is single-use by default because that is what "one slot" means, and
    an organiser who wants a code a whole team can use sets `max_uses` above one
    rather than being handed a different kind of object.
    """

    id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name='invites')
    code = models.CharField(max_length=32)

    # Who it was meant for, so an organiser reading their list of 64 codes can
    # tell which one they sent to whom. Free text: they are as likely to write
    # "the Lagos lot" as a username.
    label = models.CharField(max_length=120, blank=True, default='')

    max_uses = models.PositiveIntegerField(default=1)
    used_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tournament_invites_made')

    class Meta:
        unique_together = ('tournament', 'code')
        ordering = ['id']

    def __str__(self):
        return '%s for tournament %s' % (self.code, self.tournament_id)

    @property
    def spent(self):
        return self.used_count >= self.max_uses

    def matches(self, given):
        return str(given or '').strip().lower() == self.code.strip().lower()


class TournamentMetric(models.Model):
    """One thing this tournament counts, and what it is worth.

    PRD section 3 asks for performance metrics "specific to the game" and for
    "tie breakers for MVPs and teams" among the organiser's settings. Both mean
    the same thing: the organiser decides what a good game is here, and the
    platform does the arithmetic.

    Stored as rows rather than as a JSON blob on Tournament, for the reason
    entry requirements were: a refusal, a weight or an order has to name WHICH
    metric it is about, and a list of dicts cannot be filtered, ordered or
    pointed at by a foreign key.

    `position` is the tiebreak order. Two players level on total score are
    separated by the first metric in this list, then the second, which is the
    same shape as the league table's tiebreakers and deliberately so - two
    orderings that behave differently are two orderings somebody has to hold in
    their head.
    """

    id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name='metrics')
    # The key from vent_tournament.metrics.METRICS. Not a FK, because the
    # catalogue is code rather than data: it ships with the release, is the
    # same for everybody, and a migration to add a metric would be silly.
    key = models.CharField(max_length=40)
    weight = models.FloatField(default=1.0)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'id']
        unique_together = [('tournament', 'key')]

    def __str__(self):
        return f"{self.tournament_id}:{self.key} x{self.weight}"

    def definition(self):
        from . import metrics as catalogue
        return catalogue.get(self.key)


class MatchPlayerStat(models.Model):
    """What one player did in one match, for one metric.

    One row per number rather than a wide table with a column per stat, because
    the set of stats is the organiser's and changes per tournament. A column per
    possible metric would be a migration every time somebody adds a game.

    The player is a user rather than a registration: in a team tournament the
    interesting question is which PERSON was the most valuable, and a
    registration is a team.
    """

    id = models.AutoField(primary_key=True)
    match = models.ForeignKey(
        BracketMatch, on_delete=models.CASCADE, related_name='player_stats')
    player = models.ForeignKey(
        'vent_auth.Users', on_delete=models.CASCADE, related_name='match_stats')
    # Which side they played for, so a stat line can be shown next to the right
    # team without inferring it from team membership, which changes.
    registration = models.ForeignKey(
        TournamentRegistration, on_delete=models.CASCADE, null=True, blank=True,
        related_name='player_stats')
    key = models.CharField(max_length=40)
    value = models.FloatField(default=0)
    recorded_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One number per player per metric per match. Reporting a corrected
        # figure updates the row rather than adding a second one, which is what
        # makes a re-submission safe.
        unique_together = [('match', 'player', 'key')]
        ordering = ['match_id', 'player_id', 'key']

    def __str__(self):
        return f"{self.match_id}:{self.player_id}:{self.key}={self.value}"


class TournamentMVP(models.Model):
    """The award, once somebody has decided it.

    Computed from the stats by default, and the organiser may override with a
    reason. Both are recorded, because "the numbers said X and the organiser
    chose Y" is a fact somebody will ask about, and losing it makes the decision
    look arbitrary when it was not.
    """

    id = models.AutoField(primary_key=True)
    tournament = models.OneToOneField(
        Tournament, on_delete=models.CASCADE, related_name='mvp')
    player = models.ForeignKey(
        'vent_auth.Users', on_delete=models.CASCADE, related_name='mvp_awards')
    # The score the arithmetic gave them, kept as it stood when the award was
    # made. Stats can be corrected afterwards and the award should not silently
    # start disagreeing with itself.
    score = models.FloatField(default=0)
    # Set when the organiser picked somebody the arithmetic did not.
    overridden = models.BooleanField(default=False)
    reason = models.CharField(max_length=300, blank=True, default='')
    decided_by = models.ForeignKey(
        'vent_auth.Users', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='mvp_decisions')
    decided_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"MVP {self.tournament_id}: {self.player_id}"


class ScheduledReminder(models.Model):
    """A reminder the organiser sets now for the platform to send later.

    CEO, 29 August 2026: "organizers should be able to schedule reminders."

    **The time is stored as an anchor plus an offset, not as a timestamp.**
    "An hour before check-in opens" is what an organiser actually means, and it
    is the version that survives them moving the tournament. A fixed timestamp
    computed at save time would quietly point at the wrong moment the first
    time a start date changes, which is the most common edit there is. A fixed
    time is still available for the cases that genuinely are one - "the morning
    of, at 9" - and is stored as such.

    There is no scheduler process on this deployment. Celery is installed and
    no task has ever been defined, so this is driven by a management command
    that cron runs every few minutes:

        python manage.py send_due_reminders

    That is deliberately less machinery than a broker and a worker: the command
    is an ordinary function, it can be unit tested, and running it twice in the
    same minute is harmless because `sent_at` is what decides.
    """

    ANCHOR_CHOICES = [
        ('tournament_start', 'The tournament start'),
        ('check_in_opens', 'Check-in opening'),
        ('check_in_closes', 'Check-in closing'),
        ('fixed', 'A time I choose'),
    ]

    KIND_CHOICES = [
        ('check_in', 'Check in'),
        ('match', 'Your next match'),
        ('custom', 'My own message'),
    ]

    id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name='scheduled_reminders')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES,
                            default='check_in')
    subject = models.CharField(max_length=140, blank=True, default='')
    body = models.TextField(max_length=2000, blank=True, default='')

    anchor = models.CharField(max_length=30, choices=ANCHOR_CHOICES,
                              default='check_in_opens')
    # Minutes BEFORE the anchor. Negative means after, which is the honest way
    # to express "fifteen minutes into check-in" without a second field.
    offset_minutes = models.IntegerField(default=60)
    fixed_at = models.DateTimeField(null=True, blank=True)

    sent_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Why it did not send, when it did not. Recorded rather than logged: an
    # organiser who scheduled six reminders and had the sixth skipped is owed
    # the reason on the screen where they scheduled it.
    skipped_reason = models.CharField(max_length=200, blank=True, default='')
    people_reached = models.PositiveIntegerField(default=0)

    created_by = models.ForeignKey(
        'vent_auth.Users', on_delete=models.SET_NULL, null=True,
        related_name='scheduled_reminders')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.tournament_id}:{self.kind} @ {self.due_at()}"

    def anchor_time(self):
        """The moment this reminder is measured from, or None.

        None when the tournament has no start time, or uses no check-in and the
        anchor needs one. A reminder that cannot be placed on the clock is not
        an error; it simply never comes due, and the screen says so.
        """
        from .options import check_in_state

        if self.anchor == 'fixed':
            return self.fixed_at
        if self.anchor == 'tournament_start':
            return self.tournament.start_date_and_time
        window = check_in_state(self.tournament, timezone.now())
        if window is None:
            return None
        if self.anchor == 'check_in_opens':
            return window.get('opens_at')
        if self.anchor == 'check_in_closes':
            return window.get('closes_at')
        return None

    def due_at(self):
        """When this should go out, computed fresh every time it is asked.

        Fresh on purpose: the organiser moves the date and the reminder moves
        with it, without anybody having to remember to reschedule.
        """
        from datetime import timedelta

        anchor = self.anchor_time()
        if anchor is None:
            return None
        if self.anchor == 'fixed':
            return anchor
        return anchor - timedelta(minutes=self.offset_minutes or 0)

    def is_due(self, now=None):
        if self.sent_at or self.cancelled_at:
            return False
        due = self.due_at()
        return due is not None and due <= (now or timezone.now())


class TournamentInvitation(models.Model):
    """An organiser asking one named player or one named team to enter.

    CEO, 29 August 2026: "tournament organizers, should be able to invite people
    or teams to their events."

    Not the same thing as `TournamentInvite`, which is a code an organiser hands
    out and anybody holding it can spend. This is addressed: it names who it is
    for, it tells them, and it can be accepted or declined. An organiser wanting
    four particular teams in a bracket does not want four codes to distribute
    and reconcile; they want to ask four teams.

    One invitation per tournament per recipient, so asking twice is a reminder
    rather than a second row, and the recipient's list never shows the same
    tournament twice.
    """

    PENDING = 'pending'
    ACCEPTED = 'accepted'
    DECLINED = 'declined'
    WITHDRAWN = 'withdrawn'
    STATUS_CHOICES = [
        (PENDING, 'Waiting for an answer'),
        (ACCEPTED, 'Accepted'),
        (DECLINED, 'Declined'),
        (WITHDRAWN, 'Withdrawn by the organiser'),
    ]

    id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name='invitations')

    # Exactly one of these. A team invitation is answered by whoever owns the
    # team, and a player invitation by that player.
    user = models.ForeignKey(
        Users, on_delete=models.CASCADE, null=True, blank=True,
        related_name='tournament_invitations')
    team = models.ForeignKey(
        Teams, on_delete=models.CASCADE, null=True, blank=True,
        related_name='tournament_invitations')

    #: Somebody who may not have an account yet.
    #:
    #: CEO, 3 September 2026: "lets be able to invite through email also". An
    #: organiser's list of who they want is a list of email addresses, and
    #: half of those people have never heard of V-ENT. Requiring a username
    #: first means the organiser has to chase every one of them to sign up
    #: before they can even be asked.
    #:
    #: Stored lowercase. If it turns out to belong to an account the row is
    #: bound to that account instead, so it lands in their invitations in the
    #: app rather than only in a mailbox they may not read.
    email = models.EmailField(blank=True, default='')

    message = models.CharField(max_length=280, blank=True, default='')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)

    invited_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tournament_invitations_sent')
    created_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tournament', 'user'],
                condition=models.Q(user__isnull=False),
                name='one_invitation_per_player'),
            models.UniqueConstraint(
                fields=['tournament', 'team'],
                condition=models.Q(team__isnull=False),
                name='one_invitation_per_team'),
            models.UniqueConstraint(
                fields=['tournament', 'email'],
                condition=~models.Q(email=''),
                name='one_invitation_per_email'),
        ]

    def __str__(self):
        who = self.team.team_name if self.team_id else (
            self.user.username if self.user_id else (self.email or '?'))
        return 'invitation to %s for tournament %s' % (who, self.tournament_id)


class TournamentOverlay(models.Model):
    """An HTML overlay an organiser uploaded, and the URL they paste into OBS.

    CEO, 29 August 2026: "if users can upload html files, we should be able to
    get links to paste inside obs or vmix or any streaming software of choice."

    Which decides almost everything about this model:

    **The token is the credential.** OBS opens a browser source with no session,
    no cookie and no header. Whatever authorises it has to be in the URL, so it
    is a long random token and the URL is treated as a secret. A rotate is a new
    token, which is why it lives in a column rather than being derived from the
    id.

    **The file is stored, not the markup.** An overlay is a designer's file and
    is edited by re-uploading it. Keeping it as a file means the original is
    what is served, which is the only version anybody can debug against.

    **Nothing about it is private.** It shows the same standings as the public
    tournament page, to a camera pointed at a screen in a hall. The token exists
    so that an overlay cannot be enumerated and hotlinked, not because the
    contents are secret.
    """

    id = models.AutoField(primary_key=True)
    # What this overlay belongs to. Exactly one of the two.
    #
    # It was tournament-only, so an organiser running an EVENT had nowhere to
    # upload a design and no URL to paste into OBS - the same shape of gap as
    # short links, and the reason `tools/check-parity.py` has a row for it. An
    # event has a programme, a door count and sponsors worth putting on a
    # screen just as a tournament has a bracket.
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, null=True, blank=True, related_name='overlays')
    event = models.ForeignKey(
        'vent_event.Event', on_delete=models.CASCADE,
        null=True, blank=True, related_name='overlays')
    name = models.CharField(max_length=120)
    file = models.FileField(upload_to='tournament_overlays/')

    # What goes in the URL. Long, random, and rotatable: a URL pasted into a
    # machine at a venue and forgotten is a URL that has to be revocable.
    token = models.CharField(max_length=48, unique=True, db_index=True)

    # What the uploader's file turned out to be, worked out once at upload
    # rather than on every request from OBS. See `overlay_binding.inspect`.
    binding = models.CharField(max_length=20, default='none')
    bound_fields = models.JSONField(default=list, blank=True)

    # Where it sits on the frame, in the SAME nine places and the same pixel
    # nudge a V-ENT graphic uses. See `presentation.py`, which is the one list.
    #
    # CEO, 4 September 2026: "should be able to change position even for the
    # overlays you upload". I had said an uploaded file is moved by editing its
    # own CSS, which is true and is not an answer: the person holding the file
    # at a venue is an operator, not its designer.
    #
    # `as_designed` is the default and it means the file is served byte for
    # byte as it was uploaded, with nothing injected to move it. That is not a
    # convenience: an overlay already pasted into a machine must not move
    # because this column appeared.
    options = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tournament_overlays')

    @property
    def owner(self):
        """Whatever this overlay is for, of whichever kind."""
        return self.tournament or self.event

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return '%s for tournament %s' % (self.name, self.tournament_id)

    def save(self, *args, **kwargs):
        if not self.token:
            import secrets
            self.token = secrets.token_urlsafe(24)[:48]
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = list(
                    set(kwargs['update_fields']) | {'token'})
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# The production studio
# ---------------------------------------------------------------------------
#
# CEO, 1 September 2026: "the site's tournament bracket systems will handle the
# calculations and seeding and feed it into the production studio based off what
# is being requested for each element, and each element can be copied and pasted
# into your streaming software as browser sources and it updates in realtime...
# it'll be like a production studio for any organizer who can pay for it."
#
# So this is not a feature of one event. It is a capability an organiser rents.
#
# The difference from `TournamentOverlay`, which already exists: that one serves
# a designer's uploaded HTML file. This one serves elements V-ENT itself ships,
# bound to the bracket, driven by an operator during a broadcast. Both keep
# working; they answer different questions. An organiser with a designer uses
# the first. An organiser who wants a scoreboard in ten minutes uses this.

class BroadcastSession(models.Model):
    """One broadcast. A stream day, usually.

    A session rather than a bare tournament because a three day event is three
    broadcasts with three sets of graphics on screen, and because ending a
    session is how an operator clears everything at once without hunting for
    what is still showing.

    **The token is the credential.** OBS opens a browser source with no session,
    no cookie and no header, so whatever authorises an element URL has to be in
    the URL. It is per session, which means last week's URLs stop working when
    the session ends, and a leaked URL costs one broadcast rather than the
    tournament.
    """

    STATUS = [('live', 'Live'), ('ended', 'Ended')]

    id = models.AutoField(primary_key=True)
    # What this broadcast is of. Exactly one of the two, the same shape as
    # TournamentOverlay. It was tournament-only, so an organiser streaming an
    # EVENT had the upload-your-own overlays and none of the studio: no
    # console, no now-and-next, no sponsor wall, no doors count. The audit of
    # 2 September recorded it as the gap the parity checker had no row for.
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, null=True, blank=True,
        related_name='broadcast_sessions')
    event = models.ForeignKey(
        'vent_event.Event', on_delete=models.CASCADE, null=True, blank=True,
        related_name='broadcast_sessions')
    name = models.CharField(max_length=120, blank=True, default='')
    token = models.CharField(max_length=48, unique=True, db_index=True)
    status = models.CharField(max_length=10, choices=STATUS, default='live')

    # The house style for this broadcast: how graphics arrive, how they leave,
    # whether a surface stays behind. Any one graphic may differ; see
    # `presentation.resolve`.
    defaults = models.JSONField(default=dict, blank=True)

    # Which LOOK the graphics are drawn in.
    #
    # V-ENT's own is the default and is what an organiser with no design of
    # their own gets. `rivalry` is the CADE Rivalry Series pack: a finished
    # broadcast design that already existed, approved before the event, with
    # its own typefaces and its own artwork behind two of the cards.
    #
    # A look, not a fork. Every graphic is the same component reading the same
    # feed, and only the drawing changes, so a fix to what a card SAYS reaches
    # both looks and cannot drift apart. CEO, 4 September 2026: "the design you
    # were doing did not match the original design."
    THEMES = [('vent', 'V-ENT'), ('rivalry', 'CADE Rivalry Series')]
    theme = models.CharField(max_length=16, choices=THEMES, default='vent')

    started_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='broadcast_sessions')
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return '%s (%s)' % (self.name or 'Broadcast', self.owner_ref)

    @property
    def owner(self):
        """Whatever this broadcast is of, of whichever kind."""
        return self.tournament or self.event

    @property
    def kind(self):
        return 'event' if self.event_id else 'tournament'

    @property
    def owner_ref(self):
        return self.event_id if self.event_id else self.tournament_id

    @property
    def is_live(self):
        return self.status == 'live'

    def save(self, *args, **kwargs):
        if not self.token:
            import secrets
            self.token = secrets.token_urlsafe(24)[:48]
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = list(
                    set(kwargs['update_fields']) | {'token'})
        super().save(*args, **kwargs)


class BroadcastElement(models.Model):
    """One graphic in one session, and whether it is on screen.

    The operator's whole job is turning these on and off and correcting what
    they say, so that is exactly what the row holds: a kind, a payload, and
    whether it is showing.

    **State lives here, not in the browser source.** An element page is a dumb
    renderer that asks "what should I be showing" a few times a second. That is
    what makes it survivable: OBS can be restarted mid-broadcast, the machine
    can be swapped, a second operator can open the same URL on another laptop,
    and the graphic comes back exactly as it was. A design that kept state in
    the page would lose the broadcast with the tab.

    One row per kind per session, because two scorebars is not a thing anybody
    wants and letting it happen is how a stale one ends up on air.
    """

    # What V-ENT ships. Adding to this list is how the studio grows, and each
    # one is a page under /studio/<token>/<kind>. Which kinds a broadcast may
    # use depends on what it is of: a tournament has a bracket, an event has a
    # programme. `kinds_for()` is the one place that says which.
    TOURNAMENT_KINDS = [
        ('scorebar', 'Score bar'),
        ('standings', 'Standings table'),
        ('lower_third', 'Lower third'),
        ('player_card', 'Player card'),
        ('bracket', 'Bracket'),
        ('sponsors', 'Sponsor wall'),
        ('media', 'Clip or picture'),
        ('squad_depth', 'Squad depth'),
        ('ticker', 'Ticker'),
        ('intro', 'Intro'),
        ('outro', 'Outro'),
        # The Rivalry Series set, from the STREAM ELEMENTS tab of the CEO's own
        # event flow. Every one of them is about a FIXTURE rather than a match,
        # which is the thing this platform could not draw before: an aggregate
        # tie is two matches and one result, and a graphic that knows only about
        # matches tells the audience the wrong story on the second whistle.
        ('fixture_card', 'Fixture card'),
        ('fixture_result', 'Fixture result'),
        ('match_result', 'Match result'),
        ('head_to_head', 'Head to head'),
        ('break_screen', 'Break screen'),
        # Already an event kind below, and the same graphic reading the same
        # document. A run of show hangs off a tournament or an event, so the
        # element does too. It is listed in both places and appears once in
        # KINDS, because the column's choices are a set of values rather than a
        # union of two menus.
        ('now_next', 'Now and next'),
        ('award', 'Award'),
        ('explainer', 'Explainer'),
        # The rest of the STREAM ELEMENTS sheet, added 4 September after the
        # CEO named the seven that matter for this broadcast. Two of them are
        # frames rather than cards: the camera or the game sits inside, so the
        # hole in the middle stays transparent and its position is the whole
        # measurement.
        ('desk_lower_third', 'Desk lower third'),
        ('matchday', 'Matchday card'),
        ('analyst_desk', 'Analyst desk frame'),
        ('play_area', 'Play area frame'),
    ]
    EVENT_KINDS = [
        ('now_next', 'Now and next'),
        ('programme', 'Programme'),
        ('lower_third', 'Lower third'),
        ('sponsors', 'Sponsor wall'),
        ('doors', 'Doors'),
        ('media', 'Clip or picture'),
        ('ticker', 'Ticker'),
        ('intro', 'Intro'),
        ('outro', 'Outro'),
        # A desk and a camera frame belong to whoever is broadcasting, and an
        # event has both as often as a tournament does. Built for one side and
        # forgotten on the other is the fault the parity checker exists for.
        # `matchday` is not here: it draws a day of aggregate fixtures, which
        # only a tournament has.
        ('desk_lower_third', 'Desk lower third'),
        ('analyst_desk', 'Analyst desk frame'),
        ('play_area', 'Play area frame'),
    ]
    # The column's choices: every kind either side may use. Written out rather
    # than computed, because a class body cannot see its own names from inside
    # a comprehension.
    #
    # `now_next` is NOT repeated here any more: it moved into TOURNAMENT_KINDS
    # above, and a value listed twice would offer the same choice twice in the
    # admin and read as two different graphics to anybody scanning the list.
    KINDS = TOURNAMENT_KINDS + [
        ('programme', 'Programme'),
        ('doors', 'Doors'),
    ]

    # Graphics that draw a clip or a picture the organiser uploaded, rather
    # than data the platform computes.
    MEDIA_KINDS = ['media']

    @classmethod
    def kinds_for(cls, kind_of_owner):
        return cls.EVENT_KINDS if kind_of_owner == 'event' else cls.TOURNAMENT_KINDS

    id = models.AutoField(primary_key=True)
    session = models.ForeignKey(
        BroadcastSession, on_delete=models.CASCADE, related_name='elements')
    kind = models.CharField(max_length=20, choices=KINDS)

    # What the operator typed or picked: which fixture, which player, a caption.
    # Free-form per kind, because a lower third and a standings table have
    # nothing in common and a column per field would be a hundred empty columns.
    payload = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('session', 'kind')
        ordering = ['kind']

    def __str__(self):
        return '%s %s' % (self.kind, 'on' if self.is_active else 'off')


# ---------------------------------------------------------------------------
# Who may enter results
# ---------------------------------------------------------------------------

class TournamentStaff(models.Model):
    """Somebody the organiser has let record results for one tournament.

    CEO, 3 September 2026: "only those given the access to, should be able to"
    input results. A scorekeeper is named by username by the organiser, may
    record a knockout score or a league fixture on this tournament, and may do
    nothing else: not edit the tournament, not run the studio, not add another
    scorekeeper. Removing the row revokes it at once. Mirrors EventManager's
    door staff, which is the same idea for a ticket desk.

    A row per tournament rather than a platform role, because the person who
    keeps score at one league is a stranger to every other.
    """

    ROLE_CHOICES = [
        ('scorekeeper', 'Scorekeeper'),
    ]

    id = models.AutoField(primary_key=True)
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name='staff')
    user = models.ForeignKey(
        Users, on_delete=models.CASCADE, related_name='tournament_staff_roles')
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default='scorekeeper')
    added_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tournament_staff_added')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tournament', 'user')
        ordering = ['user__username']

    def __str__(self):
        return '%s on %s (%s)' % (self.user.username, self.tournament_id, self.role)


class StudioAsset(models.Model):
    """A clip or a picture kept in the studio, to be called on whenever.

    CEO, 3 September 2026: "i want to be able use player brolls on the site if
    possible maybe the videos are uploaded to a place in the studio and then
    can be called on whenever, same for other videos or images that can be
    uploaded and then linked to differnet things like mayabe particular teams
    or players, or texts or IDS etc, then when those things are needed, can be
    triggered into a live overlay."

    So an asset is uploaded once, to a tournament's or an event's studio, and
    is then addressable three ways: by its own id, by a tag the organiser gave
    it, or by what it is about. A b-roll of a player is tagged with that
    player; a team's walk-on is tagged with the team; a sting is tagged with a
    word the operator will remember at 9pm with a match starting.

    `tags` is free text on purpose. An operator under time pressure types what
    they think of, not what a schema anticipated, and the alternative is a
    dropdown of somebody else's categories.
    """

    KINDS = [('video', 'Video'), ('image', 'Image'), ('font', 'Font')]

    id = models.AutoField(primary_key=True)
    # What studio it belongs to. Exactly one, the same shape as the broadcast
    # session and the overlay.
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, null=True, blank=True,
        related_name='studio_assets')
    event = models.ForeignKey(
        'vent_event.Event', on_delete=models.CASCADE, null=True, blank=True,
        related_name='studio_assets')

    kind = models.CharField(max_length=8, choices=KINDS)
    name = models.CharField(max_length=140)
    file = models.FileField(upload_to='studio_assets/')
    size_bytes = models.BigIntegerField(default=0)

    # How long a clip runs, so the console can take it off air by itself
    # rather than leaving a finished video frozen on a last frame.
    duration_ms = models.PositiveIntegerField(default=0)

    # The name this asset fills inside an uploaded overlay.
    #
    # CEO, 3 September 2026: "should still be able to upload images and media
    # that they want to be used and assign them to names or text or areas
    # inside the overlays so those medias are pulled and shown inside the
    # overlay when the overlays are triggered."
    #
    # A designer marks a slot in their HTML with `data-vent-src="asset.hero"`,
    # and whatever is assigned to `hero` here is what appears there. One word,
    # because it is typed into an attribute by hand.
    slot = models.CharField(max_length=40, blank=True, default='', db_index=True)

    # What it is about. Any of these may be empty; an asset with none is still
    # perfectly usable by name.
    tags = models.JSONField(default=list, blank=True)
    team_tag = models.CharField(max_length=40, blank=True, default='')
    player = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='studio_assets')

    uploaded_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='studio_assets_uploaded')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return '%s (%s)' % (self.name, self.kind)

    @property
    def owner(self):
        return self.tournament or self.event

    @property
    def owner_kind(self):
        return 'event' if self.event_id else 'tournament'

    def matches(self, word):
        """Whether this asset answers to `word`: its tag, team or player."""
        needle = str(word or '').strip().lower()
        if not needle:
            return False
        if needle == (self.team_tag or '').lower():
            return True
        if self.player_id and needle == (self.player.username or '').lower():
            return True
        return any(needle == str(t).strip().lower() for t in (self.tags or []))


# ---------------------------------------------------------------------------
# The run of show
# ---------------------------------------------------------------------------
#
# CEO, 4 September 2026, the morning of the Rivalry Series Season 2 production:
# "for the programe flow for an event, i want to be able to create something
# that will appear with really good ui for mobile, that will show the necessary
# info to someone looking at it on the website, this is the current event flow,
# i also wnat to be able to share the event flow to people, can decide to make
# it public or not."
#
# This is NOT `EventSession`, which is already called the Programme. That one
# answers "what is on, where in the venue, and does the room hold me", for
# somebody deciding whether to come. It carries a capacity because a panel room
# holds eighty when the venue holds nine hundred.
#
# A run of show answers a different question and for different people: at 13:39
# what is on screen, who is driving it, and what comes next. It is minute by
# minute, it names the person or desk responsible for each cue, and on the day
# it is the only document anybody reads. The CEO's own sheet has seventy nine
# rows across two days and a column called OWNS IT.
#
# Kept apart from EventSession deliberately. Merging them would put a capacity
# on "RESULT CARD, GFX, three minutes" and an owner on "Cosplay parade", and
# then neither list would be usable for its own job.
#
# It hangs off a tournament OR an event, exactly like TournamentOverlay and
# BroadcastSession above, and for the same reason: V-ENT runs two kinds of
# thing and a document built for one of them is a feature half the platform
# does not have.

class RunSheet(models.Model):
    """One run of show, belonging to a tournament or an event.

    **Private by default, and that is not a detail.** A run sheet carries staff
    names, when the money is counted, when a celebrity arrives and which
    segments are not confirmed. Publishing it by accident is worse than not
    having it. Three states rather than a boolean, because the middle one is
    what an organiser actually wants most days:

    | | |
    |---|---|
    | `private` | the organiser and whoever may run production. Nobody else |
    | `link` | anybody holding the address. Not listed, not indexed |
    | `public` | on the event's page and in the sitemap |

    **The address is a token, never the id.** Same reason as everywhere else on
    this platform: sequential ids in a URL let anybody walk the table by
    counting, and a run sheet is exactly the kind of document somebody would
    walk for.
    """

    PRIVATE = 'private'
    LINK = 'link'
    PUBLIC = 'public'
    VISIBILITY = (
        (PRIVATE, 'Only me and my team'),
        (LINK, 'Anybody with the link'),
        (PUBLIC, 'On the event page, and findable'),
    )

    id = models.AutoField(primary_key=True)
    # Exactly one of the two.
    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, null=True, blank=True,
        related_name='run_sheets')
    event = models.ForeignKey(
        'vent_event.Event', on_delete=models.CASCADE, null=True, blank=True,
        related_name='run_sheets')

    name = models.CharField(max_length=140, blank=True, default='')
    # A sentence under the title, for whatever the sheet's own header said.
    subtitle = models.CharField(max_length=240, blank=True, default='')

    token = models.CharField(max_length=48, unique=True, db_index=True)
    visibility = models.CharField(max_length=8, choices=VISIBILITY,
                                  default=PRIVATE)

    # Which clock the sheet is written on. The platform stores everything in
    # UTC and every other screen converts to the reader's own zone, which is
    # right for a ticket and wrong for this: a run sheet says 13:39 because
    # that is what the clock on the wall of the venue will say, and a caster in
    # London reading 12:39 has been told the wrong thing. So the times are the
    # venue's, the zone is named here, and NOW is worked out against it.
    time_zone = models.CharField(max_length=64, default='Africa/Lagos')

    # Whether a reader who is not staff sees the OWNS IT column. An organiser
    # may want the timings public and the crew private, which is the common
    # case for anything with named talent on it.
    show_owners = models.BooleanField(default=True)
    # Same question for the NOTE column, which is where "six legal names are
    # still not held" ends up.
    show_notes = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        Users, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='run_sheets')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def owner(self):
        return self.tournament or self.event

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name or ('Run of show %s' % self.id)

    def save(self, *args, **kwargs):
        if not self.token:
            import secrets
            self.token = secrets.token_urlsafe(18)[:48]
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = list(
                    set(kwargs['update_fields']) | {'token'})
        super().save(*args, **kwargs)


class RunSheetDay(models.Model):
    """One day of the run of show. A tab in the organiser's spreadsheet.

    The date is optional on purpose. A sheet gets written before the dates are
    fixed, and refusing to hold "Day 1" until somebody commits to a Friday is
    refusing to hold the thing people actually have.

    Without a date the times are still times, they are just not moments. That
    is enough to read a running order and not enough to say what is on NOW,
    and the screen says which of those it is doing rather than guessing.
    """

    id = models.AutoField(primary_key=True)
    sheet = models.ForeignKey(RunSheet, on_delete=models.CASCADE,
                              related_name='days')
    label = models.CharField(max_length=80)
    date = models.DateField(null=True, blank=True)
    # "doors 10:00 to 18:00", or whatever the sheet's own header row said.
    note = models.CharField(max_length=240, blank=True, default='')
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return '%s (%s)' % (self.label, self.sheet_id)


class RunSheetItem(models.Model):
    """One cue.

    The columns are the CEO's own: PHASE, ACTIVITY, OWNS IT, MATCH, STARTS,
    ENDS, MINS. Nothing here invents a vocabulary that the people who wrote the
    sheet would then have to translate into.

    **Times are times, not datetimes.** A run sheet is written as 13:39, and the
    day it belongs to is the day it is written under. Storing a datetime would
    force a timezone decision at write time on a document whose author is
    standing in the venue, and a sheet shifted by an hour is worse than useless.
    The moment is built at read time from the day's date plus the time, in the
    event's own zone.

    **`minutes` is stored, not derived.** Every row in the CEO's sheet carries
    it, some rows have a duration and no clock time yet, and a derived value
    disagrees with the printed sheet the moment somebody nudges a start time.
    What is on the sheet is what is on the screen.
    """

    id = models.AutoField(primary_key=True)
    day = models.ForeignKey(RunSheetDay, on_delete=models.CASCADE,
                            related_name='items')

    # The band a run of show is read in: STREAM STARTS, MATCHES ONGOING, BREAK,
    # DAY CLOSE. Blank means it continues the band above, which is how a
    # spreadsheet with merged cells actually reads.
    phase = models.CharField(max_length=80, blank=True, default='')
    activity = models.CharField(max_length=400)
    # "GFX", "Casters / GFX", "Analyst desk". Free text because it is a desk or
    # a person, and a fixed list would be wrong at the first production.
    owner = models.CharField(max_length=120, blank=True, default='')
    # "NGA1 v GHA1". Only some cues belong to a match.
    match = models.CharField(max_length=120, blank=True, default='')

    starts_at = models.TimeField(null=True, blank=True)
    ends_at = models.TimeField(null=True, blank=True)
    minutes = models.DecimalField(max_digits=6, decimal_places=1,
                                  null=True, blank=True)

    note = models.TextField(blank=True, default='')
    # The sheet's own convention: red bold means scheduled and costed, not
    # booked. Something that is not confirmed is the single most useful thing
    # to see on a run sheet, so it is a column rather than a colour.
    is_confirmed = models.BooleanField(default=True)

    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return '%s %s' % (self.starts_at or '', self.activity[:40])


# ---------------------------------------------------------------------------
# Text on top of an overlay
# ---------------------------------------------------------------------------

from . import text_layers as _layer_rules


class OverlayLayer(models.Model):
    """Something an operator put on top of one overlay, and how it arrives.

    Two asks, one model, because they are one thing:

    CEO, 4 September 2026, inbox row 52: "also should be able to add text,
    change the font size, color, position, animation of that text also on any
    overlay".

    And row 51: "there should be elements you can add or ways to add certan
    uploaded things like images, sponsor logos, player images or videos as like
    elements that will then be movable inside an element once they are loaded".

    A caption and a sponsor logo differ in what is drawn and in nothing else:
    both sit somewhere on the frame, both arrive and leave, both are ordered
    against each other, both are switched off without being deleted. So `kind`
    decides what is painted and every other column is shared. A second table
    would have been the same feature built twice, and the second one would have
    been the one missing the delay the first grew a week later.

    It was called `OverlayLayer` for the few hours between the two asks.

    **It hangs off either kind, which is the point.** V-ENT has a graphic the
    platform draws (`BroadcastElement`) and an HTML file an organiser designed
    themselves (`TournamentOverlay`), and "any overlay" means both. One model
    with two nullable owners rather than two tables, for the reason the one
    model per thing rule gives: a second table is a feature that gets built for
    half the product and nobody notices which half until somebody asks why
    their overlay is different.

    **Exactly one owner.** A row with both would be drawn twice and a row with
    neither is a row nothing can reach. Held in `save()` as well as at the API,
    because no route can express either: the address names the owner.

    Every measurement is in pixels at 1920x1080, the raster the rest of the
    studio uses. What the layer may say, what a colour is and what the ranges
    are all live in `text_layers.py`, so the API and the model cannot disagree.
    """

    id = models.AutoField(primary_key=True)

    # What this layer paints. Words, or something out of the studio's own media
    # library: an image, a sponsor's logo, a player's photograph, a clip.
    #
    # The library is the only source on purpose. An operator pointing a layer
    # at a URL somewhere else is an overlay that breaks when that host does,
    # six hours into a broadcast, with nobody able to see why.
    kind = models.CharField(max_length=8, choices=_layer_rules.KINDS,
                            default=_layer_rules.DEFAULTS['kind'])

    # Which piece of media, for an asset layer. Nulled rather than kept when the
    # asset is deleted, so a layer never points at something that is gone: the
    # runtime then draws nothing rather than a broken image on air.
    asset = models.ForeignKey(
        'StudioAsset', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='layers')

    # How wide it is drawn, in pixels at 1920x1080. Height follows the media's
    # own proportions, because an operator stretching a sponsor's logo is a
    # conversation nobody wants to have. 0 means the media's natural size.
    #
    # Nothing here ever draws an asset ABOVE its natural size: the runtime caps
    # it, for the reason the whole platform caps it, which is that no filter
    # adds detail a file never had.
    width_px = models.SmallIntegerField(
        default=_layer_rules.DEFAULTS['width_px'])

    # A layer on a studio graphic V-ENT draws.
    element = models.ForeignKey(
        BroadcastElement, on_delete=models.CASCADE, null=True, blank=True,
        related_name='text_layers')
    # A layer on an HTML file the organiser uploaded.
    overlay = models.ForeignKey(
        TournamentOverlay, on_delete=models.CASCADE, null=True, blank=True,
        related_name='text_layers')

    # The words. Blank when `field` is doing the talking.
    text = models.CharField(max_length=_layer_rules.TEXT_MAX, blank=True,
                            default=_layer_rules.DEFAULTS['text'])
    # A path into the feed, e.g. `tournament.title`. When it is set it wins, and
    # `text` is what is drawn if the path resolves to nothing. That fallback is
    # the whole reason both columns exist: a caption bound to a fixture that has
    # not been picked yet must say something rather than leave a hole.
    field = models.CharField(max_length=_layer_rules.FIELD_MAX, blank=True,
                             default=_layer_rules.DEFAULTS['field'])

    font_size = models.SmallIntegerField(
        default=_layer_rules.DEFAULTS['font_size'])
    # `#RRGGBB` or `#RRGGBBAA`, validated and never defaulted. A broadcast
    # graphic carries the client's brand over live video and the operator is the
    # person who knows what the sponsor's red is; see text_layers.py.
    colour = models.CharField(max_length=9,
                              default=_layer_rules.DEFAULTS['colour'])
    family = models.CharField(max_length=12, choices=_layer_rules.FAMILIES,
                              default=_layer_rules.DEFAULTS['family'])
    # The slot of a font the organiser uploaded to the studio, which wins over
    # `family` when it is set. The runtime already writes an `@font-face` block
    # naming each slot, so a designer writes `font-family: 'hero'` and the
    # organiser decides later what hero is.
    font_slot = models.CharField(max_length=40, blank=True, db_index=False,
                                 default=_layer_rules.DEFAULTS['font_slot'])
    weight = models.SmallIntegerField(choices=_layer_rules.WEIGHTS,
                                      default=_layer_rules.DEFAULTS['weight'])
    align = models.CharField(max_length=8, choices=_layer_rules.ALIGNMENTS,
                             default=_layer_rules.DEFAULTS['align'])

    # Where it sits, out of `presentation.POSITIONS`, and a nudge off that
    # anchor within `presentation.OFFSET_LIMIT`.
    #
    # No `choices` on these three on purpose. The list lives in presentation.py
    # and is shared with every graphic; copying it here would put a second copy
    # in a migration, and adding a position would then need a migration to a
    # column that stores a string either way.
    position = models.CharField(max_length=16,
                                default=_layer_rules.DEFAULTS['position'])
    offset_x = models.IntegerField(default=_layer_rules.DEFAULTS['offset_x'])
    offset_y = models.IntegerField(default=_layer_rules.DEFAULTS['offset_y'])

    entry = models.CharField(max_length=12,
                             default=_layer_rules.DEFAULTS['entry'])
    exit = models.CharField(max_length=12,
                            default=_layer_rules.DEFAULTS['exit'])

    # So two layers can arrive one after the other. `duration_ms` of 0 means it
    # stays until the graphic goes, the same word `presentation.DEFAULTS` uses.
    delay_ms = models.IntegerField(default=_layer_rules.DEFAULTS['delay_ms'])
    duration_ms = models.IntegerField(
        default=_layer_rules.DEFAULTS['duration_ms'])

    # Paint order and z, low first.
    order = models.SmallIntegerField(default=_layer_rules.DEFAULTS['order'])
    # Switched off without being deleted, which is what an operator does mid
    # show. A deleted layer has to be retyped at the worst possible moment.
    is_active = models.BooleanField(
        default=_layer_rules.DEFAULTS['is_active'])

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return '%s on %s' % (self.text[:24] or self.field or 'text',
                             'element %s' % self.element_id if self.element_id
                             else 'overlay %s' % self.overlay_id)

    @property
    def owner(self):
        """Whichever overlay this layer is on."""
        return self.element or self.overlay

    def save(self, *args, **kwargs):
        if bool(self.element_id) == bool(self.overlay_id):
            raise ValueError(
                'A text layer belongs to exactly one overlay: '
                'an element or an uploaded file, never both and never neither.')
        super().save(*args, **kwargs)


class BroadcastSlot(models.Model):
    """A fixed place on the frame, and whatever is currently in it.

    CEO, 6 September 2026, sending the RIVALRY control room and saying V-ENT
    should do "this kind of setup for production, except that this one will be
    online and people can upload anything they want and use to run overlays
    that will be updating in realtime based off the tournament data", then on
    7 September: "BUILD IT PROPERLY."

    ## The gap this closes

    Almost all of it already existed: uploaded HTML with an OBS token URL,
    position and nudge, media and text layers, an asset library, fonts, and a
    feed already carrying standings, teams, players, live scores, the aggregate
    and the run of show.

    What was missing is the SLOT. V-ENT gave one URL per graphic, so a
    broadcast using twenty kinds meant twenty browser sources, each added and
    removed by hand. The RIVALRY control room gives its crew FOUR fixed sources
    - `/s/full`, `/s/lower`, `/s/bug`, `/s/bg` - and a panel decides what
    occupies each. Nobody adds a browser source during a show, so the second
    design is the one that survives contact with a live gallery.

    ## Why four, and why these four

    Taken from the reference the CEO sent rather than invented, and they are
    four because they are the four LAYERS a broadcast composites, in order:

        bg      behind everything. A scene, a holding card, a break loop.
        full    a full-frame graphic: standings, bracket, head to head.
        lower   the band across the bottom: a name, a caption, a now-and-next.
        bug     the small persistent corner: the score bar, a ticker.

    An operator stacks them once in OBS, bottom to top, and never touches the
    sources again. Adding a fifth role later is a row in ROLES and a new URL;
    it does not disturb the four already pasted into somebody's scene.

    ## What may occupy one

    Either of the two things this studio can draw, which is the other half of
    what was asked:

      - one of V-ENT's own graphics, by `item_kind` (any BroadcastElement kind)
      - an overlay somebody UPLOADED, by `overlay`

    So "people can upload anything they want and use to run overlays" and the
    house graphics share one mechanism, one URL per role, and one feed. A slot
    holding an uploaded file still updates in real time, because the file is
    bound to the same feed every V-ENT graphic reads.

    ## The address never changes

    That is the entire point. `active` and what occupies the slot move as often
    as the operator likes; the URL pasted into OBS is written once per session
    and is stable until the session ends. State lives here rather than in the
    browser source, so OBS can be restarted mid-show and the frame comes back
    exactly as it was, which is the rule every element page already follows.
    """

    ROLES = [
        ('bg', 'Background'),
        ('full', 'Full frame'),
        ('lower', 'Lower third'),
        ('bug', 'Corner bug'),
    ]

    id = models.AutoField(primary_key=True)
    session = models.ForeignKey(BroadcastSession, on_delete=models.CASCADE,
                                related_name='slots')
    role = models.CharField(max_length=10, choices=ROLES)

    # One of V-ENT's own graphics. Not a FK to BroadcastElement: the element row
    # holds the PAYLOAD for a kind in this session, and a slot points at the
    # kind. That keeps "what is in the lower third" and "what does the lower
    # third say" as separate decisions, which is how an operator actually works:
    # they cue a graphic, then correct its text, and neither should disturb the
    # other.
    item_kind = models.CharField(max_length=32, blank=True, default='')

    # Or a file somebody uploaded. Exactly one of the two is set; both empty
    # means the slot is holding nothing, which is a normal resting state and
    # not an error.
    overlay = models.ForeignKey('TournamentOverlay', on_delete=models.SET_NULL,
                                null=True, blank=True, related_name='slots')

    # Whether this layer is on air. Separate from what occupies it, so an
    # operator can load the next graphic into a slot while it is dark and take
    # it up when the moment comes, which is the whole job in a gallery.
    active = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['session_id', 'role']
        constraints = [
            models.UniqueConstraint(fields=['session', 'role'],
                                    name='one_slot_per_role_per_session'),
        ]

    def save(self, *args, **kwargs):
        """Keep `updated_at` honest when only some fields are written.

        Same trap as `vent_event.Ticket`: `auto_now` is applied in `pre_save`,
        but a save carrying `update_fields` writes ONLY the named columns, so
        the new stamp is computed and dropped. The feed's version stamp is
        built from these, so losing it means a slot changes and no browser
        source ever notices.
        """
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            fields = set(update_fields)
            fields.add('updated_at')
            kwargs['update_fields'] = tuple(fields)
        return super().save(*args, **kwargs)

    @property
    def holds(self):
        """What is in it: 'element', 'overlay' or '' for nothing."""
        if self.overlay_id:
            return 'overlay'
        if self.item_kind:
            return 'element'
        return ''

    def __str__(self):
        return '%s/%s -> %s' % (self.session_id, self.role, self.holds or 'empty')
