"""Safely enter Kakao keys in Cloud Shell and enable the existing Google site.

Keys go directly to Secret Manager, never to shell history, local files or output.
Requires the login Redirect URI to have been registered in the existing Kakao app.
"""

import argparse
import getpass
import http.cookiejar
import re
import shutil
import sys
import warnings
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

from gcp_deploy_test import SERVICE, check_site, resource_name, verify_owned_resource
from gcp_prepare_database import REGION, GoogleCloud, SetupError

KEYS = [
    (
        "KAKAO_RESET_API_KEY",
        "yeongcha-kakao-rest-api-key",
        "1. 카카오 REST API 키 값 (입력 숨김): ",
    ),
    (
        "KAKAO_CLIENT_SECRET",
        "yeongcha-kakao-login-secret",
        "2. 카카오 로그인용 Client Secret (입력 숨김): ",
    ),
]


def hidden_input(prompt):
    if not sys.stdin.isatty():
        raise SetupError(
            "Cloud Shell 입력창에서 직접 실행해 주세요. 키를 화면에 표시하지 않습니다."
        )
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning:
            raise SetupError(
                "입력을 숨길 수 없어 중단했습니다. Cloud Shell에서 직접 실행해 주세요."
            ) from None


def validate_key(variable, value):
    value = value.strip()
    if variable == "KAKAO_RESET_API_KEY":
        if not re.fullmatch(r"[0-9a-fA-F]{32}", value):
            raise SetupError(
                "REST API 키의 실제 32자리 값을 복사해 주세요. 입력값은 저장하지 않았습니다."
            )
    elif not (16 <= len(value) <= 512) or any(not 33 <= ord(c) <= 126 for c in value):
        raise SetupError(
            "카카오 로그인용 Client Secret 값을 확인해 주세요. 입력값은 저장하지 않았습니다."
        )
    return value


def verify_authorize_url(location, base_url):
    parsed = urlsplit(location)
    params = parse_qs(parsed.query)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "kauth.kakao.com"
        or parsed.path != "/oauth/authorize"
        or params.get("redirect_uri") != [base_url + "/accounts/kakao/login/callback/"]
        or params.get("response_type") != ["code"]
        or not params.get("state")
        or set(" ".join(params.get("scope", [])).split()) != {"profile_nickname"}
        or not re.fullmatch(r"[0-9a-fA-F]{32}", params.get("client_id", [""])[0])
    ):
        raise SetupError("카카오 로그인 시작 주소의 설정 검사를 통과하지 못했습니다.")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def check_login_start(base_url):
    opener = build_opener(NoRedirect, HTTPCookieProcessor(http.cookiejar.CookieJar()))
    entry = base_url + "/accounts/entry/organizer/"
    try:
        with opener.open(entry, timeout=30) as response:
            csrf = re.search(
                r'name="csrfmiddlewaretoken" value="([^"]+)"', response.read().decode()
            )
        if not csrf:
            raise SetupError("로그인 화면의 요청 보호값을 확인하지 못했습니다.")
        body = urlencode({"csrfmiddlewaretoken": csrf[1]}).encode()
        request = Request(
            base_url + "/accounts/start/organizer/", data=body, headers={"Referer": entry}
        )
        try:
            with opener.open(request, timeout=30):
                raise SetupError("로그인 시작 요청이 예상한 이동 응답을 반환하지 않았습니다.")
        except HTTPError as error:
            if error.code != 302:
                raise SetupError(f"로그인 시작 검사 실패: HTTP {error.code}") from None
            verify_authorize_url(error.headers.get("Location", ""), base_url)
    except HTTPError as error:
        raise SetupError(f"로그인 화면 조회 실패: HTTP {error.code}") from None
    except URLError as error:
        raise SetupError("로그인 시작 검사의 HTTPS 연결을 확인하지 못했습니다.") from error


