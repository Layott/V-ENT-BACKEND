"""Currencies, and what they are worth against the naira.

V-ENT prices in naira because that is what Paystack settles and what a VENT COIN
is worth. An organiser in Accra or Nairobi still thinks in cedis or shillings,
and a reader in either place should not have to do arithmetic to find out what a
ticket costs.

The rate is stored here rather than fetched from an exchange on every page load:
the VPS runs everything itself, and a price that silently changes between the
page and the checkout because a third party moved is worse than one that is a
day stale. An admin sets the rates, and `rate_updated` says when, so nobody has
to guess how old the number is.

**These rates are for display.** Money still moves in naira through Paystack, and
a VENT COIN is still a naira amount. A converted figure is there so somebody can
tell roughly what they are paying, never to bill them in another currency.
"""
from django.db import migrations, models
import django.utils.timezone


# code, name, symbol, rate: how many of this currency one naira buys.
# Set from mid-market rates on 27 August 2026. They are a starting point for the
# admin to correct, not a live feed.
SEED = [
    ('NGN', 'Nigerian naira', '₦', '1', 0),
    ('GHS', 'Ghanaian cedi', 'GH₵', '0.0098', 1),
    ('KES', 'Kenyan shilling', 'KSh', '0.0840', 2),
    ('ZAR', 'South African rand', 'R', '0.0118', 3),
    ('EGP', 'Egyptian pound', 'E£', '0.0316', 4),
    ('XOF', 'West African CFA franc', 'CFA', '0.3800', 5),
    ('XAF', 'Central African CFA franc', 'FCFA', '0.3800', 6),
    ('MAD', 'Moroccan dirham', 'DH', '0.0065', 7),
    ('TZS', 'Tanzanian shilling', 'TSh', '1.6500', 8),
    ('UGX', 'Ugandan shilling', 'USh', '2.3500', 9),
    ('RWF', 'Rwandan franc', 'FRw', '0.8900', 10),
    ('ETB', 'Ethiopian birr', 'Br', '0.0880', 11),
    ('ZMW', 'Zambian kwacha', 'ZK', '0.0170', 12),
    ('USD', 'US dollar', '$', '0.00065', 20),
]


def seed(apps, schema_editor):
    Currency = apps.get_model('vent_auth', 'Currency')
    for code, name, symbol, rate, order in SEED:
        Currency.objects.update_or_create(
            code=code,
            defaults={'name': name, 'symbol': symbol,
                      'rate_from_ngn': rate, 'sort_order': order},
        )


def unseed(apps, schema_editor):
    Currency = apps.get_model('vent_auth', 'Currency')
    Currency.objects.filter(code__in=[c[0] for c in SEED]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0051_fold_annual_titles_into_series'),
    ]

    operations = [
        migrations.CreateModel(
            name='Currency',
            fields=[
                ('code', models.CharField(max_length=3, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=60)),
                ('symbol', models.CharField(max_length=8)),
                ('rate_from_ngn', models.DecimalField(
                    max_digits=18, decimal_places=8, default=1,
                    help_text='How many of this currency one naira buys.')),
                ('rate_updated', models.DateTimeField(default=django.utils.timezone.now)),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'verbose_name_plural': 'Currencies',
                'ordering': ['sort_order', 'code'],
            },
        ),
        migrations.RunPython(seed, unseed),
    ]
