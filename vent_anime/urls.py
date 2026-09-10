"""Every anime route, and every one of them wrapped by the switch.

`gated` is applied HERE, once per route, rather than as a decorator on each
view. A rule that has to be remembered at every view is a rule that holds
everywhere except the one added last, and that one ships open.

`tests_switch.py` walks this list and fails if any route is missing the mark, so
a route added tomorrow without `gated` fails the build rather than the module.
"""
from django.urls import path

from . import views_battles, views_reader, views_rooms, views_series
from .switch import gated

urlpatterns = [
    # What the module knows the names of.
    path('catalogue/', gated(views_series.anime_catalogue),
         name='anime_catalogue'),

    # Comics
    path('series/', gated(views_series.series_list), name='anime_series_list'),
    path('series/<str:reference>/', gated(views_series.series_detail),
         name='anime_series_detail'),
    path('series/<str:reference>/volumes/', gated(views_series.series_volumes),
         name='anime_series_volumes'),
    path('series/<str:reference>/chapters/',
         gated(views_series.series_chapters), name='anime_series_chapters'),
    path('series/<str:reference>/subscribe/',
         gated(views_reader.series_subscribe), name='anime_series_subscribe'),
    path('series/<str:reference>/follow/', gated(views_reader.series_follow),
         name='anime_series_follow'),
    path('series/<str:reference>/rate/', gated(views_reader.series_rate),
         name='anime_series_rate'),
    path('series/<str:reference>/boost/', gated(views_reader.series_boost),
         name='anime_series_boost'),
    path('series/<str:reference>/promo/', gated(views_reader.series_promo),
         name='anime_series_promo'),

    # Chapters
    path('chapters/<str:reference>/', gated(views_series.chapter_detail),
         name='anime_chapter_detail'),
    path('chapters/<str:reference>/buy/', gated(views_reader.chapter_buy),
         name='anime_chapter_buy'),
    path('chapters/<str:reference>/comments/',
         gated(views_reader.chapter_comments), name='anime_chapter_comments'),
    path('chapters/<str:reference>/progress/',
         gated(views_reader.chapter_progress), name='anime_chapter_progress'),
    path('chapters/<str:reference>/bookmarks/',
         gated(views_reader.chapter_bookmarks), name='anime_chapter_bookmarks'),

    # The reader themselves
    path('my-list/', gated(views_reader.my_list), name='anime_my_list'),
    path('reader-settings/', gated(views_reader.reader_settings),
         name='anime_reader_settings'),

    # Rooms
    path('rooms/', gated(views_rooms.room_list), name='anime_room_list'),
    path('rooms/<str:token>/', gated(views_rooms.room_detail),
         name='anime_room_detail'),
    path('rooms/<str:token>/join/', gated(views_rooms.room_join),
         name='anime_room_join'),
    path('rooms/<str:token>/leave/', gated(views_rooms.room_leave),
         name='anime_room_leave'),
    path('rooms/<str:token>/feed/', gated(views_rooms.room_feed),
         name='anime_room_feed'),
    path('rooms/<str:token>/page/', gated(views_rooms.room_page),
         name='anime_room_page'),
    path('rooms/<str:token>/control/', gated(views_rooms.room_control),
         name='anime_room_control'),
    path('rooms/<str:token>/chat/', gated(views_rooms.room_chat),
         name='anime_room_chat'),
    path('rooms/<str:token>/annotations/', gated(views_rooms.room_annotations),
         name='anime_room_annotations'),
    path('rooms/<str:token>/invite/', gated(views_rooms.room_invite),
         name='anime_room_invite'),
    path('rooms/<str:token>/remove/', gated(views_rooms.room_remove),
         name='anime_room_remove'),
    path('rooms/<str:token>/close/', gated(views_rooms.room_close),
         name='anime_room_close'),
    path('rooms/<str:token>/analytics/', gated(views_rooms.room_analytics),
         name='anime_room_analytics'),
    path('rooms/<str:token>/signal/', gated(views_rooms.room_signal),
         name='anime_room_signal'),

    # Battles
    path('battles/', gated(views_battles.battle_list), name='anime_battles'),
    path('battles/<str:reference>/', gated(views_battles.battle_detail),
         name='anime_battle_detail'),
    path('battles/<str:reference>/nominate/',
         gated(views_battles.battle_nominate), name='anime_battle_nominate'),
    path('battles/<str:reference>/approve/',
         gated(views_battles.battle_approve), name='anime_battle_approve'),
    path('battles/<str:reference>/vote/', gated(views_battles.battle_vote),
         name='anime_battle_vote'),
    path('battles/<str:reference>/comments/',
         gated(views_battles.battle_comments), name='anime_battle_comments'),
]
