from django.urls import include, path

from accounts import views as account_views
from core import views
from orders import views as order_views

handler400 = "core.error_views.bad_request"
handler403 = "core.error_views.permission_denied"
handler404 = "core.error_views.not_found"
handler500 = "core.error_views.server_error"

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/", include("accounts.urls")),
    path("cafes/", include("cafes.urls")),
    path("orders/", include("orders.urls")),
    path("join/<slug:token>/", order_views.join, name="join"),
    path("join/<slug:token>/submitted/", order_views.submitted, name="join-submitted"),
    path("accounts/login/", account_views.entry, {"role": "organizer"}, name="account_login"),
    path("accounts/kakao/login/callback/", account_views.callback, name="kakao_callback"),
    path("health/live/", views.liveness, name="health-live"),
    path("health/ready/", views.readiness, name="health-ready"),
]
