# -*- coding: utf-8 -*-
"""The card catalogue. Lineup routes hang off /tournament/ in vent_tournament."""

from django.urls import path

from . import views

urlpatterns = [
    path('ingest/', views.ingest, name='cards_ingest'),
    path('search/', views.search, name='cards_search'),
    path('formations/', views.formations, name='cards_formations'),
]
