import unittest
from copy import deepcopy

from gcp_prepare_database import GoogleCloud, SetupError
from gcp_remove_recovery_backup import BACKUP_ID, DESCRIPTION, EXPECTED_END, PROJECT, remove_backup


class BackupCloudModel(GoogleCloud):
    def __init__(self):
        super().__init__(PROJECT)
        self.calls = []
        self.protected = True
        self.backups = [
            {
                "id": BACKUP_ID,
                "type": "ON_DEMAND",
                "status": "SUCCESSFUL",
                "description": DESCRIPTION,
                "endTime": EXPECTED_END,
                "instance": "yeongcha-db",
            },
            {
                "id": "1792864800000",
                "type": "AUTOMATED",
                "status": "SUCCESSFUL",
                "endTime": "2026-10-24T18:49:48.258Z",
            },
            {"id": "other-manual", "type": "ON_DEMAND", "status": "SUCCESSFUL"},
        ]

    def request(self, args, **kwargs):
        self.calls.append(args)
        if args == ["sql", "instances", "describe", "yeongcha-db"]:
            return {
                "name": "yeongcha-db",
                "state": "RUNNABLE",
                "connectionName": f"{PROJECT}:asia-northeast3:yeongcha-db",
                "settings": {"deletionProtectionEnabled": self.protected},
            }
        if args == ["sql", "backups", "list", "--instance=yeongcha-db"]:
            return deepcopy(self.backups)
        if args == ["sql", "backups", "delete", BACKUP_ID, "--instance=yeongcha-db"]:
            self.backups = [row for row in self.backups if str(row.get("id")) != BACKUP_ID]
            return {}
        raise AssertionError(args)


class RecoveryBackupRemovalTests(unittest.TestCase):
    def run_remove(self, cloud):
        remove_backup(cloud, site_check=lambda url: None, report=lambda message: None)

    def test_only_exact_approved_backup_is_deleted_and_other_backups_remain(self):
        cloud = BackupCloudModel()
        self.run_remove(cloud)
        self.assertEqual([row["id"] for row in cloud.backups], ["1792864800000", "other-manual"])
        mutations = [call for call in cloud.calls if "delete" in call]
        self.assertEqual(
            mutations, [["sql", "backups", "delete", BACKUP_ID, "--instance=yeongcha-db"]]
        )
        self.assertTrue(
            all("list" in call or "describe" in call or call in mutations for call in cloud.calls)
        )

    def test_repeat_does_not_delete_any_additional_backup(self):
        cloud = BackupCloudModel()
        self.run_remove(cloud)
        self.run_remove(cloud)
        self.assertEqual(sum("delete" in call for call in cloud.calls), 1)

    def test_wrong_metadata_or_missing_newer_automatic_backup_stops_before_delete(self):
        for key, value in [
            ("type", "AUTOMATED"),
            ("description", "other-purpose"),
            ("status", "FAILED"),
            ("endTime", "2026-10-25T00:00:00Z"),
            ("instance", "other-db"),
        ]:
            with self.subTest(key=key):
                cloud = BackupCloudModel()
                cloud.backups[0][key] = value
                with self.assertRaises(SetupError):
                    self.run_remove(cloud)
                self.assertFalse(any("delete" in call for call in cloud.calls))
        cloud = BackupCloudModel()
        cloud.backups[1]["status"] = "FAILED"
        with self.assertRaises(SetupError):
            self.run_remove(cloud)
        self.assertFalse(any("delete" in call for call in cloud.calls))

    def test_wrong_project_or_unprotected_db_never_mutates(self):
        cloud = BackupCloudModel()
        cloud.project = "other-project"
        with self.assertRaises(SetupError):
            self.run_remove(cloud)
        self.assertFalse(cloud.calls)
        cloud.project = PROJECT
        cloud.protected = False
        with self.assertRaises(SetupError):
            self.run_remove(cloud)
        self.assertFalse(any("delete" in call for call in cloud.calls))

    def test_failed_preflight_site_check_stops_before_delete(self):
        cloud = BackupCloudModel()

        def unavailable(url):
            raise SetupError("site unavailable")

        with self.assertRaises(SetupError):
            remove_backup(cloud, site_check=unavailable, report=lambda message: None)
        self.assertFalse(any("delete" in call for call in cloud.calls))


if __name__ == "__main__":
    unittest.main()
