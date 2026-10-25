"""Read retention and location metadata only, never log messages or database rows."""

import json
import re
import shutil
import sys

from gcp_deploy_test import SERVICE, verify_owned_resource
from gcp_prepare_database import INSTANCE, REGION, GoogleCloud, SetupError

PROJECT = "active-venture-449808-a5"
PROJECT_NUMBER = "290262245390"


def inspect_privacy(cloud):
    if cloud.project != PROJECT:
        raise SetupError("기존 영차 프로젝트에서만 조회합니다.")
    project_aliases = {PROJECT, PROJECT_NUMBER}
    service = cloud.request(["run", "services", "describe", SERVICE, f"--region={REGION}"])
    if not verify_owned_resource([service], SERVICE):
        raise SetupError("기존 영차 사이트를 확인하지 못했습니다. 조회를 중단합니다.")
    containers = service.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    if len(containers) != 1:
        raise SetupError("기존 단일 웹 프로그램 구성을 확인하지 못했습니다.")
    env = containers[0].get("env", [])
    raw_flag = next(
        (row.get("value") for row in env if row.get("name") == "KAKAO_WITHDRAWAL_ENABLED"), None
    )
    withdrawal_flag = {"true": True, "false": False}.get(str(raw_flag).lower())
    admin_secret_attached = any(
        row.get("name") == "KAKAO_ADMIN_KEY" and bool(row.get("valueFrom", {}).get("secretKeyRef"))
        for row in env
    )

    database = cloud.request(["sql", "instances", "describe", INSTANCE])
    if database.get("name") != INSTANCE:
        raise SetupError("기존 주문 저장소를 확인하지 못했습니다.")
    backup_config = database.get("settings", {}).get("backupConfiguration", {})
    backups = cloud.request(["sql", "backups", "list", f"--instance={INSTANCE}"])
    sinks = cloud.request(["logging", "sinks", "list"])
    locations = {"global", REGION}
    routes = []
    for sink in sinks:
        destination = sink.get("destination", "")
        match = re.fullmatch(
            r"logging.googleapis.com/projects/([a-z0-9-]+)/locations/([a-z0-9-]+)/buckets/([^/]+)",
            destination,
        )
        if match and match[1] in project_aliases:
            locations.add(match[2])
            route = {
                "destination_type": "project_log_bucket",
                "location": match[2],
                "bucket": match[3],
            }
        else:
            route = {"destination_type": "other_destination_requires_review"}
        route["disabled"] = sink.get("disabled", False)
        route["exclusion_count"] = len(sink.get("exclusions", []))
        routes.append(route)

    buckets = []
    for location in sorted(locations):
        rows = cloud.request(["logging", "buckets", "list", f"--location={location}"])
        for row in rows:
            # Do not print descriptions, labels, encryption keys, IAM, filters or payloads.
            name = row.get("name", "")
            match = re.fullmatch(
                r"projects/([a-z0-9-]+)/locations/([a-z0-9-]+)/buckets/([^/]+)", name
            )
            if match is None or match[1] not in project_aliases or match[2] != location:
                raise SetupError(
                    "조회한 접속 기록 저장소의 위치/소유 프로젝트를 확인하지 못했습니다."
                )
            buckets.append(
                {
                    "bucket": match[3],
                    "location": match[2],
                    "retention_days": row.get("retentionDays"),
                    "locked": row.get("locked", False),
                    "state": row.get("lifecycleState"),
                }
            )

    return {
        "project": cloud.project,
        "web": {
            "region": REGION,
            "kakao_withdrawal_flag": withdrawal_flag,
            "admin_key_secret_reference_attached": admin_secret_attached,
        },
        "database": {
            "region": database.get("region"),
            "automated_backup_enabled": backup_config.get("enabled"),
            "automated_backup_retention": backup_config.get("backupRetentionSettings"),
            "configured_backup_location": backup_config.get("location"),
            "backup_metadata": [
                {
                    key: row.get(key)
                    for key in ("id", "type", "status", "location", "startTime", "endTime")
                }
                for row in backups
            ],
        },
        "logging": {
            "locations_queried": sorted(locations),
            "buckets": buckets,
            "project_routes": routes,
            "scope": "project_sinks_and_global_seoul_and_same_project_sink_destinations",
            "all_organization_destinations_or_filters_reviewed": False,
            "log_contents_read": False,
        },
        "settings_changed": False,
        "secret_values_read": False,
        "database_rows_read": False,
        "costs_checked": False,
        "legal_retention_basis_verified": False,
    }


def main():
    if not shutil.which("gcloud"):
        print("평소 사용하던 Google Cloud Shell에서 실행해 주세요.", file=sys.stderr)
        return 1
    cloud = GoogleCloud(PROJECT)
    try:
        report = inspect_privacy(cloud)
    except (SetupError, ValueError) as error:
        print(cloud.redact(str(error)), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        "접속 기록·백업의 보관/위치 설정 조회가 끝났습니다. 저장 자료나 설정은 변경하지 않았습니다."
    )
    print("개별 로그 내용·회원/주문 내용·비밀키·사용 요금은 조회하지 않았습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
