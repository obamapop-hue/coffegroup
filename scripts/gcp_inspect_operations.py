"""Read-only Google operating checks. Never fetches secret values or changes resources."""

import json
import shutil
import sys

from gcp_deploy_test import SERVICE, check_site
from gcp_prepare_database import INSTANCE, REGION, GoogleCloud, SetupError

PROJECT = "active-venture-449808-a5"
PUBLIC_SITE = "https://yeongcha-test-t7e4i53n5q-du.a.run.app"


def inspect_operations(cloud, *, site_check=check_site):
    service = cloud.request(["run", "services", "describe", SERVICE, f"--region={REGION}"])
    template = service.get("spec", {}).get("template", {})
    annotations = template.get("metadata", {}).get("annotations", {})
    service_annotations = service.get("metadata", {}).get("annotations", {})
    spec = template.get("spec", {})
    containers = spec.get("containers", [])
    if len(containers) != 1:
        raise SetupError("Expected the existing single-container web service.")
    container = containers[0]
    safe_env_names = {
        "DJANGO_DEBUG",
        "DJANGO_TRUST_PROXY",
        "KAKAO_LOGIN_ENABLED",
        "REQUIRE_DEADLINE_WORKER",
    }
    env_rows = container.get("env", [])
    safe_env = {row["name"]: row.get("value") for row in env_rows if row["name"] in safe_env_names}
    secret_names = {
        row["name"]: {
            "secret": row["valueFrom"]["secretKeyRef"].get("name"),
            "version": row["valueFrom"]["secretKeyRef"].get("key"),
        }
        for row in env_rows
        if "secretKeyRef" in row.get("valueFrom", {})
    }
    database = cloud.request(["sql", "instances", "describe", INSTANCE])
    db_settings = database.get("settings", {})
    backup = db_settings.get("backupConfiguration", {})
    ip = db_settings.get("ipConfiguration", {})
    backups = cloud.request(["sql", "backups", "list", f"--instance={INSTANCE}", "--limit=5"])
    billing = cloud.request(["billing", "projects", "describe", cloud.project])
    report = {
        "project": cloud.project,
        "web": {
            "region": REGION,
            "ready_conditions": [
                {"type": row.get("type"), "status": row.get("status")}
                for row in service.get("status", {}).get("conditions", [])
            ],
            "revision": service.get("status", {}).get("latestReadyRevisionName"),
            "min_instances_per_revision": annotations.get("autoscaling.knative.dev/minScale"),
            "min_instances_per_service": service_annotations.get("run.googleapis.com/minScale"),
            "max_instances": annotations.get("autoscaling.knative.dev/maxScale"),
            "cpu_throttling": annotations.get("run.googleapis.com/cpu-throttling", "true"),
            "resources": container.get("resources", {}).get("limits", {}),
            "startup_probe_configured": bool(container.get("startupProbe")),
            "liveness_probe_configured": bool(container.get("livenessProbe")),
            "runtime_account": spec.get("serviceAccountName"),
            "safe_environment_flags": safe_env,
            "secret_references_only": secret_names,
            "initial_owner_selector_present": any(
                row["name"] == "INITIAL_OPERATOR_ACCOUNT_CODE" for row in env_rows
            ),
        },
        "database": {
            "state": database.get("state"),
            "version": database.get("databaseVersion"),
            "region": database.get("region"),
            "tier": db_settings.get("tier"),
            "disk_gb": db_settings.get("dataDiskSizeGb"),
            "availability": db_settings.get("availabilityType"),
            "deletion_protection": db_settings.get("deletionProtectionEnabled", False),
            "automatic_backup_enabled": backup.get("enabled", False),
            "backup_start_utc": backup.get("startTime"),
            "retention": backup.get("backupRetentionSettings"),
            "point_in_time_recovery": backup.get("pointInTimeRecoveryEnabled", False),
            "public_network_allow_entries": len(ip.get("authorizedNetworks", [])),
            "latest_backups": [
                {key: row.get(key) for key in ("id", "status", "type", "startTime", "endTime")}
                for row in backups
            ],
        },
        "billing_enabled": billing.get("billingEnabled"),
        "actual_cost_and_credit_balance_checked": False,
        "restoration_test_performed": False,
    }
    # Log text may contain personal data or OAuth codes: return only count and severity.
    entries = cloud.request(
        [
            "logging",
            "read",
            (
                'resource.type="cloud_run_revision" AND '
                'resource.labels.service_name="yeongcha-test" AND severity>=ERROR'
            ),
            "--freshness=24h",
            "--limit=20",
        ]
    )
    report["recent_errors"] = {
        "returned_count_limit_20": len(entries),
        "entries": [
            {"timestamp": row.get("timestamp"), "severity": row.get("severity")} for row in entries
        ],
    }
    site_check(PUBLIC_SITE)
    report["public_https_and_db_ready"] = True
    return report


def main():
    if not shutil.which("gcloud"):
        print("평소 사용하던 Google Cloud Shell에서 실행해 주세요.", file=sys.stderr)
        return 1
    cloud = GoogleCloud(PROJECT)
    try:
        report = inspect_operations(cloud)
    except (SetupError, ValueError) as error:
        print(cloud.redact(str(error)), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("확인 완료. 설정·저장 자료·요금제는 변경하지 않았습니다.")
    print("사용 요금과 백업 복원 성공 여부는 아직 확인한 것이 아닙니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
