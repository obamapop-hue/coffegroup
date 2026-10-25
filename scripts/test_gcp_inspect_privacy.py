import json
import unittest

from gcp_inspect_privacy import PROJECT, PROJECT_NUMBER, inspect_privacy
from gcp_prepare_database import GoogleCloud, SetupError


class PrivacyCloudModel(GoogleCloud):
    def __init__(self):
        super().__init__(PROJECT)
        self.calls = []
        self.bucket_project = PROJECT
        self.retention = 30
        self.routes = [
            {
                "destination": (
                    f"logging.googleapis.com/projects/{PROJECT}/locations/global/buckets/_Default"
                ),
                "description": "PRIVATE_DESCRIPTION",
                "filter": "PRIVATE_FILTER",
            },
            {
                "destination": (
                    f"logging.googleapis.com/projects/{PROJECT_NUMBER}"
                    "/locations/asia-northeast1/buckets/custom"
                )
            },
        ]

    def request(self, args, **kwargs):
        self.calls.append(args)
        if args[:3] == ["run", "services", "describe"]:
            return {
                "metadata": {"name": "yeongcha-test", "labels": {"yeongcha-stage": "test"}},
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "env": [
                                        {"name": "KAKAO_WITHDRAWAL_ENABLED", "value": "True"},
                                        {
                                            "name": "KAKAO_ADMIN_KEY",
                                            "valueFrom": {
                                                "secretKeyRef": {"name": "admin-key", "key": "1"}
                                            },
                                        },
                                        {"name": "DATABASE_URL", "value": "PRIVATE_DATABASE_VALUE"},
                                        {"name": "OTHER_VALUE", "value": "PRIVATE_SECRET"},
                                    ]
                                }
                            ]
                        }
                    }
                },
            }
        if args[:3] == ["sql", "instances", "describe"]:
            return {
                "name": "yeongcha-db",
                "region": "asia-northeast3",
                "settings": {
                    "backupConfiguration": {
                        "enabled": True,
                        "backupRetentionSettings": {"retainedBackups": 7},
                    }
                },
            }
        if args[:3] == ["sql", "backups", "list"]:
            return [
                {
                    "id": "123",
                    "type": "ON_DEMAND",
                    "status": "SUCCESSFUL",
                    "location": "asia",
                    "description": "PRIVATE_BACKUP_DESCRIPTION",
                }
            ]
        if args == ["logging", "sinks", "list"]:
            return self.routes
        if args[:3] == ["logging", "buckets", "list"]:
            location = args[3].split("=", 1)[1]
            return [
                {
                    "name": f"projects/{self.bucket_project}/locations/{location}/buckets/_Default",
                    "retentionDays": self.retention,
                    "lifecycleState": "ACTIVE",
                    "description": "PRIVATE_DESCRIPTION",
                    "cmekSettings": {"kmsKeyName": "PRIVATE_KMS_KEY"},
                }
            ]
        raise AssertionError(args)


class PrivacyInspectionTests(unittest.TestCase):
    def test_only_selected_metadata_is_returned_without_private_payloads(self):
        cloud = PrivacyCloudModel()
        result = inspect_privacy(cloud)
        self.assertNotIn("PRIVATE_", json.dumps(result))
        self.assertTrue(result["web"]["admin_key_secret_reference_attached"])
        self.assertTrue(result["web"]["kakao_withdrawal_flag"])
        self.assertEqual(result["database"]["backup_metadata"][0]["location"], "asia")
        self.assertEqual(result["logging"]["buckets"][0]["retention_days"], 30)
        self.assertFalse(result["settings_changed"])
        self.assertFalse(result["legal_retention_basis_verified"])

    def test_read_only_and_no_secret_access_billing_or_log_content_reads(self):
        cloud = PrivacyCloudModel()
        inspect_privacy(cloud)
        self.assertTrue(
            all(
                call[1:3]
                in (
                    ["services", "describe"],
                    ["instances", "describe"],
                    ["backups", "list"],
                    ["sinks", "list"],
                    ["buckets", "list"],
                )
                for call in cloud.calls
            )
        )
        self.assertFalse(any(call[0] in {"secrets", "billing", "storage"} for call in cloud.calls))
        self.assertFalse(
            any("read" in call or "update" in call or "delete" in call for call in cloud.calls)
        )

    def test_same_project_regional_sink_destination_is_included_and_alias_accepted(self):
        cloud = PrivacyCloudModel()
        cloud.bucket_project = PROJECT_NUMBER
        result = inspect_privacy(cloud)
        self.assertEqual(
            set(result["logging"]["locations_queried"]),
            {"global", "asia-northeast3", "asia-northeast1"},
        )
        self.assertEqual(
            result["logging"]["project_routes"][1]["destination_type"], "project_log_bucket"
        )

    def test_foreign_or_unrecognized_destination_requires_review_without_expanding_scope(self):
        cloud = PrivacyCloudModel()
        cloud.routes = [
            {
                "destination": (
                    "logging.googleapis.com/projects/other-project"
                    "/locations/us-central1/buckets/private"
                )
            },
            {"destination": "storage.googleapis.com/PRIVATE_BUCKET"},
        ]
        result = inspect_privacy(cloud)
        self.assertNotIn("us-central1", result["logging"]["locations_queried"])
        self.assertTrue(
            all(
                route["destination_type"] == "other_destination_requires_review"
                for route in result["logging"]["project_routes"]
            )
        )
        self.assertNotIn("PRIVATE_BUCKET", json.dumps(result))

    def test_absent_retention_is_unknown_rather_than_assumed_thirty_days(self):
        cloud = PrivacyCloudModel()
        cloud.retention = None
        result = inspect_privacy(cloud)
        self.assertIsNone(result["logging"]["buckets"][0]["retention_days"])
        self.assertIsNone(result["database"]["configured_backup_location"])

    def test_wrong_project_or_foreign_bucket_is_rejected(self):
        cloud = PrivacyCloudModel()
        cloud.project = "other-project"
        with self.assertRaises(SetupError):
            inspect_privacy(cloud)
        self.assertFalse(cloud.calls)
        cloud.project = PROJECT
        cloud.bucket_project = "other-project"
        with self.assertRaises(SetupError):
            inspect_privacy(cloud)


if __name__ == "__main__":
    unittest.main()
