import unittest

from gcp_check_recovery import (
    CONNECTION,
    JOB,
    LABEL,
    LABEL_VALUE,
    PROJECT,
    PUBLIC_SITE,
    REGION,
    RESTORE_OPERATION,
    TARGET,
    check_recovery,
    clean_temporary_resources,
)
from gcp_prepare_database import GoogleCloud, SetupError


class RecoveryCloud(GoogleCloud):
    def __init__(self):
        super().__init__(PROJECT)
        self.calls = []
        self.target_exists = True
        self.job_exists = False
        self.protection = True
        self.completed = True
        self.operation_target = TARGET
        self.operation_status = "DONE"
        self.job_label = LABEL_VALUE

    def request(self, args, **kwargs):
        self.calls.append(args)
        if args[:3] == ["sql", "operations", "describe"]:
            self.assert_operation = args[3] == RESTORE_OPERATION
            return {
                "status": self.operation_status,
                "operationType": "RESTORE_VOLUME",
                "targetId": self.operation_target,
            }
        if args[:3] == ["sql", "instances", "describe"]:
            assert args[3] == TARGET
            return {
                "name": TARGET,
                "state": "RUNNABLE",
                "databaseVersion": "POSTGRES_18",
                "connectionName": CONNECTION,
                "settings": {"deletionProtectionEnabled": self.protection},
            }
        if args[:3] == ["sql", "instances", "list"]:
            return [{"name": "yeongcha-db"}] + ([{"name": TARGET}] if self.target_exists else [])
        if args[:3] == ["run", "services", "describe"]:
            return {
                "spec": {
                    "template": {
                        "spec": {
                            "serviceAccountName": (
                                f"yeongcha-runtime@{PROJECT}.iam.gserviceaccount.com"
                            ),
                            "containers": [
                                {
                                    "image": (
                                        f"{REGION}-docker.pkg.dev/{PROJECT}/yeongcha/app@sha256:"
                                    )
                                    + "a" * 64
                                }
                            ],
                        }
                    }
                }
            }
        if args[:3] == ["run", "jobs", "list"]:
            return (
                [{"metadata": {"name": JOB, "labels": {LABEL: self.job_label}}}]
                if self.job_exists
                else []
            )
        if args[:3] in (["run", "jobs", "create"], ["run", "jobs", "update"]):
            self.job_exists = True
            return {}
        if args[:3] == ["run", "jobs", "execute"]:
            return {
                "status": {
                    "conditions": [{"type": "Completed", "status": str(self.completed)}],
                    "failedCount": int(not self.completed),
                }
            }
        if args[:3] == ["run", "jobs", "delete"]:
            assert args[3] == JOB
            self.job_exists = False
            return {}
        if args[:3] == ["sql", "instances", "patch"]:
            assert args[3:] == [TARGET, "--no-deletion-protection"]
            self.protection = False
            return {}
        if args[:3] == ["sql", "instances", "delete"]:
            assert args[3] == TARGET
            self.target_exists = False
            return {}
        raise AssertionError(args)


class RecoveryCheckTests(unittest.TestCase):
    def test_verify_binds_only_temporary_socket_then_deletes_only_temporary_resources(self):
        cloud = RecoveryCloud()
        checked = []
        check_recovery(cloud, report=lambda _: None, site_check=checked.append)
        job = next(c for c in cloud.calls if c[:3] == ["run", "jobs", "create"])
        self.assertIn(f"--set-cloudsql-instances={CONNECTION}", job)
        self.assertIn("--command=/app/.venv/bin/python", job)
        self.assertTrue(next(c for c in job if c.startswith("--args=")).startswith("--args=^~^-c~"))
        self.assertFalse(cloud.target_exists)
        self.assertFalse(cloud.job_exists)
        self.assertEqual(checked, [PUBLIC_SITE, PUBLIC_SITE])
        for call in cloud.calls:
            if call[:3] in (["sql", "instances", "patch"], ["sql", "instances", "delete"]):
                self.assertEqual(call[3], TARGET)
        self.assertFalse(any(call[:3] == ["run", "services", "update"] for call in cloud.calls))
        self.assertFalse(any("secrets" in call for call in cloud.calls))

    def test_wrong_restore_target_stops_before_any_changes(self):
        cloud = RecoveryCloud()
        cloud.operation_target = "yeongcha-db"
        with self.assertRaises(SetupError):
            check_recovery(cloud, report=lambda _: None, site_check=lambda _: None)
        self.assertEqual(len(cloud.calls), 1)

    def test_running_restore_stops_before_creating_job(self):
        cloud = RecoveryCloud()
        cloud.operation_status = "RUNNING"
        with self.assertRaises(SetupError):
            check_recovery(cloud, report=lambda _: None, site_check=lambda _: None)
        self.assertFalse(cloud.job_exists)

    def test_failed_probe_does_not_claim_success_and_can_be_cleaned_separately(self):
        cloud = RecoveryCloud()
        cloud.completed = False
        with self.assertRaises(SetupError):
            check_recovery(cloud, report=lambda _: None, site_check=lambda _: None)
        self.assertTrue(cloud.target_exists)
        clean_temporary_resources(cloud, report=lambda _: None)
        self.assertFalse(cloud.target_exists)

    def test_cleanup_is_repeatable_after_resources_are_already_removed(self):
        cloud = RecoveryCloud()
        clean_temporary_resources(cloud, report=lambda _: None)
        calls = len(cloud.calls)
        clean_temporary_resources(cloud, report=lambda _: None)
        self.assertFalse(any("delete" in call or "patch" in call for call in cloud.calls[calls:]))

    def test_unowned_job_is_never_replaced_or_deleted(self):
        cloud = RecoveryCloud()
        cloud.job_exists = True
        cloud.job_label = "unrelated"
        with self.assertRaises(SetupError):
            check_recovery(cloud, report=lambda _: None, site_check=lambda _: None)
        self.assertTrue(cloud.target_exists)
        self.assertTrue(cloud.job_exists)
