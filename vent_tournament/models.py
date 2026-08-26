from django.db import models
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
    game_mode = models.CharField(max_length=50, null=True, blank=True)  # Game Mode
    tournament_logo = models.ImageField(upload_to='tournament_logos/', null=True, blank=True)
    tournament_banner = models.ImageField(upload_to='tournament_banners/', null=True, blank=True)
    tournament_description = models.TextField(null=True)
    tournament_rules = models.TextField(null=True, blank=True)
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
        if not self.slug:
            from vent_auth.slugs import build_slug
            self.slug = build_slug(
                self.tournament_title, model=type(self), instance_pk=self.pk, pk_field='pk',
            )
        super().save(*args, **kwargs)


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

