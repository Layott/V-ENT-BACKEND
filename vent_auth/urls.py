from django.urls import path, include
from .views import *
from django.conf import settings
from django.conf.urls.static import static
from . import views_admin_events as admin_events


from .views_rankings import games_list
from .views_follow import follow, followers, following
from .views_user_activity import user_tournaments, user_events
from .views_twofactor import (
    two_factor_start, two_factor_confirm, two_factor_disable,
    two_factor_status,
)
from .views_admin_rates import (
    admin_rates, admin_rate_detail, admin_refresh_rates,
)
from .views_admin_games import (
    admin_games, admin_game_detail, admin_game_series, admin_series_detail,
    admin_game_modes, admin_mode_detail,
)
from .views_admin_matches import admin_tournament_matches
from .views_kyc_files import kyc_document
from .views_waitlist import waitlist_claim, waitlist_claim_preview
from . import views_discord_auth as discord_auth
from . import views_discord_interactions as discord_interactions
from . import views_discord_server as discord_guild
from . import views_discord_webhooks as discord_hooks
from . import views_linking as linking
from . import views_cards as cards

from . import views_feedback

urlpatterns = [
    # Somewhere to say what is wrong. Open to anybody: the wall somebody hit is
    # sometimes the sign-in page itself.
    path("feedback/", views_feedback.feedback, name="feedback"),
    # path("admin/", admin.site.urls),
    path('signup/', signup, name='signup'),
    path('verify/<uidb64>/<token>/', verify_token_3, name='verify_token_3'),
    path('login/', login, name='login'),
    path('login/2fa/verify/', login_2fa_verify, name='login_2fa_verify'),
    # Turning it on and off for an ordinary member. The login half above has
    # existed and worked for a while; there was no way to enrol.
    path('2fa/start/', two_factor_start, name='two_factor_start'),
    path('2fa/confirm/', two_factor_confirm, name='two_factor_confirm'),
    path('2fa/disable/', two_factor_disable, name='two_factor_disable'),
    path('2fa/status/', two_factor_status, name='two_factor_status'),

    # Following a team or a person, and who follows what. Organisations already
    # had this; teams and people had no table, no endpoint and no count.
    # Addressed by slug or username, never by a primary key.
    # What somebody has taken part in. Both profile history panels have been
    # fetching these two since they were written and neither route existed, so
    # both tabs were empty for every account. Found by check-api-paths.mjs.
    path('user-activity/tournaments/', user_tournaments, name='user_tournaments'),
    path('user-activity/events/', user_events, name='user_events'),
    path('follow/mine/', following, name='following_mine'),
    path('follow/<str:kind>/<str:ref>/', follow, name='follow'),
    path('follow/<str:kind>/<str:ref>/followers/', followers, name='followers'),
    path('logout/', logout, name='logout'),
    path('dj-rest-auth/', include('dj_rest_auth.urls')),
    path('dj-rest-auth/registration/', include('dj_rest_auth.registration.urls')),
    path('accounts/', include('allauth.urls')),
    path('dj-rest-auth/google/', GoogleLogin.as_view(), name='google_login'),
    path('change-fullname/', change_fullname, name='change_fullname'),
    path('change-email/', change_email, name='change_email'),
    path('verify-new-email/', verify_new_email, name='verify_new_email'),
    path("forgot-password/send-token/", forgot_password, name="forgot_password"),
    path("forgot-password/verify-token/", verify_forgot_password_token, name="verify_forgot_password_token"),
    path("forgot-password/change-password/", change_password_fp, name="change_password_fp"),
    path("send-code/", send_code, name="send_code"),
    path("save-username/", save_username, name="save_username"),
    path("admin/me/", admin_me, name="admin_me"),
    path("admin/get-all-username-and-email/", get_all_username_and_email, name="get_all_username_and_email"),
    path("admin/user-count/", get_number_of_all_users, name="get_number_of_all_users"),
    path("admin/check-username-availability/", check_username_availability, name="check_username_availability"),
    # Admin dashboard
    path("admin/metrics/", admin_metrics, name="admin_metrics"),
    path("admin/charts/", admin_charts, name="admin_charts"),
    path("admin/recent-activity/", admin_recent_activity, name="admin_recent_activity"),
    path("admin/settings/", admin_settings, name="admin_settings"),
    path("admin/users/", admin_list_users, name="admin_list_users"),
    path("admin/users/bulk/", admin_bulk_user_action, name="admin_bulk_user_action"),
    path("admin/users/<str:user_id>/", admin_get_user, name="admin_get_user"),
    path("admin/users/<str:user_id>/ban/", admin_ban_user, name="admin_ban_user"),
    path("admin/users/<str:user_id>/role/", admin_set_user_role, name="admin_set_user_role"),
    path("admin/users/<str:user_id>/delete/", admin_delete_user, name="admin_delete_user"),
    path("admin/tournaments/", admin_list_tournaments, name="admin_list_tournaments"),
    path("admin/events/", admin_list_events, name="admin_list_events"),
    # The console's view of one event: its numbers, its tickets, what was sent.
    # Literal segments first so `<str:event_ref>` cannot swallow them.
    path("admin/tickets/<str:code>/action/", admin_events.admin_ticket_action,
         name="admin_ticket_action"),
    path("admin/tournaments/<str:tournament_ref>/sent/",
         admin_events.admin_tournament_sent, name="admin_tournament_sent"),
    path("admin/events/<str:event_ref>/tickets/", admin_events.admin_event_tickets,
         name="admin_event_tickets"),
    path("admin/events/<str:event_ref>/sent/", admin_events.admin_event_sent,
         name="admin_event_sent"),
    path("admin/events/<str:event_ref>/state/", admin_events.admin_event_state,
         name="admin_event_state"),
    path("admin/events/<str:event_ref>/", admin_events.admin_event_detail,
         name="admin_event_detail"),
    path("admin/games/", admin_games, name="admin_games"),
    path("admin/games/<int:game_id>/", admin_game_detail, name="admin_game_detail"),
    path("admin/games/<int:game_id>/series/", admin_game_series, name="admin_game_series"),
    path("admin/games/<int:game_id>/modes/", admin_game_modes, name="admin_game_modes"),
    path("admin/modes/<int:mode_id>/", admin_mode_detail, name="admin_mode_detail"),
    path("admin/series/<int:series_id>/", admin_series_detail, name="admin_series_detail"),
    path("platform/modules/", public_platform_modules, name="public_platform_modules"),
    path("currencies/", public_currencies, name="public_currencies"),
    path("admin/rates/", admin_rates, name="admin_rates"),
    path("admin/rates/refresh/", admin_refresh_rates, name="admin_refresh_rates"),
    path("admin/rates/<str:code>/", admin_rate_detail, name="admin_rate_detail"),
    path("me/admin/", my_admin_capabilities, name="my_admin_capabilities"),
    # Every match, named, so an override picks one instead of typing an id.
    path("admin/tournaments/<int:tournament_id>/matches/", admin_tournament_matches, name="admin_tournament_matches"),
    path("admin/tournaments/<int:tournament_id>/", admin_get_tournament, name="admin_get_tournament"),
    path("admin/tournaments/<int:tournament_id>/dispute/resolve/", admin_resolve_dispute, name="admin_resolve_dispute"),
    path("admin/tournaments/<int:tournament_id>/cancel/", admin_cancel_tournament, name="admin_cancel_tournament"),
    path("admin/tournaments/<int:tournament_id>/disqualify/", admin_disqualify_registration, name="admin_disqualify_registration"),
    path("admin/matches/<int:match_id>/score/", admin_override_match_score, name="admin_override_match_score"),
    path("admin/payouts/", admin_payouts_list, name="admin_payouts_list"),
    path("admin/payouts/pending/", admin_pending_payouts, name="admin_pending_payouts"),
    path("admin/payouts/bulk-approve/", admin_bulk_approve_payouts, name="admin_bulk_approve_payouts"),
    path("admin/payouts/<int:withdrawal_id>/approve/", admin_approve_payout, name="admin_approve_payout"),
    path("admin/payouts/<int:withdrawal_id>/reject/", admin_reject_payout, name="admin_reject_payout"),
    path("admin/kyc/", admin_kyc_list, name="admin_kyc_list"),
    path("admin/kyc/pending/", admin_pending_kyc, name="admin_pending_kyc"),
    path("admin/kyc/<int:kyc_id>/approve/", admin_approve_kyc, name="admin_approve_kyc"),
    path("admin/kyc/<int:kyc_id>/reject/", admin_reject_kyc, name="admin_reject_kyc"),
    path("admin/audit-log/export.csv", admin_audit_log_export, name="admin_audit_log_export"),
    path("admin/audit-log/", admin_audit_log, name="admin_audit_log"),
    # Admin dispute center (queue across all tournaments)
    path("admin/disputes/", admin_disputes_list, name="admin_disputes_list"),
    path("admin/disputes/<int:dispute_id>/resolve/", admin_resolve_dispute_by_id, name="admin_resolve_dispute_by_id"),
    # Notifications inbox + bell
    path("notifications/", list_notifications, name="list_notifications"),
    path("notifications/unread-count/", notifications_unread_count, name="notifications_unread_count"),
    path("notifications/read-all/", mark_all_notifications_read, name="mark_all_notifications_read"),
    path("notifications/<int:notification_id>/read/", mark_notification_read, name="mark_notification_read"),
    path("notifications/<int:notification_id>/delete/", delete_notification, name="delete_notification"),
    path("get-username-with-email/", get_username_with_email, name="get_username_with_email"),
    path("edit-profile-info/", edit_profile_info, name="edit_profile_info"),
    path("get-user-informations/", get_user_informations, name="get_user_informations"),
    # Public-ish profile card for a username, used by the wallet send flow.
    path("user/lookup/", lookup_user, name="lookup_user"),
    path("get-user-status/", get_user_status, name="get_user_status"),
    path("add-email-to-waitlist/", add_email_to_waitlist, name="add_email_to_waitlist"),
    path("update-web-and-social-links/", update_web_and_social_links, name="update_web_and_social_links"),
    path("social-auth/", social_auth, name="social_auth"),
    path("edit-favorite-games/", edit_favorite_games, name="edit_favorite_games"),
    path("update-favorite-games/", update_favorite_games, name="update_favorite_games"),
    path("update-gaming-accounts/", update_gaming_accounts, name="update_gaming_accounts"),
    # Linking an external account for real: Discord OAuth2, Steam OpenID.
    # Saved cards. A card is saved by being used once, never by typing its
    # number into a V-ENT form.
    path("wallet/cards/", cards.list_cards, name="list_cards"),
    path("wallet/cards/<int:card_id>/remove/", cards.remove_card, name="remove_card"),
    path("wallet/cards/<int:card_id>/default/", cards.set_default_card, name="set_default_card"),
    path("wallet/cards/charge/", cards.charge_saved_card, name="charge_saved_card"),
    path("link/status/", linking.link_status, name="link_status"),
    # Signing in and signing up WITH Discord. A separate callback from the
    # linking one on purpose: that one attaches a handle to whoever is already
    # signed in, this one can create an account, and one URL with two security
    # stories is how the wrong one gets used.
    # The Discord channels a tournament or an event announces into. One view
    # for both owners: building it for tournaments and leaving events until
    # later is the fault with its own rule.
    # An organisation driving the bot in its OWN Discord server. Each
    # capability is granted separately, so the invite an organiser authorises
    # carries only the permissions they ticked. CEO 7 Sept: "let each
    # organiser grant only the parts they want."
    # The one URL Discord POSTs a slash command to. Public because Discord
    # calls it; the Ed25519 signature is the authentication.
    path("discord/interactions/", discord_interactions.interactions,
         name="discord_interactions"),
    path("discord/guild/callback/", discord_guild.install_callback,
         name="discord_guild_callback"),
    path("discord/guild/<str:ref>/install/", discord_guild.install_url,
         name="discord_guild_install"),
    path("discord/guild/<str:ref>/servers/", discord_guild.servers,
         name="discord_guild_servers"),
    path("discord/guild/<str:ref>/servers/<int:server_id>/",
         discord_guild.server_detail, name="discord_guild_server"),
    path("discord/guild/<str:ref>/servers/<int:server_id>/targets/",
         discord_guild.server_targets, name="discord_guild_targets"),
    path("discord/guild/<str:ref>/servers/<int:server_id>/post/",
         discord_guild.server_post, name="discord_guild_post"),
    path("discord/guild/<str:ref>/servers/<int:server_id>/role/",
         discord_guild.server_role, name="discord_guild_role"),
    path("discord/guild/<str:ref>/servers/<int:server_id>/channel/",
         discord_guild.server_channel, name="discord_guild_channel"),
    path("discord/guild/<str:ref>/servers/<int:server_id>/purge/",
         discord_guild.server_purge, name="discord_guild_purge"),
    path("discord/guild/<str:ref>/servers/<int:server_id>/log/",
         discord_guild.server_log, name="discord_guild_log"),
    path("discord/webhooks/<str:kind>/<str:ref>/", discord_hooks.webhooks,
         name="discord_webhooks"),
    path("discord/webhooks/<str:kind>/<str:ref>/<int:hook_id>/",
         discord_hooks.webhook_detail, name="discord_webhook_detail"),
    path("discord/start/", discord_auth.discord_signin_start,
         name="discord_signin_start"),
    path("discord/callback/", discord_auth.discord_signin_callback,
         name="discord_signin_callback"),
    path("link/<str:provider>/start/", linking.link_start, name="link_start"),
    path("link/discord/callback/", linking.discord_callback, name="discord_link_callback"),
    path("link/steam/callback/", linking.steam_callback, name="steam_link_callback"),
    path("link/<str:provider>/dm/", linking.link_dm_toggle, name="link_dm_toggle"),
    path("link/<str:provider>/disconnect/", linking.link_disconnect, name="link_disconnect"),
    path("upload-avatar/", upload_avatar, name="upload_avatar"),
    path("upload-banner/", upload_banner, name="upload_banner"),
    path("resend-link/", resend_link, name="resend_link"),
    path("resend-forgot-password-token/", resend_forgot_password_token, name="resend_forgot_password_token"),
    path("upload-images/", upload_images, name="upload_images"),
    path("games/", games_list, name="games_list"),
    path("get-user-gallery/", get_user_gallery, name="get_user_gallery"),
    path("delete-gallery-image/", delete_gallery_image, name="delete_gallery_image"),
    # Wallet
    path("wallet/balance/", get_wallet_balance, name="get_wallet_balance"),
    path("wallet/transactions/", get_wallet_transactions, name="get_wallet_transactions"),
    path("wallet/topup/initiate/", topup_initiate, name="topup_initiate"),
    path("wallet/topup/verify/", topup_verify, name="topup_verify"),
    path("wallet/send/", send_funds, name="send_funds"),
    path("wallet/pin/verify/", verify_wallet_pin, name="verify_wallet_pin"),
    path("wallet/pin/set/", set_wallet_pin, name="set_wallet_pin"),
    path("wallet/deduct/", wallet_deduct, name="wallet_deduct"),
    path("wallet/withdraw/initiate/", withdraw_initiate, name="withdraw_initiate"),
    path("wallet/withdraw/status/", withdraw_status, name="withdraw_status"),
    path("wallet/kyc/submit/", kyc_submit, name="kyc_submit"),
    path("wallet/kyc/status/", kyc_status, name="kyc_status"),
    # Authenticated read of an identity document. Never a /media/ URL.
    path("kyc/document/<int:document_id>/", kyc_document, name="kyc_document"),
    # Pre-launch waitlist. The token in the mailed link is the credential,
    # so neither of these takes a session.
    path("waitlist/claim/<str:token>/", waitlist_claim_preview, name="waitlist_claim_preview"),
    path("waitlist/claim/", waitlist_claim, name="waitlist_claim"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)