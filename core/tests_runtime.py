import subprocess
import sys
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import DeadlineWorkerState
from core.runtime import deadline_worker_ready, record_deadline_success, revision_key
from scripts.run_services import supervise


class SupervisorTests(SimpleTestCase):
    def test_worker_exit_stops_web_sibling_and_causes_restart(self):
        processes = []
        spawn = subprocess.Popen

        def tracked(command):
            process = spawn(command)
            processes.append(process)
            return process

        with patch("scripts.run_services.subprocess.Popen", side_effect=tracked):
            result = supervise(
                [
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    [sys.executable, "-c", "pass"],
                ]
            )
        self.assertEqual(result, 1)
        self.assertEqual(len(processes), 2)
        self.assertTrue(all(process.poll() is not None for process in processes))


class DeadlineHealthTests(TestCase):
    @override_settings(DEBUG=False, ALLOWED_HOSTS=["site.example"], REQUIRE_DEADLINE_WORKER=False)
    def test_internal_probe_host_is_allowed_only_for_read_only_health_routes(self):
        self.assertEqual(
            self.client.get("/health/live/", HTTP_HOST="internal-probe").status_code, 200
        )
        self.assertEqual(
            self.client.get("/health/ready/", HTTP_HOST="internal-probe").status_code, 200
        )
        self.assertEqual(
            self.client.post("/health/live/", HTTP_HOST="internal-probe").status_code, 405
        )
        self.assertEqual(
            self.client.get("/", HTTP_HOST="internal-probe", secure=True).status_code, 400
        )

    @override_settings(REQUIRE_DEADLINE_WORKER=True)
    def test_missing_stale_and_other_revision_do_not_report_ready(self):
        import os

        with patch.dict(os.environ, {"K_REVISION": "revision-new"}):
            self.assertEqual(self.client.get(reverse("health-ready")).status_code, 503)
            DeadlineWorkerState.objects.create(
                revision="revision-old", last_success_at=timezone.now()
            )
            self.assertFalse(deadline_worker_ready())
            record_deadline_success()
            self.assertEqual(self.client.get(reverse("health-ready")).status_code, 200)
            DeadlineWorkerState.objects.filter(revision=revision_key()).update(
                last_success_at=timezone.now() - timedelta(seconds=61)
            )
            response = self.client.get(reverse("health-ready"))
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"status": "unavailable"})

    def test_another_instance_of_same_revision_cannot_mask_a_stopped_worker(self):
        import os

        with patch.dict(
            os.environ, {"K_REVISION": "same-revision", "YEONGCHA_RUNTIME_INSTANCE": "one"}
        ):
            record_deadline_success()
        with patch.dict(
            os.environ, {"K_REVISION": "same-revision", "YEONGCHA_RUNTIME_INSTANCE": "two"}
        ):
            self.assertFalse(deadline_worker_ready())

    def test_worker_records_success_only_after_successful_deadline_cycle(self):
        call_command("run_deadlines", once=True, stdout=StringIO())
        self.assertTrue(deadline_worker_ready())
        DeadlineWorkerState.objects.all().delete()
        from django.core.management.base import CommandError
        from django.db import OperationalError

        with (
            patch(
                "orders.management.commands.run_deadlines.expire_due_rooms",
                side_effect=OperationalError("private-error"),
            ),
            self.assertRaises(CommandError),
        ):
            call_command("run_deadlines", once=True, stdout=StringIO())
        self.assertFalse(DeadlineWorkerState.objects.exists())

    def test_liveness_does_not_depend_on_worker_or_database(self):
        with patch("core.runtime.DeadlineWorkerState.objects.filter", side_effect=AssertionError):
            self.assertEqual(self.client.get(reverse("health-live")).status_code, 200)
