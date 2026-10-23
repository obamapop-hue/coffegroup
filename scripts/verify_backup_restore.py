"""Verify a logical backup and restore using fake orders in disposable LOCAL databases.

Requires the existing development PostgreSQL Docker container. Refuses remote DBs.
Does not modify the development database or any Google Cloud resources.
"""

# App model imports require initialized Django settings.
# ruff: noqa: E402
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()
import psycopg
from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase
from django.test.runner import DiscoverRunner
from psycopg import sql

from orders.selling import complete_order
from orders.test_selling import SellingFixture

if settings.DATABASES["default"].get("HOST", "") not in ("", "localhost", "127.0.0.1", "::1"):
    raise RuntimeError("Only a local PostgreSQL development host is allowed.")
settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_yeongcha_backup_source"
TARGET = "test_yeongcha_backup_restored"
CONTAINER = "coffegroup-db-1"


def docker(command, database, *, data=None):
    if not database.startswith("test_yeongcha_backup_"):
        raise RuntimeError("Only disposable backup verification databases are allowed.")
    result = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "sh", "-c", command, "sh", database],
        input=data,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(
            "Local PostgreSQL backup/restore command failed. Production was not accessed."
        )
    return result.stdout


def fingerprint(cursor, tables):
    result = {}
    for table in tables:
        cursor.execute(sql.SQL("SELECT to_jsonb(t)::text FROM {} t").format(sql.Identifier(table)))
        rows = sorted(row[0] for row in cursor.fetchall())
        result[table] = {
            "count": len(rows),
            "sha256": hashlib.sha256(json.dumps(rows).encode()).hexdigest(),
        }
    return result


class BackupRestoreCheck(SellingFixture, TransactionTestCase):
    def test_restore_rows_and_database_protection(self):
        self.sent()
        self.accept()
        complete_order(user=self.seller, room_id=self.room.pk)
        final = self.another_order()
        from orders.selling import decide_order

        decide_order(user=self.seller, room_id=final.room_id, action="reject", reason="복원 검증")
        tables = connection.introspection.table_names()
        with connection.cursor() as cursor:
            expected = fingerprint(cursor, tables)
            cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", [TARGET])
            self.assertIsNone(
                cursor.fetchone(), "A verification target already exists; it was not changed."
            )
        backup = docker(
            'exec pg_dump -U "$POSTGRES_USER" --format=custom --no-owner --no-acl "$1"',
            connection.settings_dict["NAME"],
        )
        created = False
        try:
            docker('exec createdb -U "$POSTGRES_USER" "$1"', TARGET)
            created = True
            docker(
                (
                    'exec pg_restore -U "$POSTGRES_USER" --no-owner --no-acl '
                    '--exit-on-error --dbname "$1"'
                ),
                TARGET,
                data=backup,
            )
            db = connection.settings_dict
            with psycopg.connect(
                dbname=TARGET,
                user=db["USER"],
                password=db["PASSWORD"],
                host=db["HOST"],
                port=db["PORT"],
                autocommit=True,
            ) as restored:
                with restored.cursor() as cursor:
                    self.assertEqual(fingerprint(cursor, tables), expected)
                    with self.assertRaises(psycopg.errors.CheckViolation):
                        cursor.execute("UPDATE orders_finalorder SET total_amount = 1")
                    with self.assertRaises(psycopg.errors.CheckViolation):
                        cursor.execute("UPDATE orders_room SET menu_snapshot = '[]'::jsonb")
            evidence = {
                "scope": "disposable local PostgreSQL only",
                "tables_verified": len(tables),
                "all_row_counts_and_hashes_match": True,
                "restored_immutability_triggers_verified": True,
                "google_restore_verified": False,
            }
            path = ROOT / ".cache" / "phase8"
            path.mkdir(parents=True, exist_ok=True)
            (path / "local-restore-verification.json").write_text(
                json.dumps(evidence, indent=2) + "\n"
            )
            print(json.dumps(evidence), flush=True)
        finally:
            if created:
                docker('exec dropdb -U "$POSTGRES_USER" --force "$1"', TARGET)


if __name__ == "__main__":
    sys.exit(bool(DiscoverRunner(verbosity=1).run_tests(["__main__.BackupRestoreCheck"])))
