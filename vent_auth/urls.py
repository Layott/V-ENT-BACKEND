from django.urls import path, include
from .views import *
from django.conf import settings
from django.conf.urls.static import static


urlpatterns = [
    # path("admin/", admin.site.urls),
    path('signup/', signup, name='signup'),
    path('verify-token/', verify_token, name='verify_token'),
    path('login/', login, name='login'),
    path('dj-rest-auth/', include('dj_rest_auth.urls')),
    path('dj-rest-auth/registration/', include('dj_rest_auth.registration.urls')),
    path('accounts/', include('allauth.urls')),
    path('dj-rest-auth/google/', GoogleLogin.as_view(), name='google_login'),
    path('change-fullname/', change_fullname, name='change_fullname'),
    path('change-username/', change_username, name='change_username'),
    path('change-email/', change_email, name='change_email'),
    path('verify-new-email/', verify_new_email, name='verify_new_email'),
    path('verify-token-2/', verify_token_2, name='verify_token_2'),
    path("forgot-password/send-token/", forgot_password, name="forgot_password"),
    path("forgot-password/verify-token/", verify_forgot_password_token, name="verify_forgot_password_token"),
    path("forgot-password/change-password/", change_password_fp, name="change_password_fp"),

] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)