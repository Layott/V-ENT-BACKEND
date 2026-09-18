# Written by hand on 2026-09-18: where the money went when a ticket was
# refunded. An event that is cancelled refunds every live paid ticket (CEO,
# 18 September 2026), and support's question is always where it went.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vent_event', '0051_slot_purchase_refunded'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='refunded_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ticket',
            name='refund_reference',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
