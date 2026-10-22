"""Deploy the verified image to a separate Google Cloud test service.

Run in authenticated Cloud Shell. Does not import Railway data or enable Kakao.
The original Railway environment and its Django signing key remain untouched.
"""

import argparse
import json
import re
import secrets
import shutil
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from gcp_prepare_database import DATABASE_SECRET, REGION, GoogleCloud, SetupError, prepare_database

SERVICE = "yeongcha-test"
JOB = "yeongcha-test-migrate"
SIGNING_SECRET = "yeongcha-test-django-secret"
LABEL_KEY = "yeongcha-stage"
LABEL_VALUE = "test"
DIGEST = "sha256:07b198bd40efc71d2084e7ab5fe537c4f928d83cefdef4a9fcf3e309fb2d8d22"


def resource_name(row):
    return row.get("metadata", {}).get("name", row.get("name", "")).rsplit("/", 1)[-1]


def verify_owned_resource(rows, name):
    for row in rows:
        if resource_name(row) == name:
            labels = row.get("metadata", {}).get("labels", row.get("labels", {}))
            if labels.get(LABEL_KEY) != LABEL_VALUE:
                raise SetupError(
                    f"{name} exists without the test label. No deployment changes made."
                )
            return True
    return False


def verify_execution(execution):
    status = execution.get("status", execution)
    completed = any(
        row.get("type") == "Completed"
        and (row.get("status") in (True, "True") or row.get("state") == "CONDITION_SUCCEEDED")
        for row in status.get("conditions", [])
    )
    if not completed or status.get("failedCount", 0):
        raise SetupError("Migration job completion was not verified. Web deployment stopped.")


def check_site(base_url):
    for path, expected in [("/health/live/", "ok"), ("/health/ready/", "ready"), ("/", None)]:
        for attempt in range(8):
            try:
                with urlopen(base_url + path, timeout=20) as response:
                    body = response.read()
                    if response.status != 200:
                        raise SetupError(f"HTTPS check failed: {path}")
                    if expected and json.loads(body).get("status") != expected:
                        raise SetupError(f"Unexpected health response: {path}")
                    if expected is None and "영차" not in body.decode():
                        raise SetupError("The homepage did not contain the expected brand.")
                break
            except HTTPError as error:
                if error.code not in (403, 429, 503) or attempt == 7:
                    raise SetupError(f"HTTPS check failed: {path}, HTTP {error.code}") from None
            except URLError:
                if attempt == 7:
                    raise SetupError(f"HTTPS connection could not be verified: {path}") from None
            time.sleep(3)


def deploy_test(cloud, report=None, site_check=check_site):
    if report is None:

        def report(message):
            print(message, flush=True)

    region = f"--region={REGION}"
    services = cloud.request(["run", "services", "list", region])
    jobs = cloud.request(["run", "jobs", "list", region])
    verify_owned_resource(services, SERVICE)
    job_exists = verify_owned_resource(jobs, JOB)

    report("1/5 Checking saved database credentials (no password will be printed).")
    prepare_database(cloud, report=report)
    runtime = f"yeongcha-runtime@{cloud.project}.iam.gserviceaccount.com"
    report("2/5 Preparing a separate signing key for the Google test site.")
    secret_rows = cloud.request(["secrets", "list"])
    if not any(resource_name(row) == SIGNING_SECRET for row in secret_rows):
        key = secrets.token_urlsafe(64)
        cloud.remember(key)
        cloud.request(
            [
                "secrets",
                "create",
                SIGNING_SECRET,
                "--replication-policy=user-managed",
                f"--locations={REGION}",
                "--data-file=-",
            ],
            data=key,
        )
    version = cloud.request(["secrets", "versions", "describe", "1", f"--secret={SIGNING_SECRET}"])
    if version.get("state") != "ENABLED":
        raise SetupError("The saved test signing key is not enabled. It was not replaced.")
    cloud.request(
        [
            "secrets",
            "add-iam-policy-binding",
            SIGNING_SECRET,
            f"--member=serviceAccount:{runtime}",
            "--role=roles/secretmanager.secretAccessor",
            "--condition=None",
        ]
    )

    image = f"{REGION}-docker.pkg.dev/{cloud.project}/yeongcha/app@{DIGEST}"
    connection = f"{cloud.project}:{REGION}:yeongcha-db"
    secret_flags = (
        f"--set-secrets=DATABASE_URL={DATABASE_SECRET}:1,DJANGO_SECRET_KEY={SIGNING_SECRET}:1"
    )
    env_flags = (
        "--set-env-vars=DJANGO_DEBUG=False,DJANGO_ALLOWED_HOSTS=localhost,"
        "DJANGO_TRUST_PROXY=True,KAKAO_LOGIN_ENABLED=False"
    )
    shared = [
        region,
        f"--image={image}",
        f"--service-account={runtime}",
        f"--set-cloudsql-instances={connection}",
        secret_flags,
        env_flags,
        "--cpu=1",
        "--memory=512Mi",
    ]
    report("3/5 Applying DB migrations and checking real DB write/read access. Please wait.")
    label_flag = "--update-labels" if job_exists else "--labels"
    cloud.request(
        [
            "run",
            "jobs",
            "update" if job_exists else "create",
            JOB,
            *shared,
            f"{label_flag}={LABEL_KEY}={LABEL_VALUE}",
            "--tasks=1",
            "--parallelism=1",
            "--max-retries=0",
            "--task-timeout=600s",
            "--command=/bin/bash",
            "--args=-c,.venv/bin/python manage.py migrate --noinput && "
            ".venv/bin/python manage.py check_database",
        ]
    )
    execution = cloud.request(["run", "jobs", "execute", JOB, region, "--wait"])
    verify_execution(execution)
    report("DB migrations and DB write/read job completed successfully.")

    report("4/5 Starting the private test service with an always-running deadline worker.")
    service = cloud.request(
        [
            "run",
            "deploy",
            SERVICE,
            *shared,
            f"--labels={LABEL_KEY}={LABEL_VALUE}",
            "--execution-environment=gen2",
            "--port=8000",
            "--min-instances=1",
            "--max-instances=1",
            "--no-cpu-throttling",
            "--concurrency=40",
            "--timeout=60s",
            "--no-allow-unauthenticated",
        ]
    )
    base_url = service.get("status", {}).get("url", service.get("uri", "")).rstrip("/")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".run.app")
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise SetupError("Could not verify the actual Cloud Run URL. Service remains private.")
    cloud.request(
        [
            "run",
            "services",
            "update",
            SERVICE,
            region,
            f"--update-env-vars=DJANGO_ALLOWED_HOSTS={parsed.hostname}",
        ]
    )
    report("5/5 Opening the test address and checking public HTTPS and DB readiness.")
    cloud.request(
        [
            "run",
            "services",
            "add-iam-policy-binding",
            SERVICE,
            region,
            "--member=allUsers",
            "--role=roles/run.invoker",
        ]
    )
    site_check(base_url)
    report(f"Test URL: {base_url}")
    report("Test checks passed. Railway data and Kakao login will be connected in the next stage.")
    return base_url


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
        deploy_test(cloud)
    except (SetupError, ValueError) as error:
        print(cloud.redact(str(error)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "Interrupted. Cloud operations may continue; check their status before retrying.",
            file=sys.stderr,
        )
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
