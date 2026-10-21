import uuid

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render

from .services import ROLES


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        # Password registration is never exposed. Social signup is handled below.
        return False

    def add_message(
        self, request, level, message_template=None, message_context=None, extra_tags=""
    ):
        # Our own screens supply concise messages, without allauth's account names.
        return None

    def respond_user_inactive(self, request, user):
        return render(request, "account/account_inactive.html", status=403)


class KakaoAdapter(DefaultSocialAccountAdapter):
    def populate_user(self, request, sociallogin, data):
        raw = sociallogin.account.extra_data
        nickname = (
            raw.get("kakao_account", {}).get("profile", {}).get("nickname")
            or raw.get("properties", {}).get("nickname")
            or "영차 회원"
        )
        nickname = " ".join(str(nickname).split())[:40]
        # Sanitize BEFORE allauth looks up or updates an existing SocialAccount.
        sociallogin.account.extra_data = {"nickname": nickname}
        sociallogin.email_addresses = []
        sociallogin.phone = None
        user = sociallogin.user
        user.username = f"kakao_{uuid.uuid4().hex}"
        user.nickname = nickname
        user.email = ""
        user.first_name = ""
        user.last_name = ""
        return user

    def is_open_for_signup(self, request, sociallogin):
        return settings.KAKAO_LOGIN_ENABLED and sociallogin.account.provider == "kakao"

    def pre_social_login(self, request, sociallogin):
        role = (sociallogin.state.get("data") or {}).get("role")
        if (
            not settings.KAKAO_LOGIN_ENABLED
            or sociallogin.account.provider != "kakao"
            or role not in ROLES
            or sociallogin.state.get("process") != "login"
        ):
            raise ImmediateHttpResponse(redirect("accounts:login-error"))
        if sociallogin.is_existing:
            nickname = sociallogin.account.extra_data["nickname"]
            sociallogin.user.nickname = nickname
            sociallogin.user.save(update_fields=["nickname"])

    def on_authentication_error(self, request, provider, **kwargs):
        # Do not expose/log provider payloads, OAuth codes, tokens, or secrets.
        messages.error(request, "카카오 로그인을 완료하지 못했어요. 다시 시도해 주세요.")
        raise ImmediateHttpResponse(redirect("accounts:login-error"))
