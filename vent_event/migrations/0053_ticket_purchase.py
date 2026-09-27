"""Every ticket learns which purchase it came from (inbox 273).

A purchase's ledger lines hang off its first ticket with the count on them.
The tickets of that purchase are the first ticket and the ones created with
it: same event, same tier, same price, the same buyer or the same card
payment, created in the same moment, in id order. They share one key, so a
single ticket can take back its own share of the purchase's money.
"""
import uuid
from datetime import timedelta

from django.db import migrations, models


def forwards(apps, schema_editor):
    Ticket = apps.get_model('vent_event', 'Ticket')
    Line = apps.get_model('vent_event', 'EventLedgerEntry')
    firsts = (Line.objects.filter(ticket__isnull=False, registration__isnull=True,
                                  prize__isnull=True, quantity__gt=0)
              .exclude(kind='reversal')
              .values_list('ticket_id', 'quantity').distinct())
    seen = set()
    for ticket_id, quantity in firsts:
        if ticket_id in seen:
            continue
        seen.add(ticket_id)
        first = Ticket.objects.filter(pk=ticket_id).first()
        if first is None or first.purchase:
            continue
        mates = Ticket.objects.filter(
            event_id=first.event_id, tier_id=first.tier_id, id__gte=first.id,
            price_vc=first.price_vc, price_ngn=first.price_ngn, purchase='',
            purchased_at__gte=first.purchased_at - timedelta(seconds=5),
            purchased_at__lte=first.purchased_at + timedelta(seconds=30))
        if first.payment_reference:
            mates = mates.filter(payment_reference=first.payment_reference)
        else:
            mates = mates.filter(payment_reference='')
        ids = list(mates.order_by('id').values_list('id', flat=True)[:max(int(quantity), 1)])
        if first.id not in ids:
            ids = [first.id]
        Ticket.objects.filter(pk__in=ids).update(purchase=uuid.uuid4().hex)


class Migration(migrations.Migration):
    dependencies = [('vent_event', '0052_ticket_refund_record')]
    operations = [
        migrations.AddField(
            model_name='ticket', name='purchase',
            field=models.CharField(blank=True, db_index=True, default='', max_length=32)),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
