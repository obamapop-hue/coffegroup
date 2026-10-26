"""Use fake members and mocked Kakao on a disposable local PostgreSQL database."""

# ruff: noqa: E402
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.test.runner import DiscoverRunner

from accounts.models import MembershipRecord, User
from cafes.models import Cafe, Menu

if settings.DATABASES["default"].get("HOST", "") not in ("", "localhost", "127.0.0.1", "::1"):
    raise RuntimeError("Only a local development database is allowed.")
settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_yeongcha_membership_browser"


@override_settings(
    ALLOWED_HOSTS=["localhost", "testserver"],
    DEBUG=True,
    SECURE_SSL_REDIRECT=False,
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
    KAKAO_LOGIN_ENABLED=True,
    KAKAO_ADMIN_KEY="synthetic-only",
    KAKAO_WITHDRAWAL_ENABLED=True,
)
class MembershipBrowserCheck(StaticLiveServerTestCase):
    def test_real_csrf_confirmation_and_private_preview(self):
        cases = []
        for width in [320, 390, 1440]:
            member = User.objects.create_user(f"member-{width}", nickname=f"확인용 회원 {width}")
            SocialAccount.objects.create(user=member, provider="kakao", uid=str(width))
            unverified = Client()
            unverified.force_login(member)
            verified = Client()
            verified.force_login(member)
            session = verified.session
            session["withdrawal_verified"] = {"user": member.pk, "at": time.time()}
            session.save()
            cases.append(
                {
                    "width": width,
                    "unverified": unverified.cookies["sessionid"].value,
                    "verified": verified.cookies["sessionid"].value,
                    "id": member.pk,
                    "record": member.membership_record.pk,
                    "nickname": member.nickname,
                }
            )
        operator = User.objects.create_user(
            "preview-operator", nickname="시험 운영자", is_operator=True
        )
        seller = User.objects.create_user("browser-cafe-seller", nickname="시험 판매자")
        cafe = Cafe.objects.create(name="회원 기능 시험 카페", seller=seller, is_open=True)
        Menu.objects.create(cafe=cafe, name="아메리카노", price=3000)
        operator_client = Client()
        operator_client.force_login(operator)
        output = ROOT / ".cache" / "withdrawal"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "base": self.live_server_url,
            "cases": cases,
            "operator": operator_client.cookies["sessionid"].value,
            "cafe": cafe.pk,
            "output": str(output),
        }
        with (
            patch("accounts.withdrawal_views.verify_admin_member", return_value=True),
            patch("accounts.withdrawal.unlink_from_kakao", return_value=True),
        ):
            result = subprocess.run(
                ["node", str(ROOT / "scripts/verify_withdrawal_browser.cjs")],
                input=json.dumps(config),
                text=True,
                timeout=90,
                cwd=ROOT,
            )
        self.assertEqual(result.returncode, 0)
        self.assertFalse(User.objects.filter(pk__in=[case["id"] for case in cases]).exists())
        self.assertTrue(User.objects.filter(pk=operator.pk).exists())
        self.assertEqual(
            MembershipRecord.objects.filter(
                pk__in=[case["record"] for case in cases],
                user__isnull=True,
                withdrawn_at__isnull=False,
                unlink_confirmed_at__isnull=False,
            ).count(),
            len(cases),
        )


if __name__ == "__main__":
    sys.exit(bool(DiscoverRunner(verbosity=1).run_tests(["__main__.MembershipBrowserCheck"])))
