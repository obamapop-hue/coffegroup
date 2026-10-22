"""Prepare Cloud SQL credentials inside authenticated Google Cloud Shell.

Uses only the Python standard library and gcloud. Never prints credentials or
writes them to local files. A failed/repeated run never resets a user's password.
"""

import argparse
import json
import re
import secrets
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, quote, unquote, urlsplit

INSTANCE = "yeongcha-db"
DATABASE = "yeongcha"
DATABASE_USER = "yeongcha_app"
DATABASE_SECRET = "yeongcha-database-url"
REGION = "asia-northeast3"


class SetupError(Exception):
    pass


class GoogleCloud:
    def __init__(self, project):
        self.project = project
        self.sensitive = []

    def remember(self, value):
        if value:
            self.sensitive.append(value)

    def redact(self, value):
        for secret in sorted(self.sensitive, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        return value

    def request(self, args, *, data=None, raw=False):
        command = ["gcloud", *args, f"--project={self.project}", "--quiet"]
        if not raw:
            command.append("--format=json")
        result = subprocess.run(command, input=data, text=True, capture_output=True, check=False)
        if result.returncode:
            message = self.redact(result.stderr.strip())
            raise SetupError(f"Google Cloud command failed: {message}")
        if raw:
            self.remember(result.stdout.strip())
            return result.stdout.strip()
        return json.loads(result.stdout) if result.stdout.strip() else None


def prepare_database(cloud, report=print):
    connection_name = f"{cloud.project}:{REGION}:{INSTANCE}"
    runtime = f"yeongcha-runtime@{cloud.project}.iam.gserviceaccount.com"
    instance = cloud.request(["sql", "instances", "describe", INSTANCE])
    if (
        instance.get("state") != "RUNNABLE"
        or instance.get("connectionName") != connection_name
        or instance.get("databaseVersion") != "POSTGRES_18"
    ):
        raise SetupError("Expected PostgreSQL 18 instance is not ready. No changes made.")
    databases = cloud.request(["sql", "databases", "list", f"--instance={INSTANCE}"])
    if not any(row.get("name") == DATABASE for row in databases):
        raise SetupError("The yeongcha database must be created first. No changes made.")
    cloud.request(["iam", "service-accounts", "describe", runtime])
    users = cloud.request(["sql", "users", "list", f"--instance={INSTANCE}"])
    user_exists = any(row.get("name") == DATABASE_USER for row in users)
    secret_rows = cloud.request(["secrets", "list"])
    secret_exists = any(
        row.get("name", "").rsplit("/", 1)[-1] == DATABASE_SECRET for row in secret_rows
    )
    if user_exists and not secret_exists:
        raise SetupError("Database user exists without its saved secret. Password was not changed.")

    if secret_exists:
        url = cloud.request(
            ["secrets", "versions", "access", "1", f"--secret={DATABASE_SECRET}"], raw=True
        )
        parsed = urlsplit(url)
        password = unquote(parsed.password or "")
        cloud.remember(password)
        if (
            parsed.scheme != "postgresql"
            or parsed.username != DATABASE_USER
            or parsed.hostname is not None
            or parsed.path != f"/{DATABASE}"
            or parse_qs(parsed.query) != {"host": [f"/cloudsql/{connection_name}"]}
            or len(password) < 32
        ):
            raise SetupError("Existing secret does not match this database. No password changed.")
        report("Existing credential reused; secret version 1 was not changed.")
    else:
        password = secrets.token_hex(32)
        url = (
            f"postgresql://{DATABASE_USER}:{quote(password, safe='')}@/{DATABASE}"
            f"?host=/cloudsql/{connection_name}"
        )
        cloud.remember(password)
        cloud.remember(url)
        # Persist first: a disconnect during user creation can then be retried
        # with the same password. Never rotate a password just to make a retry pass.
        cloud.request(
            [
                "secrets",
                "create",
                DATABASE_SECRET,
                "--replication-policy=user-managed",
                f"--locations={REGION}",
                "--data-file=-",
            ],
            data=url,
        )
        report("Database credential stored in Secret Manager (version 1).")

    if not user_exists:
        cloud.request(
            [
                "sql",
                "users",
                "create",
                DATABASE_USER,
                f"--instance={INSTANCE}",
                f"--password={password}",
            ]
        )
        report("Database user yeongcha_app created.")
    else:
        report("Existing database user retained; password was not reset.")

    cloud.request(
        [
            "secrets",
            "add-iam-policy-binding",
            DATABASE_SECRET,
            f"--member=serviceAccount:{runtime}",
            "--role=roles/secretmanager.secretAccessor",
            "--condition=None",
        ]
    )
    report("Runtime account granted access to this secret only.")
    report("Database credentials prepared. Connection will be verified during deployment.")
    return {"database_secret": DATABASE_SECRET, "secret_version": "1"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="active-venture-449808-a5")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", args.project):
        parser.error("Use the project ID, not its display name or number.")
    if not shutil.which("gcloud"):
        parser.error("Run this script in your authenticated Google Cloud Shell.")
    cloud = GoogleCloud(args.project)
    try:
        prepare_database(cloud)
    except (SetupError, ValueError) as error:
        print(cloud.redact(str(error)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted. Saved credentials will be reused on the next run.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
