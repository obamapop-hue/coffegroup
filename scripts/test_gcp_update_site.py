"""Check update ordering and avoid resetting authentication during ordinary deployments."""

import unittest

from gcp_deploy_test import JOB, SERVICE
from gcp_prepare_database import GoogleCloud, SetupError
from gcp_update_site import update_site


class UpdateCloud(GoogleCloud):
    def __init__(self):
        super().__init__("active-venture-449808-a5")
        self.calls = []
        self.migration_ok = True
        self.build_status = "SUCCESS"
        self.owned = True
        self.probe_flags_supported = True

    def request(self, args, **kwargs):
        self.calls.append(args)
        if args == ["run", "services", "update", "--help"]:
            return "--startup-probe --liveness-probe" if self.probe_flags_supported else ""
        if args[:3] in (["run", "services", "list"], ["run", "jobs", "list"]):
            name = SERVICE if args[1] == "services" else JOB
            return [
                {
                    "metadata": {
                        "name": name,
                        "labels": {"yeongcha-stage": "test" if self.owned else "other"},
                    }
                }
            ]
        if args[:3] == ["run", "services", "describe"]:
            return {"status": {"url": "https://yeongcha-test-example.run.app"}}
        if args[:2] == ["builds", "submit"]:
            self.tag = next(a.removeprefix("--tag=") for a in args if a.startswith("--tag="))
            return {"id": "example-build"}
        if args[:2] == ["builds", "describe"]:
            return {
                "status": self.build_status,
                "results": {"images": [{"name": self.tag, "digest": "sha256:" + "a" * 64}]},
            }
        if args[:3] == ["run", "jobs", "execute"]:
            return {
                "status": {
                    "conditions": [{"type": "Completed", "status": str(self.migration_ok)}],
                    "failedCount": int(not self.migration_ok),
                }
            }
        if args[:3] in (["run", "jobs", "update"], ["run", "services", "update"]):
            return {}
        raise AssertionError(args)


class UpdateSiteTests(unittest.TestCase):
    def update(self, cloud):
        self.checked = []
        return update_site(
            cloud,
            "/source",
            "a" * 40,
            report=lambda _: None,
            site_check=self.checked.append,
            pause=lambda _: None,
        )

    def test_image_only_update_runs_migrations_before_web_and_checks_original_url(self):
        cloud = UpdateCloud()
        self.update(cloud)
        job = next(c for c in cloud.calls if c[:3] == ["run", "jobs", "update"])
        execute = next(c for c in cloud.calls if c[:3] == ["run", "jobs", "execute"])
        web = next(c for c in cloud.calls if c[:4] == ["run", "services", "update", SERVICE])
        self.assertLess(cloud.calls.index(job), cloud.calls.index(execute))
        self.assertLess(cloud.calls.index(execute), cloud.calls.index(web))
        self.assertTrue(any(c.endswith("@sha256:" + "a" * 64) for c in web))
        self.assertEqual(self.checked, ["https://yeongcha-test-example.run.app"])
        self.assertEqual(len(job), 6)
        self.assertFalse(any("secrets" in value or "set-env-vars" in value for value in web))
        self.assertIn("--update-env-vars=REQUIRE_DEADLINE_WORKER=True", web)
        self.assertTrue(
            any(c.startswith("--startup-probe=httpGet.path=/health/ready/") for c in web)
        )
        self.assertTrue(
            any(c.startswith("--liveness-probe=httpGet.path=/health/ready/") for c in web)
        )

    def test_failed_migrations_leave_current_web_image_untouched(self):
        cloud = UpdateCloud()
        cloud.migration_ok = False
        with self.assertRaises(SetupError):
            self.update(cloud)
        self.assertFalse(any(c[:4] == ["run", "services", "update", SERVICE] for c in cloud.calls))

    def test_failed_build_never_runs_migrations(self):
        cloud = UpdateCloud()
        cloud.build_status = "FAILURE"
        with self.assertRaises(SetupError):
            self.update(cloud)
        self.assertFalse(any(c[:3] == ["run", "jobs", "update"] for c in cloud.calls))

    def test_other_service_label_stops_before_build(self):
        cloud = UpdateCloud()
        cloud.owned = False
        with self.assertRaises(SetupError):
            self.update(cloud)
        self.assertFalse(any(c[:2] == ["builds", "submit"] for c in cloud.calls))

    def test_unsupported_health_flags_stop_before_any_build_or_mutation(self):
        cloud = UpdateCloud()
        cloud.probe_flags_supported = False
        with self.assertRaises(SetupError):
            self.update(cloud)
        self.assertFalse(any(c[:2] == ["builds", "submit"] for c in cloud.calls))
