"""Verify combined seller alert setup using real HTML/audio and a disposable local DB."""

# Application imports follow Django initialization.
# ruff: noqa: E402
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.test.runner import DiscoverRunner

from accounts.models import User
from cafes.models import Cafe

if settings.DATABASES["default"].get("HOST", "") not in ("", "localhost", "127.0.0.1", "::1"):
    raise RuntimeError("Only a local development database is allowed.")
settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_yeongcha_seller_alerts_browser"


@override_settings(
    ALLOWED_HOSTS=["localhost", "testserver"],
    DEBUG=True,
    SECURE_SSL_REDIRECT=False,
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
)
class SellerAlertsBrowserCheck(StaticLiveServerTestCase):
    def test_combined_setup_and_failures(self):
        seller = User.objects.create_user("alerts-browser-seller")
        Cafe.objects.create(name="알림 확인 카페", seller=seller, is_open=True)
        client = Client()
        client.force_login(seller)
        output = ROOT / ".cache" / "seller-alerts-browser"
        output.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["node", str(ROOT / "scripts/verify_phase7_browser_fallbacks.cjs")],
            input=json.dumps(
                {
                    "base": self.live_server_url,
                    "timeoutSeller": client.cookies["sessionid"].value,
                    "output": str(output),
                }
            ),
            text=True,
            timeout=120,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    sys.exit(bool(DiscoverRunner(verbosity=1).run_tests(["__main__.SellerAlertsBrowserCheck"])))
