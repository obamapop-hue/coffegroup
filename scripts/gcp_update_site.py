"""Build and update the existing Google site without replacing its settings or secrets."""

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from gcp_deploy_test import JOB, SERVICE, check_site, verify_execution, verify_owned_resource
from gcp_prepare_database import REGION, GoogleCloud, SetupError

PROJECT = "active-venture-449808-a5"


def update_site(cloud, source, revision, *, report=print, site_check=check_site, pause=time.sleep):
    if not re.fullmatch(r"[0-9a-f]{12,40}", revision):
        raise SetupError("Expected a committed source revision.")
    region = f"--region={REGION}"
    services = cloud.request(["run", "services", "list", region])
    jobs = cloud.request(["run", "jobs", "list", region])
    if not verify_owned_resource(services, SERVICE) or not verify_owned_resource(jobs, JOB):
        raise SetupError("The existing test service and migration job are required.")
    service = cloud.request(["run", "services", "describe", SERVICE, region])
    help_text = cloud.request(["run", "services", "update", "--help"], raw=True)
    if not all(flag in help_text for flag in ("--startup-probe", "--liveness-probe")):
        raise SetupError("The Cloud Shell gcloud command lacks the required health-check flags.")
    base_url = service.get("status", {}).get("url", "").rstrip("/")
    if not re.fullmatch(r"https://[a-z0-9.-]+\.run\.app", base_url):
        raise SetupError("The existing Google site address could not be verified.")
    tag = f"{REGION}-docker.pkg.dev/{cloud.project}/yeongcha/app:site-{revision}"
    report("1/4 수정한 사이트를 빌드합니다. 기존 로그인 설정과 DB는 유지합니다.")
    build = cloud.request(
        [
            "builds",
            "submit",
            str(source),
            f"--tag={tag}",
            region,
            "--default-buckets-behavior=regional-user-owned-bucket",
            "--async",
        ]
    )
    build_id = build.get("id", "")
    if not build_id:
        raise SetupError("Cloud Build did not return a build ID. Web update stopped.")
    report(f"빌드 ID: {build_id}")
    for attempt in range(180):
        build = cloud.request(["builds", "describe", build_id, region])
        status = build.get("status")
        if status == "SUCCESS":
            break
        if status not in ("QUEUED", "WORKING", "PENDING"):
            raise SetupError(f"Build did not succeed ({status}). Web update stopped.")
        if attempt % 6 == 0:
            report("빌드 진행 중입니다. 완료될 때까지 이 창을 유지해 주세요.")
        pause(5)
    else:
        raise SetupError("Build still running. Check the printed build ID before retrying.")
    images = build.get("results", {}).get("images", [])
    digest = next((row.get("digest", "") for row in images if row.get("name") == tag), "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise SetupError("Successful image digest was not verified. Web update stopped.")
    image = tag.split(":site-", 1)[0] + "@" + digest
    report("2/4 DB 구조를 갱신하고 실제 읽기·쓰기를 검사합니다.")
    # Preserve runtime identity, DB secrets, command and socket attachment.
    cloud.request(["run", "jobs", "update", JOB, region, f"--image={image}"])
    execution = cloud.request(["run", "jobs", "execute", JOB, region, "--wait"])
    verify_execution(execution)
    report("3/4 웹을 새 버전으로 갱신합니다. 카카오 키·계정·카페·메뉴는 유지합니다.")
    cloud.request(
        [
            "run",
            "services",
            "update",
            SERVICE,
            region,
            f"--image={image}",
            "--update-env-vars=REQUIRE_DEADLINE_WORKER=True",
            "--startup-probe=httpGet.path=/health/ready/,httpGet.port=8000,"
            "timeoutSeconds=5,periodSeconds=5,failureThreshold=24",
            "--liveness-probe=httpGet.path=/health/ready/,httpGet.port=8000,"
            "timeoutSeconds=5,periodSeconds=30,failureThreshold=3",
        ]
    )
    report("4/4 사이트 HTTPS와 DB 연결을 검사합니다.")
    site_check(base_url)
    report(f"배포와 기본 검사가 완료됐습니다. 사이트: {base_url}")
    report("사이트에서 반영된 화면과 기능을 확인해 주세요.")
    return base_url


def main():
    if not shutil.which("gcloud"):
        print("인증된 Google Cloud Shell에서 실행해 주세요.", file=sys.stderr)
        return 1
    source = Path(__file__).resolve().parents[1]
    revision = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    changes = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if changes:
        print(
            "소스에 저장소와 다른 변경이 있어 중단했습니다. 기존 파일은 수정하지 않았습니다.",
            file=sys.stderr,
        )
        return 1
    cloud = GoogleCloud(PROJECT)
    try:
        update_site(cloud, source, revision, report=lambda message: print(message, flush=True))
    except (SetupError, ValueError) as error:
        print(cloud.redact(str(error)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "중단되었습니다. Google 작업은 계속 실행될 수 있으니 상태를 확인해 주세요.",
            file=sys.stderr,
        )
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
