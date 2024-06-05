from django.db import models

# Create your models here.


class Users(AbstractUser):
    user_id = models.AutoField(primary_key=True, null=False)
    full_name = models.CharField(max_length=148, null=False)
    