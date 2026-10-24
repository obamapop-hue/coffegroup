from django.db import connection
from django.test import TestCase

from scripts.recovery_probe import CONNECTION, check_recovered_data, target_database

from .test_selling import SellingFixture


class RecoveryProbeTests(SellingFixture, TestCase):
    def test_real_database_schema_rows_guards_and_temporary_write_read(self):
        self.sent()
        self.accept()
        counts = check_recovered_data(connection, connection.settings_dict["NAME"])
        self.assertEqual(counts["orders_finalorder"], 1)
        self.assertEqual(counts["orders_submission"], 1)
        self.assertGreater(counts["accounts_user"], 0)

    def test_database_selection_replaces_source_socket_without_changing_credentials_or_input(self):
        source = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "yeongcha",
            "HOST": "",
            "PASSWORD": "private-test-password",
            "OPTIONS": {"host": "/cloudsql/original", "connect_timeout": 5},
        }
        target = target_database(source, CONNECTION)
        self.assertEqual(target["HOST"], "/cloudsql/" + CONNECTION)
        self.assertNotIn("host", target["OPTIONS"])
        self.assertEqual(target["PASSWORD"], source["PASSWORD"])
        self.assertEqual(source["OPTIONS"]["host"], "/cloudsql/original")
        with self.assertRaises(ValueError):
            target_database(source, "active-venture-449808-a5:asia-northeast3:yeongcha-db")