def configure_kakao(
    cloud,
    *,
    replace=False,
    reader=hidden_input,
    site_check=check_site,
    login_check=check_login_start,
    report=None,
):
    if report is None:

        def report(message):
            print(message, flush=True)

    region = f"--region={REGION}"
    service = cloud.request(["run", "services", "describe", SERVICE, region])
    if not verify_owned_resource([service], SERVICE):
        raise SetupError("대상 테스트 서비스를 확인하지 못했습니다. 설정을 변경하지 않았습니다.")
    runtime = f"yeongcha-runtime@{cloud.project}.iam.gserviceaccount.com"
    account = service.get("spec", {}).get("template", {}).get("spec", {}).get("serviceAccountName")
    if account is None:
        account = service.get("template", {}).get("serviceAccount")
    if account != runtime:
        raise SetupError("웹 실행 계정이 예상한 계정과 다릅니다. 설정을 변경하지 않았습니다.")
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
        raise SetupError("Google 사이트의 실제 주소를 확인하지 못했습니다.")
    report("카카오에 등록한 로그인 Redirect URI:")
    report(base_url + "/accounts/kakao/login/callback/")
    known = {resource_name(row) for row in cloud.request(["secrets", "list"])}
    references = []
    for variable, secret_name, prompt in KEYS:
        if secret_name not in known or replace:
            value = reader(prompt)
            cloud.remember(value)
            value = validate_key(variable, value)
            cloud.remember(value)
            if secret_name in known:
                result = cloud.request(
                    ["secrets", "versions", "add", secret_name, "--data-file=-"], data=value
                )
                version = resource_name(result)
            else:
                cloud.request(
                    [
                        "secrets",
                        "create",
                        secret_name,
                        "--replication-policy=user-managed",
                        f"--locations={REGION}",
                        "--data-file=-",
                    ],
                    data=value,
                )
                version = "1"
            if not re.fullmatch(r"[1-9][0-9]*", version):
                raise SetupError("저장된 비밀값의 버전 번호를 확인하지 못했습니다.")
            report(f"{variable}: 비밀값 보관 완료 (값은 표시하지 않습니다).")
        else:
            versions = cloud.request(["secrets", "versions", "list", secret_name])
            enabled = [int(resource_name(row)) for row in versions if row.get("state") == "ENABLED"]
            if not enabled:
                raise SetupError(f"{variable}: 활성 비밀값 버전이 없어 중단했습니다.")
            version = str(max(enabled))
            report(f"{variable}: 저장된 비밀값을 재사용합니다.")
        status = cloud.request(
            ["secrets", "versions", "describe", version, f"--secret={secret_name}"]
        )
        if status.get("state") != "ENABLED":
            raise SetupError(f"{variable}: 비밀값이 활성 상태가 아닙니다.")
        cloud.request(
            [
                "secrets",
                "add-iam-policy-binding",
                secret_name,
                f"--member=serviceAccount:{runtime}",
                "--role=roles/secretmanager.secretAccessor",
                "--condition=None",
            ]
        )
        references.append(f"{variable}={secret_name}:{version}")

    report("저장된 키 두 개를 웹에 연결하고 카카오 로그인을 켭니다. 잠시 기다려 주세요.")
    cloud.request(
        [
            "run",
            "services",
            "update",
            SERVICE,
            region,
            "--update-secrets=" + ",".join(references),
            "--update-env-vars=KAKAO_LOGIN_ENABLED=True",
            "--update-labels=yeongcha-kakao=enabled",
        ]
    )
    site_check(base_url)
    login_check(base_url)
    report("사이트·DB·카카오 로그인 시작 주소 검사를 통과했습니다.")
    report(f"사이트: {base_url}")
    report("브라우저에서 카카오 로그인을 완료해 주세요. 운영자 권한은 본인 확인 후 연결합니다.")
    return base_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="active-venture-449808-a5")
    parser.add_argument(
        "--replace-keys", action="store_true", help="명시적으로 새 키 값을 입력해 교체합니다."
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", args.project):
        parser.error("프로젝트 이름이 아닌 프로젝트 ID를 입력해 주세요.")
    if not shutil.which("gcloud"):
        parser.error("인증된 Google Cloud Shell에서 실행해 주세요.")
    cloud = GoogleCloud(args.project)
    try:
        configure_kakao(cloud, replace=args.replace_keys)
    except (SetupError, ValueError, EOFError) as error:
        print(cloud.redact(str(error)) or "입력이 완료되지 않아 중단했습니다.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("중단했습니다. 이미 저장된 키는 다음 실행에서 재사용합니다.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
