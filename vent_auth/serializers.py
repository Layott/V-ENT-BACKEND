from rest_framework import serializers
from .models import Users

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = Users
        fields = ('full_name', 'email', 'password', 'is_superuser', 'is_active')
        extra_kwargs = {'password': {'write_only': True}}
