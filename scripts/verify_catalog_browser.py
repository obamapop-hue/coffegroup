"""Verify menu/option editing against a disposable local database, never Google Cloud."""

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
from cafes.catalog import add_basic_options
from cafes.models import Cafe, Menu
from orders.models import Room
from orders.services import create_room

if settings.DATABASES["default"].get("HOST", "") not in ("", "localhost", "127.0.0.1", "::1"):
    raise RuntimeError("Only local databases are allowed.")
settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_yeongcha_catalog_browser"


@override_settings(
    ALLOWED_HOSTS=["localhost", "testserver"],
    DEBUG=True,
    SECURE_SSL_REDIRECT=False,
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
)
class BrowserCheck(StaticLiveServerTestCase):
    def test_integrated_editor(self):
        operator = User.objects.create_user("catalog-browser-operator", is_operator=True)
        client = Client()
        client.force_login(operator)
        cases = []
        for width in [320, 390, 1440]:
            seller = User.objects.create_user(f"catalog-seller-{width}")
            cafe = Cafe.objects.create(name=f"확인 카페 {width}", seller=seller, is_open=True)
            menu = Menu.objects.create(cafe=cafe, name="아메리카노", price=3000, category="커피")
            add_basic_options(menu)
            room = create_room(user=operator, cafe_id=cafe.pk)
            cases.append({"width": width, "cafe": cafe.pk, "room": room.pk})
        config = {
            "base": self.live_server_url,
            "cookie": client.cookies["sessionid"].value,
            "cases": cases,
            "output": str(ROOT / ".cache" / "catalog-browser"),
        }
        Path(config["output"]).mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["node", str(ROOT / "scripts" / "verify_catalog_browser.cjs")],
            input=json.dumps(config),
            text=True,
            cwd=ROOT,
            timeout=180,
        )
        self.assertEqual(result.returncode, 0)
        for case in cases:
            room = Room.objects.get(pk=case["room"])
            self.assertEqual(room.menu_snapshot[0]["price"], 3000)
            self.assertEqual(room.menu_snapshot[0]["groups"][1]["choices"][0]["name"], "샷 +0")
            menu = Menu.objects.get(cafe_id=case["cafe"], name="아메리카노")
            self.assertEqual(menu.price, 4100)
            self.assertEqual(menu.option_groups.get(name="샷 추가").choices.count(), 5)
            self.assertEqual(menu.option_groups.get(name="시럽 추가").choices.count(), 2)
            latte = Menu.objects.get(cafe_id=case["cafe"], name="카페라떼")
            self.assertEqual(latte.option_groups.get(name="디카페인").choices.get().name, "기본")


if __name__ == "__main__":
    sys.exit(
        bool(DiscoverRunner(verbosity=1, interactive=False).run_tests(["__main__.BrowserCheck"]))
    )
