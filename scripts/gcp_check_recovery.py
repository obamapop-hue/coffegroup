"""Check the already-restored temporary DB, then remove its temporary resources."""

import argparse
import re
import shutil
import sys
from pathlib import Path

from gcp_deploy_test import SERVICE, SIGNING_SECRET, check_site, resource_name, verify_execution
from gcp_prepare_database import DATABASE_SECRET, INSTANCE, REGION, GoogleCloud, SetupError
from recovery_probe import CONNECTION, PROJECT, TARGET

JOB = "yeongcha-recovery-check-20261024"
RESTORE_OPERATION = "8cf98307-cb42-4183-a620-e55100000042"
PUBLIC_SITE = "https://yeongcha-test-t7e4i53n5q-du.a.run.app"
LABEL = "yeongcha-purpose"
LABEL_VALUE = "restore-check"


def verify_target(cloud, *, require_ready=True):
    operation = cloud.request(["sql", "operations", "describe", RESTORE_OPERATION])
    if (
        operation.get("status") != "DONE"
        or operation.get("error")
        or operation.get("operationType") != "RESTORE_VOLUME"
        or operation.get("targetId") != TARGET
    ):
        raise SetupError(
            "The known restore operation has not successfully completed on the temporary target."
        )
    target = cloud.request(["sql", "instances", "describe", TARGET])
    if (
        TARGET == INSTANCE
        or target.get("connectionName") != CONNECTION
        or (require_ready and target.get("state") != "RUNNABLE")
        or target.get("databaseVersion") != "POSTGRES_18"
    ):
        raise SetupError("Temporary restore database identity/readiness did not match.")
    return target


def existing_job(cloud):
    for job in cloud.request(["run", "jobs", "list", f"--region={REGION}"]):
        if resource_name(job) == JOB:
            if job.get("metadata", {}).get("labels", {}).get(LABEL) != LABEL_VALUE:
                raise SetupError("A differently-owned Job has this name; it was not changed.")
            return True
    return False


def clean_temporary_resources(cloud, report=print):
    # Only this exact operation/connection/name can be cleaned. Never relax source protection.
    job_exists = existing_job(cloud)
    instances = cloud.request(["sql", "instances", "list"])
    if not any(row.get("name") == TARGET for row in instances):
        if job_exists:
            cloud.request(["run", "jobs", "delete", JOB, f"--region={REGION}"])
        report("임시 공간은 이미 정리되어 있습니다.")
        return
    target = verify_target(cloud, require_ready=False)
    if job_exists:
        cloud.request(["run", "jobs", "delete", JOB, f"--region={REGION}"])
    if target.get("settings", {}).get("deletionProtectionEnabled"):
        cloud.request(["sql", "instances", "patch", TARGET, "--no-deletion-protection"])
    report("임시 저장소를 정리하고 있습니다. 잠시 기다려 주세요.")
    cloud.request(["sql", "instances", "delete", TARGET])
    rows = cloud.request(["sql", "instances", "list"])
    if any(row.get("name") == TARGET for row in rows):
        raise SetupError("Temporary database removal has not yet been verified.")
    report("임시 저장소와 임시 검사 도구 정리가 완료됐습니다.")


def check_recovery(cloud, *, report=print, site_check=check_site):
    report("1/4 복구된 임시 저장소와 작업 번호를 확인합니다.")
    verify_target(cloud)
    service = cloud.request(["run", "services", "describe", SERVICE, f"--region={REGION}"])
    spec = service.get("spec", {}).get("template", {}).get("spec", {})
    runtime = f"yeongcha-runtime@{PROJECT}.iam.gserviceaccount.com"
    containers = spec.get("containers", [])
    image = containers[0].get("image", "") if len(containers) == 1 else ""
    if spec.get("serviceAccountName") != runtime or not re.fullmatch(
        rf"{REGION}-docker\.pkg\.dev/{PROJECT}/yeongcha/app@sha256:[0-9a-f]{{64}}", image
    ):
        raise SetupError("The deployed application image/runtime could not be verified.")
    probe = Path(__file__).with_name("recovery_probe.py").read_text()
    if "~" in probe:
        raise SetupError("Unexpected command argument separator in probe code.")
    exists = existing_job(cloud)
    report("2/4 원래 사이트와 분리된 검사 도구를 준비합니다.")
    cloud.request(
        [
            "run",
            "jobs",
            "update" if exists else "create",
            JOB,
            f"--region={REGION}",
            f"--image={image}",
            f"--service-account={runtime}",
            f"--set-cloudsql-instances={CONNECTION}",
            f"--set-secrets=DATABASE_URL={DATABASE_SECRET}:1,DJANGO_SECRET_KEY={SIGNING_SECRET}:1",
            "--set-env-vars=DJANGO_DEBUG=False,DJANGO_ALLOWED_HOSTS=localhost,KAKAO_LOGIN_ENABLED=False,"
            f"YEONGCHA_RECOVERY_TARGET={CONNECTION}",
            f"{'--update-labels' if exists else '--labels'}={LABEL}={LABEL_VALUE}",
            "--cpu=1",
            "--memory=512Mi",
            "--tasks=1",
            "--parallelism=1",
            "--max-retries=0",
            "--task-timeout=300s",
            "--command=/app/.venv/bin/python",
            f"--args=^~^-c~{probe}",
        ]
    )
    report("3/4 복구 자료 조회·저장과 주문 수정 방지 설정을 검사합니다.")
    execution = cloud.request(["run", "jobs", "execute", JOB, f"--region={REGION}", "--wait"])
    try:
        verify_execution(execution)
    except SetupError:
        raise SetupError(
            "복구된 자료 검사가 통과하지 못했습니다. 실행 결과를 확인해야 합니다."
        ) from None
    site_check(PUBLIC_SITE)
    report("복구 자료의 구조·조회·임시 값 저장과 읽기·보호 설정 검사를 통과했습니다.")
    report("4/4 확인에 사용한 임시 공간을 정리합니다.")
    clean_temporary_resources(cloud, report)
    site_check(PUBLIC_SITE)
    report("복구 확인과 임시 공간 정리가 완료됐습니다. 기존 사이트도 정상 응답합니다.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup-only", action="store_true")
    args = parser.parse_args()
    if not shutil.which("gcloud"):
        print("평소 사용하던 Google Cloud Shell에서 실행해 주세요.", file=sys.stderr)
        return 1
    cloud = GoogleCloud(PROJECT)

    def report(text):
        print(text, flush=True)

    try:
        if args.cleanup_only:
            clean_temporary_resources(cloud, report)
        else:
            check_recovery(cloud, report=report)
    except (SetupError, ValueError) as error:
        print(cloud.redact(str(error)), file=sys.stderr)
        print(
            "검사 또는 정리가 완료되지 않았습니다. 결과를 알려 주세요. "
            "임시 저장소가 남아 있으면 비용이 발생합니다.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("대기를 중단했습니다. Google 작업과 임시 공간 상태를 확인해 주세요.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
