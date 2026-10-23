import json
import unittest

from gcp_inspect_operations import PUBLIC_SITE, inspect_operations
from gcp_prepare_database import GoogleCloud, SetupError


class InspectionCloud(GoogleCloud):
    def __init__(self):
        super().__init__("active-venture-449808-a5")
        self.calls = []
        self.containers = [
            {
                "resources": {"limits": {"cpu": "1", "memory": "512Mi"}},
                "env": [
                    {"name": "KAKAO_LOGIN_ENABLED", "value": "True"},
                    {"name": "DATABASE_URL", "value": "PRIVATE_DATABASE_VALUE"},
                    {
                        "name": "KAKAO_RESET_API_KEY",
                        "valueFrom": {"secretKeyRef": {"name": "key-name", "key": "1"}},
                    },
                ],
            }
        ]

    def request(self, args, **kwargs):
        self.calls.append(args)
        if args[:3] == ["run", "services", "describe"]:
            return {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "autoscaling.knative.dev/minScale": "1",
                                "run.googleapis.com/cpu-throttling": "false",
                            }
                        },
                        "spec": {"containers": self.containers},
                    }
                },
                "status": {
                    "latestReadyRevisionName": "revision-name",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }
        if args[:3] == ["sql", "instances", "describe"]:
            return {
                "state": "RUNNABLE",
                "databaseVersion": "POSTGRES_18",
                "settings": {"tier": "db-f1-micro", "backupConfiguration": {"enabled": True}},
            }
        if args[:3] == ["sql", "backups", "list"]:
            return [
                {"id": "123", "status": "SUCCESSFUL", "description": "PRIVATE_BACKUP_DESCRIPTION"}
            ]
        if args[:3] == ["billing", "projects", "describe"]:
            return {"billingEnabled": True, "billingAccountName": "PRIVATE_BILLING_ACCOUNT"}
        if args[:2] == ["logging", "read"]:
            return [
                {
                    "timestamp": "2026-10-23T00:00:00Z",
                    "severity": "ERROR",
                    "textPayload": "PRIVATE_LOG_PAYLOAD",
                    "httpRequest": {"requestUrl": "PRIVATE_OAUTH_CODE"},
                }
            ]
        raise AssertionError(args)


class InspectionTests(unittest.TestCase):
    def test_only_safe_configuration_and_references_are_returned(self):
        cloud = InspectionCloud()
        checked = []
        report = inspect_operations(cloud, site_check=checked.append)
        self.assertEqual(checked, [PUBLIC_SITE])
        self.assertEqual(report["web"]["min_instances_per_revision"], "1")
        self.assertEqual(report["web"]["cpu_throttling"], "false")
        self.assertTrue(report["database"]["automatic_backup_enabled"])
        self.assertFalse(report["restoration_test_performed"])
        self.assertFalse(report["actual_cost_and_credit_balance_checked"])
        self.assertNotIn("PRIVATE_", json.dumps(report))
        self.assertEqual(
            report["web"]["secret_references_only"]["KAKAO_RESET_API_KEY"],
            {"secret": "key-name", "version": "1"},
        )

    def test_all_cloud_calls_are_read_only(self):
        cloud = InspectionCloud()
        inspect_operations(cloud, site_check=lambda _: None)
        self.assertTrue(
            all(any(word in call for word in ["describe", "list", "read"]) for call in cloud.calls)
        )
        self.assertFalse(any("secrets" in call for call in cloud.calls))

    def test_unexpected_container_layout_stops_instead_of_guessing(self):
        cloud = InspectionCloud()
        cloud.containers = []
        with self.assertRaises(SetupError):
            inspect_operations(cloud, site_check=lambda _: None)
        self.assertEqual(len(cloud.calls), 1)

    def test_failed_public_check_is_not_reported_as_success(self):
        def fail(_):
            raise SetupError("HTTPS unavailable")

        with self.assertRaises(SetupError):
            inspect_operations(InspectionCloud(), site_check=fail)
