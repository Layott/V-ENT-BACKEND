"""Circuits (P5), mounted at /circuits/. See views.py for each door."""
from django.urls import path

from . import views

urlpatterns = [
    path('', views.circuit_list, name='circuits'),
    path('<str:circuit_ref>/', views.circuit_detail, name='circuit'),
    path('<str:circuit_ref>/tiers/', views.tier_list, name='circuit_tiers'),
    path('<str:circuit_ref>/tiers/<int:circuit_tier_id>/', views.tier_detail, name='circuit_tier'),
    path('<str:circuit_ref>/tournaments/', views.tournament_list, name='circuit_tournaments'),
    path('<str:circuit_ref>/tournaments/<str:tournament_ref>/', views.tournament_detail,
         name='circuit_tournament'),
    path('<str:circuit_ref>/adjustments/', views.adjustment_list, name='circuit_adjustments'),
    path('<str:circuit_ref>/adjustments/<int:circuit_adjustment_id>/', views.adjustment_detail,
         name='circuit_adjustment'),
]
