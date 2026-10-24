"""Verify deadline processing after a worker restart, with no web requests or real data.

Runs against a disposable local PostgreSQL DB only. Uses shortened remaining time
for the restart scenario; this is not another real 30-minute/5-minute elapsed test.
"""

# Application model imports follow Django initialization.
# ruff: noqa: E402
import os
import subprocess
import sys
import time
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
from django.db import connection
from django.test import TransactionTestCase
from django.test.runner import DiscoverRunner
from django.utils import timezone

from core.models import DeadlineWorkerState
from orders.collection import add_submission, close_collection
from orders.dispatch import preview_order, send_order
from orders.models import Room
from orders.services import create_room
from orders.test_selling import SellingFixture

if settings.DATABASES["default"].get("HOST", "") not in ("", "localhost", "127.0.0.1", "::1"):
    raise RuntimeError("Only a local development PostgreSQL host is allowed.")
settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_yeongcha_deadline_restart"


class DeadlineRestartCheck(SellingFixture, TransactionTestCase):
    def test_no_visitors_deadlines_are_processed_after_restart(self):
        finish = timezone.now() + timedelta(seconds=8)
        with patch("orders.services.timezone.now", return_value=finish - timedelta(minutes=30)):
            unsent = create_room(user=self.owner, cafe_id=self.cafe.pk)
        with patch("orders.services.timezone.now", return_value=finish - timedelta(minutes=5)):
            pending = create_room(user=self.owner, cafe_id=self.cafe.pk)
            add_submission(
                room_id=pending.pk,
                nickname="복구 확인",
                items=self.items(),
                request_key=uuid.uuid4(),
            )
            close_collection(user=self.owner, room_id=pending.pk)
            _, _, token = preview_order(user=self.owner, room_id=pending.pk)
            send_order(user=self.owner, room_id=pending.pk, confirmation_token=token)
        child_code = """
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.conf import settings
settings.DATABASES['default']['NAME']=os.environ['YEONGCHA_VERIFICATION_DB']
from django.core.management import call_command
call_command('run_deadlines', interval=0.25)
"""
        env = dict(
            os.environ,
            YEONGCHA_VERIFICATION_DB=connection.settings_dict["NAME"],
            K_REVISION="restart-verification",
        )

        def start():
            return subprocess.Popen(
                [sys.executable, "-c", child_code], cwd=ROOT, env=env, stdout=subprocess.DEVNULL
            )

        worker = start()
        try:
            for _ in range(80):
                if DeadlineWorkerState.objects.filter(
                    revision="restart-verification:standalone"
                ).exists():
                    break
                time.sleep(0.05)
            else:
                self.fail("Worker did not report a successful initial cycle.")
            worker.terminate()
            worker.wait(timeout=5)
            while timezone.now() <= finish:
                time.sleep(0.1)
            self.assertEqual(Room.objects.get(pk=unsent.pk).status, "open")
            self.assertEqual(Room.objects.get(pk=pending.pk).status, "pending")
            worker = start()
            for _ in range(100):
                if Room.objects.get(pk=pending.pk).status == "rejected":
                    break
                time.sleep(0.05)
            self.assertEqual(Room.objects.get(pk=unsent.pk).status, "expired")
            self.assertEqual(Room.objects.get(pk=pending.pk).rejection_reason, "5분 미응답")
            self.assertEqual(unsent.events.filter(action="expired").count(), 1)
            self.assertEqual(pending.events.filter(action="timed_out").count(), 1)
            self.assertEqual(unsent.notifications.filter(kind="expired").count(), 1)
            self.assertEqual(pending.notifications.filter(kind="rejected").count(), 1)
            print(
                "No web requests: restarted worker processed both overdue deadlines "
                "and saved one event/notice per order.",
                flush=True,
            )
        finally:
            if worker.poll() is None:
                worker.terminate()
                worker.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(bool(DiscoverRunner(verbosity=1).run_tests(["__main__.DeadlineRestartCheck"])))
