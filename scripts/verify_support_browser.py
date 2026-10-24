"""Local-only browser verification of private guest support and operator replies."""

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

if settings.DATABASES["default"].get("HOST", "") not in ("", "localhost", "127.0.0.1", "::1"):
    raise RuntimeError("Only a local PostgreSQL development host is allowed.")
settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_yeongcha_support_browser"


@override_settings(
    ALLOWED_HOSTS=["localhost", "testserver"],
    DEBUG=True,
    SECURE_SSL_REDIRECT=False,
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
)
class SupportBrowserCheck(StaticLiveServerTestCase):
    def test_guest_privacy_and_operator_reply(self):
        operator = User.objects.create_user("support-browser-operator", is_operator=True)
        client = Client()
        client.force_login(operator)
        output = ROOT / ".cache" / "support"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "base": self.live_server_url,
            "operator": client.cookies["sessionid"].value,
            "output": str(output),
        }
        result = subprocess.run(
            ["node", str(ROOT / "scripts/verify_support_browser.cjs")],
            input=json.dumps(config),
            text=True,
            timeout=90,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    sys.exit(bool(DiscoverRunner(verbosity=1).run_tests(["__main__.SupportBrowserCheck"])))
