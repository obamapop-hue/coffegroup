"""Remove only the recovery-test backup explicitly approved by the owner."""

import shutil
import sys
from datetime import datetime

from gcp_deploy_test import check_site
from gcp_prepare_database import INSTANCE, REGION, GoogleCloud, SetupError

PROJECT = "active-venture-449808-a5"
BACKUP_ID = "1792849041384"
DESCRIPTION = "yeongcha-recovery-check-20261023"
EXPECTED_END = "2026-10-24T13:38:42.417Z"
SITE = "https://yeongcha-test-t7e4i53n5q-du.a.run.app"


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result
    except (AttributeError, ValueError, TypeError):
        raise SetupError("백업의 완료 시각을 확인하지 못해 삭제하지 않습니다.") from None


def verify_instance(cloud):
    instance = cloud.request(["sql", "instances", "describe", INSTANCE])
    if (
        instance.get("name") != INSTANCE
        or instance.get("state") != "RUNNABLE"
        or instance.get("connectionName") != f"{PROJECT}:{REGION}:{INSTANCE}"
        or not instance.get("settings", {}).get("deletionProtectionEnabled")
    ):
        raise SetupError("기존 DB의 정상 상태와 삭제 방지를 확인하지 못해 중단합니다.")


def remove_backup(cloud, *, site_check=check_site, report=print):
    if cloud.project != PROJECT:
        raise SetupError("승인된 영차 프로젝트에서만 실행합니다.")
    verify_instance(cloud)
    list_command = ["sql", "backups", "list", f"--instance={INSTANCE}"]
    backups = cloud.request(list_command)
    target = next((row for row in backups if str(row.get("id")) == BACKUP_ID), None)
    if target is not None and target.get("status") != "DELETED":
        if (
            target.get("type") != "ON_DEMAND"
            or target.get("status") != "SUCCESSFUL"
            or target.get("description") != DESCRIPTION
            or target.get("endTime") != EXPECTED_END
            or target.get("instance") != INSTANCE
        ):
            raise SetupError("승인한 복구 시험용 사본과 일치하지 않아 삭제하지 않습니다.")
        newer = [
            row
            for row in backups
            if row.get("type") == "AUTOMATED"
            and row.get("status") == "SUCCESSFUL"
            and timestamp(row.get("endTime")) > timestamp(EXPECTED_END)
        ]
        if not newer:
            raise SetupError("이 사본보다 최근의 정상 자동 백업을 확인하지 못해 삭제하지 않습니다.")
        report("대상 수동 사본과 더 최근의 정상 자동 백업을 확인했습니다.")
        site_check(SITE)
        cloud.request(["sql", "backups", "delete", BACKUP_ID, f"--instance={INSTANCE}"])
        after = cloud.request(list_command)
        if any(str(row.get("id")) == BACKUP_ID and row.get("status") != "DELETED" for row in after):
            raise SetupError(
                "사본 제거를 아직 확인하지 못했습니다. 재실행하면 삭제 상태부터 다시 확인합니다."
            )
        newer_ids = {str(row["id"]) for row in newer}
        if not any(
            str(row.get("id")) in newer_ids and row.get("status") == "SUCCESSFUL" for row in after
        ):
            raise SetupError("수동 사본은 제거됐지만 최근 자동 백업의 상태를 다시 확인해야 합니다.")
        report(f"승인한 수동 백업 {BACKUP_ID} 하나의 삭제를 확인했습니다.")
    else:
        report("대상 수동 백업은 이미 제거된 상태입니다. 추가 삭제는 하지 않았습니다.")
    verify_instance(cloud)
    site_check(SITE)
    report("기존 DB의 정상 상태·삭제 방지와 사이트 응답을 확인했습니다.")
    report("원본 DB·자동 백업·회원·주문 자료와 보관 설정은 변경하지 않았습니다.")


def main():
    if not shutil.which("gcloud"):
        print("평소 사용하던 Google Cloud Shell에서 실행해 주세요.", file=sys.stderr)
        return 1
    cloud = GoogleCloud(PROJECT)
    try:
        remove_backup(cloud)
    except (SetupError, ValueError) as error:
        print(cloud.redact(str(error)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
