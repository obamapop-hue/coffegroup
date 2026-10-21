import json
import time
from io import StringIO
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import requests
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialToken
from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from cafes.models import Cafe

from .models import User

PROVIDERS = {
    "kakao": {
        "APP": {"client_id": "test-app-id", "secret": "test-app-secret"},
        "SCOPE": ["profile_nickname"],
    }
}


def json_response(data, status=200):
    response = requests.Response()
    response.status_code = status
    response.headers["content-type"] = "application/json"
    response._content = json.dumps(data).encode()
    return response


@override_settings(KAKAO_LOGIN_ENABLED=True, SOCIALACCOUNT_PROVIDERS=PROVIDERS)
class KakaoLoginTests(TestCase):
    def begin(self, role="organizer", **data):
        response = self.client.post(reverse("accounts:start", args=[role]), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.url).netloc, "kauth.kakao.com")
        return parse_qs(urlparse(response.url).query)

    def complete(self, state, uid=12345, nickname="테스트 총무"):
        profile = {
            "id": uid,
            "properties": {"profile_image": "https://example.com/private.jpg"},
            "kakao_account": {
                "email": "should-not-be-saved@example.com",
                "is_email_verified": True,
                "phone_number": "+82 10-0000-0000",
                "profile": {"nickname": nickname, "profile_image_url": "https://example.com/a.jpg"},
            },
        }
        responses = [
            json_response(
                {"access_token": "test-access-token", "refresh_token": "test-refresh-token"}
            ),
            json_response(profile),
        ]
        with patch("requests.sessions.Session.request", side_effect=responses) as network:
            response = self.client.get(
                reverse("kakao_callback"), {"state": state, "code": "test-code"}
            )
            self.assertEqual(network.call_count, 2)
        return response

    def test_real_callback_flow_creates_minimal_member_without_tokens_or_email(self):
        query = self.begin()
        self.assertEqual(query["scope"], ["profile_nickname"])
        self.assertEqual(
            query["redirect_uri"], ["http://testserver/accounts/kakao/login/callback/"]
        )
        response = self.complete(query["state"][0])
        self.assertRedirects(response, reverse("accounts:organizer"), fetch_redirect_response=False)
        user = User.objects.get(pk=self.client.session[SESSION_KEY])
        self.assertEqual(user.nickname, "테스트 총무")
        self.assertEqual(user.email, "")
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_operator)
        self.assertFalse(user.is_staff)
        self.assertEqual(SocialAccount.objects.get().extra_data, {"nickname": "테스트 총무"})
        self.assertEqual(SocialToken.objects.count(), 0)
        self.assertEqual(EmailAddress.objects.count(), 0)
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME]
        self.assertGreater(int(cookie["max-age"]), 60 * 60 * 24 * 300)
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_existing_kakao_uid_reuses_account_and_updates_nickname(self):
        self.complete(self.begin()["state"][0])
        member_pk = self.client.session[SESSION_KEY]
        self.client.post(reverse("accounts:logout"))
        self.complete(self.begin()["state"][0], nickname="바뀐 닉네임")
        self.assertEqual(self.client.session[SESSION_KEY], member_pk)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(User.objects.get().nickname, "바뀐 닉네임")

    def test_role_and_destination_cannot_be_changed_by_post_parameters(self):
        query = self.begin(
            "organizer", next="https://attacker.example/", scope="account_email", process="connect"
        )
        self.assertEqual(query["scope"], ["profile_nickname"])
        response = self.complete(query["state"][0])
        self.assertRedirects(response, reverse("accounts:organizer"), fetch_redirect_response=False)
        self.assertFalse(User.objects.get().is_operator)

    def test_unlinked_seller_signup_creates_member_but_does_not_grant_access(self):
        response = self.complete(self.begin("seller")["state"][0])
        self.assertRedirects(
            response, reverse("accounts:denied", args=["seller"]), fetch_redirect_response=False
        )
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(self.client.get(reverse("accounts:seller")).status_code, 403)
        self.assertEqual(Cafe.objects.count(), 0)

    def test_operator_entry_does_not_promote_new_member(self):
        response = self.complete(self.begin("operator")["state"][0])
        self.assertRedirects(
            response, reverse("accounts:denied", args=["operator"]), fetch_redirect_response=False
        )
        self.assertFalse(User.objects.get().is_operator)
        self.assertEqual(self.client.get(reverse("accounts:operator")).status_code, 403)

    def test_two_login_tabs_do_not_replace_the_first_role(self):
        organizer_state = self.begin("organizer")["state"][0]
        self.begin("operator")
        response = self.complete(organizer_state)
        self.assertRedirects(response, reverse("accounts:organizer"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["active_role"], "organizer")
        self.assertFalse(User.objects.get().is_operator)

    def test_inactive_member_is_not_logged_in_or_allowed_to_open_cafe(self):
        user = User.objects.create_user("inactive", is_active=False)
        SocialAccount.objects.create(user=user, provider="kakao", uid="12345")
        cafe = Cafe.objects.create(name="닫힌 카페", seller=user)
        response = self.complete(self.begin("seller")["state"][0])
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(SESSION_KEY, self.client.session)
        cafe.refresh_from_db()
        self.assertFalse(cafe.is_open)

    def test_initial_owner_selection_requires_server_config_and_exact_existing_member(self):
        owner = User.objects.create_user("selected-owner")
        SocialAccount.objects.create(user=owner, provider="kakao", uid="12345")
        with override_settings(INITIAL_OPERATOR_ACCOUNT_CODE=str(owner.account_code)):
            response = self.complete(self.begin("operator")["state"][0])
        self.assertRedirects(response, reverse("accounts:operator"), fetch_redirect_response=False)
        owner.refresh_from_db()
        self.assertTrue(owner.is_operator)

    @override_settings(INITIAL_OPERATOR_ACCOUNT_CODE="not-this-members-code")
    def test_owner_config_does_not_promote_first_arbitrary_signup(self):
        self.complete(self.begin("operator")["state"][0])
        self.assertFalse(User.objects.get().is_operator)

    def test_bad_expired_and_replayed_state_never_call_kakao(self):
        state = self.begin()["state"][0]
        with patch("requests.sessions.Session.request") as network:
            response = self.client.get(reverse("kakao_callback"), {"state": "wrong", "code": "x"})
            self.assertRedirects(
                response, reverse("accounts:login-error"), fetch_redirect_response=False
            )
            network.assert_not_called()
        session = self.client.session
        states = session["socialaccount_states"]
        states[state][1] = time.time() - 700
        session["socialaccount_states"] = states
        session.save()
        with patch("requests.sessions.Session.request") as network:
            response = self.client.get(reverse("kakao_callback"), {"state": state, "code": "x"})
            self.assertRedirects(
                response, reverse("accounts:login-error"), fetch_redirect_response=False
            )
            network.assert_not_called()
        valid_state = self.begin()["state"][0]
        self.complete(valid_state)
        with patch("requests.sessions.Session.request") as network:
            response = self.client.get(
                reverse("kakao_callback"), {"state": valid_state, "code": "x"}
            )
            self.assertRedirects(
                response, reverse("accounts:login-error"), fetch_redirect_response=False
            )
            network.assert_not_called()

    def test_denial_and_network_failure_keep_user_logged_out(self):
        state = self.begin()["state"][0]
        self.client.get(reverse("kakao_callback"), {"state": state, "error": "access_denied"})
        self.assertNotIn(SESSION_KEY, self.client.session)
        state = self.begin()["state"][0]
        with patch("requests.sessions.Session.request", side_effect=requests.Timeout):
            response = self.client.get(reverse("kakao_callback"), {"state": state, "code": "x"})
            self.assertRedirects(
                response, reverse("accounts:login-error"), fetch_redirect_response=False
            )
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(User.objects.count(), 0)

    def test_seller_callback_opens_only_own_cafe(self):
        user = User.objects.create_user("seller", nickname="판매자")
        SocialAccount.objects.create(user=user, provider="kakao", uid="12345")
        cafe = Cafe.objects.create(name="우리 카페", seller=user)
        other = Cafe.objects.create(name="다른 카페")
        self.complete(self.begin("seller")["state"][0])
        cafe.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(cafe.is_open)
        self.assertFalse(other.is_open)
        self.assertEqual(self.client.session["seller_cafe_id"], cafe.pk)

    def test_auth_start_requires_post_and_csrf(self):
        url = reverse("accounts:start", args=["organizer"])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post(url).status_code, 403)

    @override_settings(KAKAO_LOGIN_ENABLED=False)
    def test_missing_configuration_blocks_login_and_callback_without_network(self):
        with patch("requests.sessions.Session.request") as network:
            self.assertEqual(
                self.client.post(reverse("accounts:start", args=["organizer"])).status_code, 503
            )
            self.assertEqual(self.client.get(reverse("kakao_callback")).status_code, 503)
            network.assert_not_called()


class RolePermissionTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user("member", nickname="총무")
        self.owner = User.objects.create_user("owner", nickname="운영자", is_operator=True)
        self.cafe = Cafe.objects.create(name="파일럿 카페", seller=self.owner)

    def sign_in(self, user):
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")

    def test_anonymous_views_redirect_to_correct_role_entrance(self):
        for role in ("organizer", "seller", "operator"):
            self.assertRedirects(
                self.client.get(reverse(f"accounts:{role}")), reverse(f"accounts:entry-{role}")
            )

    def test_member_cannot_read_or_modify_operator_or_seller_resources(self):
        self.sign_in(self.member)
        self.assertEqual(self.client.get(reverse("accounts:organizer")).status_code, 200)
        for role in ("seller", "operator"):
            self.assertEqual(self.client.get(reverse(f"accounts:{role}")).status_code, 403)
        mutations = [
            (reverse("accounts:cafe-create"), {"name": "무단 카페"}),
            (
                reverse("accounts:seller-link", args=[self.cafe.pk]),
                {"account_code": self.member.account_code},
            ),
            (reverse("accounts:seller-unlink", args=[self.cafe.pk]), {}),
        ]
        for url, data in mutations:
            self.assertEqual(self.client.post(url, data).status_code, 403)
        self.cafe.refresh_from_db()
        self.assertEqual(self.cafe.seller_id, self.owner.pk)
        self.assertEqual(Cafe.objects.count(), 1)

    def test_default_home_has_no_role_switches_for_ordinary_member(self):
        self.sign_in(self.member)
        self.assertRedirects(self.client.get(reverse("home")), reverse("accounts:organizer"))
        response = self.client.get(reverse("accounts:organizer"))
        for text in (
            "총무 화면",
            "판매자 화면",
            "운영자 화면",
            "판매자 페이지 이동",
            "운영자 페이지 이동",
            'class="role-label"',
        ):
            self.assertNotContains(response, text)
        self.assertNotContains(response, str(self.member.account_code))
        self.assertContains(response, reverse("accounts:profile"))

    def test_profile_only_shows_logged_in_members_data_and_code(self):
        self.assertRedirects(
            self.client.get(reverse("accounts:profile")), reverse("accounts:entry-organizer")
        )
        self.sign_in(self.member)
        response = self.client.get(reverse("accounts:profile"), {"user": self.owner.pk})
        self.assertContains(response, str(self.member.account_code))
        self.assertNotContains(response, str(self.owner.account_code))
        self.assertContains(response, "내 주문 이력")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_seller_footer_uses_authorized_entry_and_disappears_when_unlinked(self):
        self.sign_in(self.owner)
        response = self.client.get(reverse("accounts:organizer"))
        self.assertContains(response, "판매자 페이지 이동")
        self.assertContains(response, "운영자 페이지 이동")
        self.assertNotContains(response, "총무 화면")
        self.cafe.refresh_from_db()
        self.assertFalse(self.cafe.is_open)
        self.assertRedirects(
            self.client.post(reverse("accounts:start", args=["seller"])),
            reverse("accounts:seller"),
        )
        self.cafe.refresh_from_db()
        self.assertTrue(self.cafe.is_open)
        self.assertContains(self.client.get(reverse("accounts:seller")), "내 주문으로 이동")
        self.client.post(reverse("accounts:seller-unlink", args=[self.cafe.pk]))
        self.assertNotContains(self.client.get(reverse("accounts:organizer")), "판매자 페이지 이동")
        self.assertEqual(
            self.client.post(reverse("accounts:start", args=["seller"])).status_code, 403
        )

    def test_one_account_uses_all_roles_and_only_seller_entry_opens_cafe(self):
        self.sign_in(self.owner)
        for role in ("organizer", "operator"):
            self.assertEqual(self.client.get(reverse(f"accounts:{role}")).status_code, 200)
            self.client.post(reverse("accounts:start", args=[role]))
        self.cafe.refresh_from_db()
        self.assertFalse(self.cafe.is_open)
        self.client.get(reverse("accounts:seller"))
        self.cafe.refresh_from_db()
        self.assertFalse(self.cafe.is_open)
        self.client.post(reverse("accounts:start", args=["seller"]))
        self.cafe.refresh_from_db()
        self.assertTrue(self.cafe.is_open)
        self.client.post(reverse("accounts:start", args=["organizer"]))
        self.cafe.refresh_from_db()
        self.assertTrue(self.cafe.is_open)
        self.client.post(reverse("accounts:logout"))
        self.cafe.refresh_from_db()
        self.assertFalse(self.cafe.is_open)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(
            list(self.cafe.access_events.order_by("pk").values_list("action", flat=True)),
            ["opened", "closed"],
        )

    def test_organizer_login_and_logout_do_not_change_existing_cafe_state(self):
        self.cafe.is_open = True
        self.cafe.save()
        self.sign_in(self.owner)
        self.client.post(reverse("accounts:start", args=["organizer"]))
        self.client.post(reverse("accounts:logout"))
        self.cafe.refresh_from_db()
        self.assertTrue(self.cafe.is_open)

    def test_logout_is_post_only_with_csrf(self):
        self.sign_in(self.owner)
        self.client.post(reverse("accounts:start", args=["seller"]))
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(protected.post(reverse("accounts:logout")).status_code, 403)
        self.cafe.refresh_from_db()
        self.assertTrue(self.cafe.is_open)

    def test_operator_links_existing_member_then_revokes_access_and_closes_cafe(self):
        self.sign_in(self.owner)
        link_url = reverse("accounts:seller-link", args=[self.cafe.pk])
        self.assertEqual(
            self.client.post(link_url, {"account_code": str(self.member.account_code)}).status_code,
            302,
        )
        self.cafe.refresh_from_db()
        self.assertEqual(self.cafe.seller_id, self.member.pk)
        self.assertFalse(self.cafe.is_open)
        seller = Client()
        seller.force_login(self.member, backend="django.contrib.auth.backends.ModelBackend")
        seller.post(reverse("accounts:start", args=["seller"]))
        self.assertEqual(
            self.client.post(reverse("accounts:seller-unlink", args=[self.cafe.pk])).status_code,
            302,
        )
        self.cafe.refresh_from_db()
        self.assertIsNone(self.cafe.seller_id)
        self.assertFalse(self.cafe.is_open)
        self.assertEqual(seller.get(reverse("accounts:seller")).status_code, 403)

    def test_second_cafe_cannot_claim_already_assigned_seller(self):
        self.sign_in(self.owner)
        second = Cafe.objects.create(name="다른 카페")
        response = self.client.post(
            reverse("accounts:seller-link", args=[second.pk]),
            {"account_code": str(self.owner.account_code)},
        )
        self.assertEqual(response.status_code, 400)
        self.cafe.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.cafe.seller_id, self.owner.pk)
        self.assertIsNone(second.seller_id)

    def test_operator_connection_never_marks_cafe_open(self):
        self.sign_in(self.owner)
        second = Cafe.objects.create(name="두 번째 카페")
        self.client.post(
            reverse("accounts:seller-link", args=[second.pk]),
            {"account_code": str(self.member.account_code)},
        )
        second.refresh_from_db()
        self.assertFalse(second.is_open)

    def test_own_nickname_is_escaped_and_other_member_codes_are_not_shared(self):
        self.member.nickname = "<script>alert(1)</script>"
        self.member.save()
        self.sign_in(self.member)
        response = self.client.get(reverse("accounts:organizer"))
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, str(self.owner.account_code))
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_bootstrap_requires_explicit_existing_kakao_account_code(self):
        with self.assertRaises(CommandError):
            call_command("grant_operator", "invalid-code", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("grant_operator", str(self.member.account_code), stdout=StringIO())
        SocialAccount.objects.create(user=self.member, provider="kakao", uid="222")
        call_command("grant_operator", str(self.member.account_code), stdout=StringIO())
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_operator)
        call_command(
            "grant_operator", str(self.member.account_code), revoke=True, stdout=StringIO()
        )
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_operator)

    def test_test_account_setup_requires_kakao_and_preserves_existing_cafe(self):
        with self.assertRaises(CommandError):
            call_command("setup_test_account", str(self.owner.account_code), stdout=StringIO())
        SocialAccount.objects.create(user=self.owner, provider="kakao", uid="999")
        for _ in range(2):
            call_command("setup_test_account", str(self.owner.account_code), stdout=StringIO())
        self.assertEqual(Cafe.objects.filter(seller=self.owner).count(), 1)
        self.cafe.refresh_from_db()
        self.assertFalse(self.cafe.is_open)
        SocialAccount.objects.create(user=self.member, provider="kakao", uid="888")
        with self.assertRaises(CommandError):
            call_command("setup_test_account", str(self.member.account_code), stdout=StringIO())
