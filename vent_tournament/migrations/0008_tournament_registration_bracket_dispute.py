from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('vent_tournament', '0007_rename_unconfirmedteams_pk'),
        ('vent_auth', '0017_phase1_wallet_admin_models'),
    ]

    operations = [
        # TournamentRegistration
        migrations.CreateModel(
            name='TournamentRegistration',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('confirmed', 'Confirmed'),
                        ('disqualified', 'Disqualified'),
                        ('withdrawn', 'Withdrawn'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('registered_at', models.DateTimeField(auto_now_add=True)),
                ('entry_fee_paid', models.BooleanField(default=False)),
                ('payment_reference', models.CharField(blank=True, max_length=255)),
                ('tournament', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='registrations',
                    to='vent_tournament.tournament',
                )),
                ('team', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='tournament_registrations',
                    to='vent_auth.teams',
                )),
                ('user', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='tournament_registrations',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
        migrations.AddConstraint(
            model_name='tournamentregistration',
            constraint=models.UniqueConstraint(
                condition=models.Q(team__isnull=False),
                fields=['tournament', 'team'],
                name='unique_tournament_team',
            ),
        ),
        migrations.AddConstraint(
            model_name='tournamentregistration',
            constraint=models.UniqueConstraint(
                condition=models.Q(user__isnull=False),
                fields=['tournament', 'user'],
                name='unique_tournament_user',
            ),
        ),

        # BracketMatch
        migrations.CreateModel(
            name='BracketMatch',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('round_number', models.PositiveIntegerField()),
                ('match_number', models.PositiveIntegerField()),
                ('score_p1', models.IntegerField(default=0)),
                ('score_p2', models.IntegerField(default=0)),
                ('status', models.CharField(
                    choices=[
                        ('scheduled', 'Scheduled'),
                        ('in_progress', 'In Progress'),
                        ('completed', 'Completed'),
                        ('bye', 'Bye'),
                    ],
                    default='scheduled',
                    max_length=20,
                )),
                ('scheduled_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('tournament', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='bracket_matches',
                    to='vent_tournament.tournament',
                )),
                ('participant_1', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='matches_as_p1',
                    to='vent_tournament.tournamentregistration',
                )),
                ('participant_2', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='matches_as_p2',
                    to='vent_tournament.tournamentregistration',
                )),
                ('winner', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='matches_won',
                    to='vent_tournament.tournamentregistration',
                )),
            ],
            options={
                'ordering': ['round_number', 'match_number'],
            },
        ),

        # TournamentDispute
        migrations.CreateModel(
            name='TournamentDispute',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('description', models.TextField()),
                ('evidence', models.JSONField(blank=True, default=list)),
                ('status', models.CharField(
                    choices=[
                        ('open', 'Open'),
                        ('under_review', 'Under Review'),
                        ('resolved', 'Resolved'),
                        ('dismissed', 'Dismissed'),
                    ],
                    default='open',
                    max_length=20,
                )),
                ('resolution_note', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('tournament', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='disputes',
                    to='vent_tournament.tournament',
                )),
                ('match', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='disputes',
                    to='vent_tournament.bracketmatch',
                )),
                ('raised_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='disputes_raised',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
    ]
