"""Runs in the one-off recovery Job. No migrations or production connections."""

import copy
import json
import os
import sys

PROJECT = "active-venture-449808-a5"
TARGET = "yeongcha-restore-check-20261024"
CONNECTION = f"{PROJECT}:asia-northeast3:{TARGET}"


def target_database(database, connection_name):
    if connection_name != CONNECTION:
        raise ValueError("Unexpected recovery connection.")
    if (
        database.get("ENGINE") != "django.db.backends.postgresql"
        or database.get("NAME") != "yeongcha"
    ):
        raise ValueError("Unexpected recovery database.")
    result = copy.deepcopy(database)
    result["HOST"] = f"/cloudsql/{CONNECTION}"
    result.setdefault("OPTIONS", {}).pop("host", None)
    result["PORT"] = ""
    result["CONN_MAX_AGE"] = 0
    return result


def check_recovered_data(connection, expected_database):
    from django.db import transaction
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    executor.loader.check_consistent_history(connection)
    if executor.migration_plan(executor.loader.graph.leaf_nodes()):
        raise ValueError("Restored schema does not match the deployed application.")
    triggers = {
        "immutable_room_snapshot",
        "immutable_final_order",
        "immutable_sent_submission",
        "immutable_sent_revision",
        "immutable_sent_room",
        "immutable_sent_event",
    }
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        if cursor.fetchone()[0] != expected_database:
            raise ValueError("Unexpected connected database.")
        cursor.execute(
            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgenabled IN ('O', 'A') "
            "AND tgname = ANY(%s)",
            [list(triggers)],
        )
        if {row[0] for row in cursor.fetchall()} != triggers:
            raise ValueError("Restored order protection is incomplete.")
        counts = {}
        # Fixed table names only. Do not emit names, account codes, passwords or order contents.
        for table in (
            "accounts_user",
            "cafes_cafe",
            "cafes_menu",
            "orders_room",
            "orders_submission",
            "orders_finalorder",
            "orders_roomevent",
        ):
            cursor.execute(f'SELECT count(*) FROM "{table}"')
            counts[table] = cursor.fetchone()[0]
        if not counts["accounts_user"] or not counts["cafes_cafe"] or not counts["cafes_menu"]:
            raise ValueError("Expected member, cafe and menu data were not found.")
    with transaction.atomic(using=connection.alias), connection.cursor() as cursor:
        cursor.execute("CREATE TEMP TABLE yeongcha_recovery_probe (value integer) ON COMMIT DROP")
        cursor.execute("INSERT INTO yeongcha_recovery_probe VALUES (123)")
        cursor.execute("SELECT value FROM yeongcha_recovery_probe")
        if cursor.fetchone() != (123,):
            raise ValueError("Restored database write/read check failed.")
    return counts


def main():
    try:
        if os.environ.get("YEONGCHA_RECOVERY_TARGET") != CONNECTION:
            raise ValueError("Recovery target was not explicitly selected.")
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        from django.conf import settings

        settings.DATABASES["default"] = target_database(settings.DATABASES["default"], CONNECTION)
        import django

        django.setup()
        from django.db import connection

        counts = check_recovered_data(connection, "yeongcha")
        print(
            json.dumps({"event": "yeongcha_recovery_verified", "target": TARGET, "counts": counts})
        )
        connection.close()
    except Exception:
        # Provider/DB errors can contain connection details. Do not print a traceback or payload.
        print(
            "Recovery database checks did not pass; no production changes were made.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
