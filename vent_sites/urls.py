"""Approved sites (P8, level 2), mounted at /sites/. See views.py for each door."""
from django.urls import path

from . import views

urlpatterns = [
    path('mine/', views.my_sites, name='sites_mine'),
    path('mine/<str:host>/check/', views.check_site, name='sites_check'),
    path('mine/<str:host>/', views.remove_site, name='sites_remove'),
    path('frame/<str:kind>/<str:ref>/', views.frame_policy, name='sites_frame_policy'),
    path('view/', views.count_view, name='sites_count_view'),
    path('<str:kind>/<str:ref>/locks/', views.set_locks, name='sites_set_locks'),
    path('<str:kind>/<str:ref>/', views.record_sites, name='sites_record'),
]
