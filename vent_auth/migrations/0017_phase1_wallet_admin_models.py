from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0016_alter_userprofile_date_of_birth'),
    ]

    operations = [
        # Users: add role field
        migrations.AddField(
            model_name='users',
            name='role',
            field=models.CharField(
                choices=[('user', 'User'), ('organizer', 'Organizer'), ('admin', 'Admin')],
                default='user',
                max_length=20,
            ),
        ),

        # UserWallet: replace user_wallet_pin with pin_hash + add kyc_verified
        migrations.RemoveField(
            model_name='userwallet',
            name='user_wallet_pin',
        ),
        migrations.AddField(
            model_name='userwallet',
            name='pin_hash',
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
        migrations.AddField(
            model_name='userwallet',
            name='kyc_verified',
            field=models.BooleanField(default=False),
        ),

        # WithdrawalRequest
        migrations.CreateModel(
            name='WithdrawalRequest',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.IntegerField()),
                ('bank_name', models.CharField(max_length=100)),
                ('account_number', models.CharField(max_length=20)),
                ('account_name', models.CharField(max_length=100)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('approved', 'Approved'),
                        ('rejected', 'Rejected'),
                        ('processing', 'Processing'),
                        ('completed', 'Completed'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('admin_note', models.TextField(blank=True)),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('wallet', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='withdrawals',
                    to='vent_auth.userwallet',
                )),
            ],
        ),

        # KYCDocument
        migrations.CreateModel(
            name='KYCDocument',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('document_type', models.CharField(
                    choices=[
                        ('national_id', 'National ID'),
                        ('passport', 'Passport'),
                        ('drivers_license', "Driver's License"),
                    ],
                    max_length=50,
                )),
                ('document_image', models.ImageField(upload_to='kyc/')),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('approved', 'Approved'),
                        ('rejected', 'Rejected'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('rejection_reason', models.TextField(blank=True)),
                ('submitted_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='kyc_documents',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),

        # AdminAction
        migrations.CreateModel(
            name='AdminAction',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action_type', models.CharField(max_length=50)),
                ('target_model', models.CharField(max_length=50)),
                ('target_id', models.CharField(max_length=100)),
                ('reason', models.TextField(blank=True)),
                ('metadata', models.JSONField(default=dict)),
                ('performed_at', models.DateTimeField(auto_now_add=True)),
                ('admin', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='admin_actions',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
    ]
