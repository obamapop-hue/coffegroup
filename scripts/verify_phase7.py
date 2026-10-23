"""Local-only browser and real five-minute deadline verification using a disposable DB.

Requires Node, Playwright and Chromium in addition to the normal dev dependencies.
Never connects to Google/Railway, creates real users, or changes the development DB.
"""
# Django setup must precede imports of application models.
# ruff: noqa: E402

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import connection
from django.test import Client, override_settings
from django.test.runner import DiscoverRunner

from cafes.models import Cafe, Menu
from orders.collection import add_submission, close_collection
from orders.dispatch import preview_order
from orders.models import Room
from orders.services import create_room
from orders.test_selling import SellingFixture

if settings.DATABASES["default"].get("HOST", "") not in ("", "localhost", "127.0.0.1", "::1"):
    raise RuntimeError("Browser verification only supports a local development PostgreSQL host.")

settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_yeongcha_phase7_browser"


@override_settings(
    ALLOWED_HOSTS=["localhost", "testserver"],
    DEBUG=True,
    SECURE_SSL_REDIRECT=False,
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
)
class Phase7BrowserCheck(SellingFixture, StaticLiveServerTestCase):
    def test_real_browser_and_deadline(self):
        timeout_cafe = Cafe.objects.create(name="기한 검증 카페", seller=self.other, is_open=True)
        timeout_menu = Menu.objects.create(cafe=timeout_cafe, name="검증 커피", price=1000)
        timeout_final = self.another_order(cafe=timeout_cafe, menu=timeout_menu)
        prepared = {}
        for name in ["first", "second", "third", "offline", "recover", "external", "logout"]:
            room = create_room(user=self.owner, cafe_id=self.cafe.pk)
            add_submission(
                room_id=room.pk,
                nickname="공개하면안되는참여자",
                items=self.items(),
                request_key=uuid.uuid4(),
            )
            close_collection(user=self.owner, room_id=room.pk)
            _, _, token = preview_order(user=self.owner, room_id=room.pk)
            prepared[name] = {"id": room.pk, "token": token}
        seller = Client()
        seller.force_login(self.seller)
        timeout_seller = Client()
        timeout_seller.force_login(self.other)
        output = ROOT / ".cache" / "phase7"
        output.mkdir(parents=True, exist_ok=True)
        cfg = {
            "base": self.live_server_url,
            "owner": self.client.cookies["sessionid"].value,
            "seller": seller.cookies["sessionid"].value,
            "timeoutSeller": timeout_seller.cookies["sessionid"].value,
            "orders": prepared,
            "timeoutOrder": timeout_final.room_id,
            "sentAt": timeout_final.sent_at.timestamp() * 1000,
            "output": str(output),
        }
        worker_code = """
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.conf import settings
settings.DATABASES['default']['NAME'] = os.environ['YEONGCHA_VERIFICATION_DB']
from django.core.management import call_command
call_command('run_deadlines', interval=1)
"""
        env = dict(os.environ, YEONGCHA_VERIFICATION_DB=connection.settings_dict["NAME"])
        worker = subprocess.Popen(
            [sys.executable, "-c", worker_code], cwd=ROOT, env=env, stdout=subprocess.DEVNULL
        )
        try:
            for scenario in ("verify_phase7_browser_fallbacks.cjs", "verify_phase7_browser.cjs"):
                result = subprocess.run(
                    ["node", str(ROOT / "scripts" / scenario)],
                    input=json.dumps(cfg),
                    text=True,
                    timeout=380,
                    cwd=ROOT,
                )
                self.assertEqual(result.returncode, 0)
            timeout_room = Room.objects.get(pk=timeout_final.room_id)
            self.assertEqual(timeout_room.status, "rejected")
            self.assertEqual(timeout_room.rejection_reason, "5분 미응답")
            self.assertEqual(timeout_room.events.filter(action="timed_out").count(), 1)
            self.assertEqual(timeout_room.notifications.filter(kind="rejected").count(), 1)
        finally:
            worker.terminate()
            worker.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(bool(DiscoverRunner(verbosity=1).run_tests(["__main__.Phase7BrowserCheck"])))
