from allauth.account.signals import user_logged_in
from django.conf import settings
from django.contrib.auth.signals import user_logged_out
from django.core.exceptions import PermissionDenied
from django.dispatch import receiver
from django.urls import reverse

from cafes.services import close_for_seller

from .services import enter_role, select_initial_operator


@receiver(user_logged_in)
def complete_kakao_role(sender, request, response, user, sociallogin=None, **kwargs):
    if sociallogin is None:
        return
    role = (sociallogin.state.get("data") or {}).get("role")
    select_initial_operator(user)
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)
    try:
        destination = enter_role(request, user, role)
    except PermissionDenied:
        # A new seller first registers as an ordinary member; no privilege is granted.
        destination = reverse("accounts:denied", args=[role])
    response["Location"] = destination


@receiver(user_logged_out)
def close_seller_on_logout(sender, request, user, **kwargs):
    cafe_id = request.session.get("seller_cafe_id") if request else None
    if user and cafe_id:
        close_for_seller(user=user, cafe_id=cafe_id)
