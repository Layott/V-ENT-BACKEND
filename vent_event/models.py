from django.db import models
from vent_auth.models import Users

class Event(models.Model):
    event_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=40)
    creator = models.ForeignKey(Users, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)
    event_type = models.CharField(max_length=8)  # e.g., physical, virtual, hybrid
    desc = models.CharField(max_length=140)
    entry_fee = models.DecimalField(max_digits=10, decimal_places=2)
    reg_start_date = models.DateTimeField()
    reg_end_date = models.DateTimeField()
    event_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    location = models.CharField(max_length=255)  # Physical address
    event_link = models.CharField(max_length=255)  # URL for virtual events
    logo = models.ImageField(upload_to='event_logos/', null=True, blank=True)
    banner = models.ImageField(upload_to='event_banners/', null=True, blank=True)

    def __str__(self):
        return self.name


class Sponsor(models.Model):
    sponsor_id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="sponsors")
    name = models.CharField(max_length=100)
    logo = models.ImageField(upload_to='sponsor_logos/')  # Sponsor logo upload path

    def __str__(self):
        return self.name

class SocialLink(models.Model):
    social_link_id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='social_links')
    platform = models.CharField(max_length=50)  # e.g., Facebook, Instagram
    url = models.URLField()

    def __str__(self):
        return f"{self.platform} - {self.url}"


class Attendees(models.Model):
    