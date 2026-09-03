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
        ]

    def __str__(self):
        participant = self.team.team_name if self.team else self.user.username
        return f"{participant} @ {self.tournament.tournament_title}"


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
            if reg.user_id and reg.user_id == user.user_id:
                return slot
            if reg.team_id and reg.team.team_owner_id == user.user_id:
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
        ]

    def __str__(self):
        who = self.team.team_name if self.team_id else (
            self.user.username if self.user_id else '?')
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
        ('ticker', 'Ticker'),
        ('intro', 'Intro'),
        ('outro', 'Outro'),
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
    ]
    # The column's choices: every kind either side may use. Written out rather
    # than computed, because a class body cannot see its own names from inside
    # a comprehension.
    KINDS = TOURNAMENT_KINDS + [
        ('now_next', 'Now and next'),
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

    KINDS = [('video', 'Video'), ('image', 'Image')]

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
