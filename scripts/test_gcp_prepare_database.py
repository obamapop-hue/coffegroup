"""Offline safety checks; these do not authenticate to Google or create resources."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from gcp_prepare_database import (
    DATABASE_SECRET,
    DATABASE_USER,
    GoogleCloud,
    SetupError,
    prepare_database,
)


class CloudModel(GoogleCloud):
    def __init__(self):
        super().__init__("active-venture-449808-a5")
        self.stored = {}
        self.users = {}
        self.grants = []
        self.fail_user = False
        self.fail_read = False
        self.ready = True
        self.writes = []

    def request(self, args, *, data=None, raw=False):
        if self.fail_read:
            raise SetupError("Permission denied")
        action = tuple(args[:3])
        if action == ("sql", "instances", "describe"):
            return {
                "state": "RUNNABLE" if self.ready else "PENDING_CREATE",
                "connectionName": f"{self.project}:asia-northeast3:yeongcha-db",
                "databaseVersion": "POSTGRES_18",
            }
        if action == ("sql", "databases", "list"):
            return [{"name": "yeongcha"}]
        if action == ("iam", "service-accounts", "describe"):
            return {"email": args[3]}
        if action == ("sql", "users", "list"):
            return [{"name": name} for name in self.users]
        if args == ["secrets", "list"]:
            return [{"name": f"projects/123/secrets/{name}"} for name in self.stored]
        if action == ("secrets", "versions", "access"):
            if not raw or args[3] != "1":
                raise AssertionError(args)
            return self.stored[DATABASE_SECRET]
        self.writes.append(action)
        if tuple(args[:2]) == ("secrets", "create"):
            if args[2] in self.stored:
                raise SetupError("Already exists")
            self.stored[args[2]] = data
        elif action == ("sql", "users", "create"):
            if self.fail_user:
                raise SetupError("Temporary user creation failure")
            self.users[args[3]] = next(
                a.removeprefix("--password=") for a in args if a.startswith("--password=")
            )
        elif tuple(args[:2]) == ("secrets", "add-iam-policy-binding"):
            self.grants.append(args)
        else:
            raise AssertionError(args)


class DatabaseCredentialTests(unittest.TestCase):
    def test_new_setup_and_repeat_preserve_credential_and_hide_output(self):
        cloud = CloudModel()
        output = io.StringIO()
        with redirect_stdout(output):
            prepare_database(cloud)
            first = dict(cloud.stored)
            prepare_database(cloud)
        self.assertEqual(first, cloud.stored)
        password = unquote(urlsplit(first[DATABASE_SECRET]).password)
        self.assertEqual(password, cloud.users[DATABASE_USER])
        self.assertNotIn(password, output.getvalue())
        self.assertNotIn(first[DATABASE_SECRET], output.getvalue())
        self.assertEqual(cloud.writes.count(("sql", "users", "create")), 1)
        self.assertEqual(cloud.writes.count(("secrets", "create", DATABASE_SECRET)), 1)
        self.assertTrue(all(g[2] == DATABASE_SECRET for g in cloud.grants))
        self.assertTrue(all("--role=roles/secretmanager.secretAccessor" in g for g in cloud.grants))

    def test_disconnect_after_secret_saved_reuses_same_password(self):
        cloud = CloudModel()
        cloud.fail_user = True
        with self.assertRaises(SetupError):
            prepare_database(cloud, report=lambda message: None)
        saved = dict(cloud.stored)
        self.assertFalse(cloud.users)
        cloud.fail_user = False
        prepare_database(cloud, report=lambda message: None)
        self.assertEqual(saved, cloud.stored)
        self.assertEqual(
            unquote(urlsplit(saved[DATABASE_SECRET]).password), cloud.users[DATABASE_USER]
        )

    def test_existing_user_without_secret_is_not_reset(self):
        cloud = CloudModel()
        cloud.users[DATABASE_USER] = "existing-password"
        with self.assertRaises(SetupError):
            prepare_database(cloud)
        self.assertEqual(cloud.users[DATABASE_USER], "existing-password")
        self.assertFalse(cloud.writes)

    def test_wrong_target_secret_stops_without_changing_user(self):
        cloud = CloudModel()
        cloud.stored[DATABASE_SECRET] = "postgresql://other:wrong@/production"
        with self.assertRaises(SetupError):
            prepare_database(cloud)
        self.assertFalse(cloud.writes)

    def test_permission_failure_and_pending_instance_do_not_write(self):
        for failure in ["fail_read", "not_ready"]:
            with self.subTest(failure=failure):
                cloud = CloudModel()
                cloud.fail_read = failure == "fail_read"
                cloud.ready = failure != "not_ready"
                with self.assertRaises(SetupError):
                    prepare_database(cloud)
                self.assertFalse(cloud.writes)

    def test_native_command_failure_redacts_credentials(self):
        cloud = GoogleCloud("active-venture-449808-a5")
        fake_password = "SYNTHETIC-TEST-PASSWORD"
        cloud.remember(fake_password)
        result = type("Result", (), {"returncode": 1, "stderr": f"failed {fake_password}"})()
        with patch("gcp_prepare_database.subprocess.run", return_value=result):
            with self.assertRaises(SetupError) as raised:
                cloud.request(["sql", "users", "create", "test"])
        self.assertNotIn(fake_password, str(raised.exception))
        self.assertIn("[REDACTED]", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
