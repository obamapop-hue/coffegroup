"""Store the Kakao Admin key privately and enable site membership withdrawal."""

import argparse
import re
import shutil
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import build_opener

from gcp_configure_kakao import NoRedirect, hidden_input
from gcp_deploy_test import SERVICE, check_site, resource_name, verify_owned_resource
from gcp_prepare_database import REGION, GoogleCloud, SetupError

SECRET = "yeongcha-kakao-admin-key"


def verify_withdrawal_route(base):
    try:
        with build_opener(NoRedirect).open(base + "/accounts/withdraw/", timeout=30):
            raise SetupError("탈퇴 화면의 로그인 보호를 확인하지 못했습니다.")
    except HTTPError as error:
        if error.code != 302 or urljoin(base, error.headers.get("Location", "")) != (
            base + "/accounts/entry/organizer/"
        ):
            raise SetupError("먼저 최신 사이트 갱신 명령을 실행해 주세요.") from None
    except URLError:
        raise SetupError("사이트의 HTTPS 연결을 확인하지 못했습니다.") from None


def configure_withdrawal(
    cloud,
    *,
    reader=hidden_input,
    replace=False,
    route_check=verify_withdrawal_route,
    site_check=check_site,
    report=print,
):
    service = cloud.request(["run", "services", "describe", SERVICE, f"--region={REGION}"])
    runtime = f"yeongcha-runtime@{cloud.project}.iam.gserviceaccount.com"
    if not verify_owned_resource([service], SERVICE) or (
        service.get("spec", {}).get("template", {}).get("spec", {}).get("serviceAccountName")
        != runtime
    ):
        raise SetupError("기존 영차 사이트와 실행 계정을 확인하지 못했습니다. 변경하지 않았습니다.")
    base = service.get("status", {}).get("url", "").rstrip("/")
    parsed = urlsplit(base)
    if not re.fullmatch(r"https://[a-z0-9.-]+\.run\.app", base) or parsed.path:
        raise SetupError("대상 사이트의 Google 주소를 확인하지 못했습니다.")
    route_check(base)
    known = {resource_name(row) for row in cloud.request(["secrets", "list"])}
    if SECRET not in known or replace:
        value = reader("카카오 어드민 키의 실제 값 (입력 숨김): ")
        cloud.remember(value)
        value = value.strip()
        cloud.remember(value)
        if not re.fullmatch(r"[0-9a-fA-F]{32}", value):
            raise SetupError(
                "어드민 키의 실제 32자리 값을 확인해 주세요. 입력값은 저장하지 않았습니다."
            )
        if SECRET in known:
            result = cloud.request(
                ["secrets", "versions", "add", SECRET, "--data-file=-"], data=value
            )
            version = resource_name(result)
        else:
            cloud.request(
                [
                    "secrets",
                    "create",
                    SECRET,
                    "--replication-policy=user-managed",
                    f"--locations={REGION}",
                    "--data-file=-",
                ],
                data=value,
            )
            version = "1"
    else:
        versions = cloud.request(["secrets", "versions", "list", SECRET])
        enabled = [int(resource_name(row)) for row in versions if row.get("state") == "ENABLED"]
        if not enabled:
            raise SetupError("저장된 어드민 키의 활성 버전이 없습니다.")
        version = str(max(enabled))
        report("저장된 어드민 키를 재사용합니다. 값은 표시하지 않습니다.")
    if not re.fullmatch(r"[1-9][0-9]*", version):
        raise SetupError("저장된 키의 버전 번호를 확인하지 못했습니다.")
    state = cloud.request(["secrets", "versions", "describe", version, f"--secret={SECRET}"])
    if state.get("state") != "ENABLED":
        raise SetupError("어드민 키가 활성 상태가 아닙니다.")
    cloud.request(
        [
            "secrets",
            "add-iam-policy-binding",
            SECRET,
            f"--member=serviceAccount:{runtime}",
            "--role=roles/secretmanager.secretAccessor",
            "--condition=None",
        ]
    )
    cloud.request(
        [
            "run",
            "services",
            "update",
            SERVICE,
            f"--region={REGION}",
            f"--update-secrets=KAKAO_ADMIN_KEY={SECRET}:{version}",
            "--update-env-vars=KAKAO_WITHDRAWAL_ENABLED=True",
        ]
    )
    site_check(base)
    report("어드민 키 보관·사이트 연결과 기본 상태 검사가 끝났습니다. 키 값은 표시하지 않습니다.")
    report("카카오 앱이 맞는지는 회원 탈퇴 전 별도로 확인하며, 실패하면 회원을 삭제하지 않습니다.")
    report("실제 회원 탈퇴·카카오 연결 해제 성공까지 확인한 것은 아닙니다.")
    report(f"사이트: {base}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="active-venture-449808-a5")
    parser.add_argument("--replace-key", action="store_true")
    args = parser.parse_args()
    if args.project != "active-venture-449808-a5" or not shutil.which("gcloud"):
        parser.error("기존 영차 프로젝트의 인증된 Google Cloud Shell에서 실행해 주세요.")
    cloud = GoogleCloud(args.project)
    try:
        configure_withdrawal(cloud, replace=args.replace_key)
    except (SetupError, ValueError, EOFError) as error:
        print(cloud.redact(str(error)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("중단했습니다. 저장한 키는 다음 실행에서 재사용합니다.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
