"""Exercise history filters against a disposable local database only."""

# ruff: noqa: E402
import json
import os
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.test.runner import DiscoverRunner
from django.utils import timezone

from accounts.models import User
from cafes.models import Cafe, Menu
from orders.collection import add_submission
from orders.services import create_room

if settings.DATABASES["default"].get("HOST", "") not in ("", "localhost", "127.0.0.1", "::1"):
    raise RuntimeError("Only a local development database is allowed.")
settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_yeongcha_history_browser"


@override_settings(
    ALLOWED_HOSTS=["localhost", "testserver"],
    DEBUG=True,
    SECURE_SSL_REDIRECT=False,
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
)
class HistoryBrowserCheck(StaticLiveServerTestCase):
    def test_filters_summary_and_pagination(self):
        operator = User.objects.create_user("history-browser-operator", is_operator=True)
        client = Client()
        client.force_login(operator)
        cases = []
        for width in [320, 390, 1440]:
            first = User.objects.create_user(f"first-{width}", nickname=f"김총무{width}")
            second = User.objects.create_user(f"second-{width}", nickname=f"이총무{width}")
            cafe = Cafe.objects.create(name=f"첫 카페 {width}", seller=first, is_open=True)
            other = Cafe.objects.create(name=f"둘째 카페 {width}", seller=second, is_open=True)
            menu = Menu.objects.create(cafe=cafe, name="아메리카노", price=3000)
            other_menu = Menu.objects.create(cafe=other, name="라떼", price=4000)
            with patch(
                "orders.services.timezone.now", return_value=timezone.now() - timedelta(days=1)
            ):
                create_room(user=first, cafe_id=cafe.pk)
            for _ in range(22):
                create_room(user=first, cafe_id=cafe.pk)
            room = create_room(user=first, cafe_id=cafe.pk)
            other_room = create_room(user=second, cafe_id=other.pk)
            for member, order, item, quantity in [
                (first, room, menu, 2),
                (second, other_room, other_menu, 3),
            ]:
                add_submission(
                    user=member,
                    room_id=order.pk,
                    nickname="참여자 별칭",
                    items=[{"menu_id": item.pk, "quantity": quantity}],
                    request_key=uuid.uuid4(),
                )
            cases.append(
                {
                    "width": width,
                    "cafe": cafe.pk,
                    "name": cafe.name,
                    "member": first.nickname,
                    "other_cafe": other.pk,
                    "other_name": other.name,
                    "other_member": second.nickname,
                    "room": room.pk,
                }
            )
        output = ROOT / ".cache" / "history-browser"
        output.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["node", str(ROOT / "scripts" / "verify_history_browser.cjs")],
            input=json.dumps(
                {
                    "base": self.live_server_url,
                    "cookie": client.cookies["sessionid"].value,
                    "cases": cases,
                    "date": timezone.localdate().isoformat(),
                    "output": str(output),
                }
            ),
            text=True,
            cwd=ROOT,
            timeout=150,
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    sys.exit(
        bool(
            DiscoverRunner(verbosity=1, interactive=False).run_tests(
                ["__main__.HistoryBrowserCheck"]
            )
        )
    )
