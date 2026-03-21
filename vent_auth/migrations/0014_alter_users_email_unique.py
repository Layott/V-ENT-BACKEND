from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0013_users_provider_id_users_signup_type_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='users',
            name='email',
            field=models.EmailField(max_length=254, unique=True),
        ),
    ]
