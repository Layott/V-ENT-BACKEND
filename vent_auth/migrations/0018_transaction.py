from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0017_phase1_wallet_admin_models'),
        ('vent_tournament', '0008_tournament_registration_bracket_dispute'),
    ]

    operations = [
        migrations.CreateModel(
            name='Transaction',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('type', models.CharField(
                    choices=[
                        ('top_up', 'Top Up'),
                        ('deduction', 'Deduction'),
                        ('prize', 'Prize'),
                        ('send', 'Send'),
                        ('receive', 'Receive'),
                        ('withdrawal', 'Withdrawal'),
                        ('refund', 'Refund'),
                    ],
                    max_length=20,
                )),
                ('amount', models.IntegerField()),
                ('description', models.CharField(max_length=255)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('completed', 'Completed'),
                        ('failed', 'Failed'),
                        ('cancelled', 'Cancelled'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('reference', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('wallet', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='transactions',
                    to='vent_auth.userwallet',
                )),
                ('tournament', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='transactions',
                    to='vent_tournament.tournament',
                )),
            ],
        ),
    ]
