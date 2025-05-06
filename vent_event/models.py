from django.db import models
from vent_auth.models import Users, Games, Teams, Organization
from django.utils import timezone


class Event(models.Model):
    event_id = models.AutoField(primary_key=True) # Event ID
    name = models.CharField(max_length=40) # Name of the event
    game = models.ForeignKey(Games, on_delete=models.CASCADE, related_name="events")
    creator = models.ForeignKey(Users, on_delete=models.CASCADE) # Creator of the event
    created_at = models.DateTimeField(auto_now_add=True) 
    last_updated = models.DateTimeField(auto_now=True) # Last updated timestamp
    event_type = models.CharField(max_length=8)  # e.g., physical, virtual, hybrid
    desc = models.CharField(max_length=140) # Description of the event
    entry_fee = models.DecimalField(max_digits=10, decimal_places=2) # Entry fee for the event
    reg_start_date = models.DateTimeField() # Registration start date
    reg_end_date = models.DateTimeField() # Registration end date
    event_date = models.DateField() # Date of the event
    start_time = models.TimeField() # Start time of the event
    end_time = models.TimeField() # End time of the event
    # For physical events, location is required; for virtual events, event_link is required
    # For virtual events, event_link is required; for hybrid events, both location and event_link are required
    # For hybrid events, both location and event_link are required
    location = models.CharField(max_length=255, null=True, blank=True)  # Location for physical events
    event_link = models.CharField(max_length=255, null=True, blank=True) # Link for virtual events
    logo = models.ImageField(upload_to='event_logos/', null=True, blank=True) # Event logo upload path
    banner = models.ImageField(upload_to='event_banners/', null=True, blank=True) # Event banner upload path
    is_active = models.BooleanField(default=True)  # To mark if the event is active or not
    interaction_count = models.PositiveIntegerField(default=0) # To track user interactions

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

