"""Circuits (P5), mounted at /circuits/. See views.py and views_leagues.py
for each door. The league addresses come first: `leagues` is never a
circuit's slug (models.RESERVED_SLUGS)."""
from django.urls import path

from . import views, views_leagues

urlpatterns = [
    path('leagues/', views_leagues.league_list, name='circuit_leagues'),
    path('leagues/<str:league_ref>/', views_leagues.league_detail, name='circuit_league'),
    path('leagues/<str:league_ref>/divisions/', views_leagues.division_list, name='circuit_league_divisions'),
    path('leagues/<str:league_ref>/divisions/<str:circuit_ref>/', views_leagues.division_detail,
         name='circuit_league_division'),
    path('leagues/<str:league_ref>/next-season/', views_leagues.next_season, name='circuit_league_next_season'),
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
    path('<str:circuit_ref>/members/', views_leagues.member_list, name='circuit_members'),
    path('<str:circuit_ref>/members/<int:circuit_member_id>/', views_leagues.member_detail,
         name='circuit_member'),
]
