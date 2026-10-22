"""Offline checks for migration failure, publication and persistent worker settings."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from gcp_deploy_test import (
    DIGEST,
    JOB,
    LABEL_KEY,
    LABEL_VALUE,
    SERVICE,
    SIGNING_SECRET,
    deploy_test,
    verify_execution,
)
from gcp_prepare_database import GoogleCloud, SetupError


class DeploymentModel(GoogleCloud):
    def __init__(self):
        super().__init__("active-venture-449808-a5")
        self.calls = []
        self.services = []
        self.jobs = []
        self.keys = {}
        self.migration_ok = True
        self.url = "https://yeongcha-test-example.du.a.run.app"
        self.public = False

    def request(self, args, *, data=None, raw=False):
        self.calls.append(args)
        if args[:3] == ["run", "services", "list"]:
            return self.services
        if args[:3] == ["run", "jobs", "list"]:
            return self.jobs
        if args == ["secrets", "list"]:
            return [{"name": f"projects/123/secrets/{name}"} for name in self.keys]
        if args[:2] == ["secrets", "create"]:
            self.keys[args[2]] = data
            return {"name": args[2]}
        if args[:3] == ["secrets", "versions", "describe"]:
            return {"state": "ENABLED"}
        if args[:2] == ["secrets", "add-iam-policy-binding"]:
            return {}
        if args[:3] in (["run", "jobs", "create"], ["run", "jobs", "update"]):
            self.jobs = [{"metadata": {"name": JOB, "labels": {LABEL_KEY: LABEL_VALUE}}}]
            return {}
        if args[:3] == ["run", "jobs", "execute"]:
            return {
                "status": {
                    "conditions": [{"type": "Completed", "status": str(self.migration_ok)}],
                    "failedCount": 0 if self.migration_ok else 1,
                }
            }
        if args[:2] == ["run", "deploy"]:
            self.public = False
            self.services = [{"metadata": {"name": SERVICE, "labels": {LABEL_KEY: LABEL_VALUE}}}]
            return {"status": {"url": self.url}}
        if args[:3] == ["run", "services", "update"]:
            return {"status": {"url": self.url}}
        if args[:3] == ["run", "services", "add-iam-policy-binding"]:
            self.public = True
            return {}
        raise AssertionError(args)


class TestDeploymentSafetyTests(unittest.TestCase):
    def deploy(self, cloud, site_check=lambda url: None, report=lambda message: None):
        with patch("gcp_deploy_test.prepare_database") as prepare:
            url = deploy_test(cloud, report=report, site_check=site_check)
        return url, prepare

    def test_migrate_private_bootstrap_host_update_then_public_checks(self):
        cloud = DeploymentModel()
        checked = []

        def check(url):
            self.assertTrue(cloud.public)
            checked.append(url)

        url, prepare = self.deploy(cloud, site_check=check)
        prepare.assert_called_once()
        self.assertEqual(checked, [url])
        job = next(c for c in cloud.calls if c[:3] == ["run", "jobs", "create"])
        web = next(c for c in cloud.calls if c[:2] == ["run", "deploy"])
        update = next(c for c in cloud.calls if c[:3] == ["run", "services", "update"])
        publish = next(
            c for c in cloud.calls if c[:3] == ["run", "services", "add-iam-policy-binding"]
        )
        self.assertLess(cloud.calls.index(job), cloud.calls.index(web))
        self.assertLess(cloud.calls.index(web), cloud.calls.index(update))
        self.assertLess(cloud.calls.index(update), cloud.calls.index(publish))
        for flag in [
            "--no-allow-unauthenticated",
            "--no-cpu-throttling",
            "--min-instances=1",
            "--max-instances=1",
            "--execution-environment=gen2",
        ]:
            self.assertIn(flag, web)
        self.assertTrue(any(DIGEST in c for c in web))
        self.assertTrue(any("KAKAO_LOGIN_ENABLED=False" in c for c in web))
        self.assertTrue(any("check_database" in c and "migrate --noinput &&" in c for c in job))
        self.assertIn("--max-retries=0", job)
        self.assertTrue(any("DJANGO_SECRET_KEY=yeongcha-test-django-secret:1" in c for c in web))
        self.assertIn(
            "--update-env-vars=DJANGO_ALLOWED_HOSTS=yeongcha-test-example.du.a.run.app", update
        )
        self.assertNotIn("*", " ".join(web + update))
        self.assertNotIn(next(iter(cloud.keys.values())), " ".join(web + job))

    def test_failed_migration_never_starts_or_publishes_web(self):
        cloud = DeploymentModel()
        cloud.migration_ok = False
        with self.assertRaises(SetupError):
            self.deploy(cloud)
        self.assertFalse(any(c[:2] == ["run", "deploy"] for c in cloud.calls))
        self.assertFalse(cloud.public)

    def test_unowned_service_or_job_is_rejected_before_secret_changes(self):
        for resource in ["services", "jobs"]:
            with self.subTest(resource=resource):
                cloud = DeploymentModel()
                name = SERVICE if resource == "services" else JOB
                setattr(cloud, resource, [{"metadata": {"name": name, "labels": {}}}])
                with self.assertRaises(SetupError):
                    self.deploy(cloud)
                self.assertFalse(cloud.keys)
                self.assertFalse(cloud.public)

    def test_repeat_preserves_signing_key_and_updates_job(self):
        cloud = DeploymentModel()
        self.deploy(cloud)
        original = cloud.keys[SIGNING_SECRET]
        self.deploy(cloud)
        self.assertEqual(original, cloud.keys[SIGNING_SECRET])
        self.assertTrue(any(c[:3] == ["run", "jobs", "update"] for c in cloud.calls))

    def test_untrusted_service_url_never_receives_public_policy(self):
        cloud = DeploymentModel()
        cloud.url = "https://unexpected.example"
        with self.assertRaises(SetupError):
            self.deploy(cloud)
        self.assertFalse(cloud.public)

    def test_failed_health_check_is_not_reported_as_success(self):
        messages = []

        def fail(url):
            raise SetupError("DB health failed")

        with self.assertRaises(SetupError):
            self.deploy(DeploymentModel(), site_check=fail, report=messages.append)
        self.assertFalse(any(message.startswith("Test URL:") for message in messages))

    def test_console_output_never_contains_generated_signing_key(self):
        cloud = DeploymentModel()
        output = io.StringIO()
        with redirect_stdout(output), patch("gcp_deploy_test.prepare_database"):
            deploy_test(cloud, site_check=lambda url: None)
        self.assertNotIn(cloud.keys[SIGNING_SECRET], output.getvalue())
        self.assertIn("Test URL:", output.getvalue())

    def test_job_requires_explicit_success(self):
        for result in [
            {},
            {"status": {}},
            {"status": {"conditions": [{"type": "Completed", "status": "False"}]}},
        ]:
            with self.subTest(result=result), self.assertRaises(SetupError):
                verify_execution(result)
        verify_execution({"conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED"}]})


if __name__ == "__main__":
    unittest.main()
