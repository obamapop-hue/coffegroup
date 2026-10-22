"""Offline checks for safe interactive input, retries and OAuth redirect scope."""

import io
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode
from urllib.request import HTTPSHandler, build_opener

from gcp_configure_kakao import (
    KEYS,
    check_login_start,
    configure_kakao,
    hidden_input,
    validate_key,
    verify_authorize_url,
)
from gcp_prepare_database import GoogleCloud, SetupError

TEST_REST = "a" * 32
TEST_CLIENT = "SYNTHETIC-LOGIN-SECRET-FOR-TEST-ONLY"
BASE = "https://yeongcha-test-example.du.a.run.app"


class KakaoCloudModel(GoogleCloud):
    def __init__(self):
        super().__init__("active-venture-449808-a5")
        self.values = {}
        self.calls = []
        self.fail_update = False
        self.runtime_correct = True

    def request(self, args, *, data=None, raw=False):
        self.calls.append(args)
        if args[:3] == ["run", "services", "describe"]:
            account = f"yeongcha-runtime@{self.project}.iam.gserviceaccount.com"
            return {
                "metadata": {"name": "yeongcha-test", "labels": {"yeongcha-stage": "test"}},
                "spec": {
                    "template": {
                        "spec": {
                            "serviceAccountName": account if self.runtime_correct else "unexpected"
                        }
                    }
                },
                "status": {"url": BASE},
            }
        if args == ["secrets", "list"]:
            return [{"name": f"projects/123/secrets/{name}"} for name in self.values]
        if args[:2] == ["secrets", "create"]:
            self.values[args[2]] = {1: data}
            return {}
        if args[:3] == ["secrets", "versions", "add"]:
            version = max(self.values[args[3]]) + 1
            self.values[args[3]][version] = data
            return {"name": f"projects/123/secrets/{args[3]}/versions/{version}"}
        if args[:3] == ["secrets", "versions", "list"]:
            return [
                {"name": f"projects/123/secrets/{args[3]}/versions/{v}", "state": "ENABLED"}
                for v in self.values[args[3]]
            ]
        if args[:3] == ["secrets", "versions", "describe"]:
            return {"state": "ENABLED"}
        if args[:2] == ["secrets", "add-iam-policy-binding"]:
            return {}
        if args[:3] == ["run", "services", "update"]:
            if self.fail_update:
                raise SetupError("Temporary update failure")
            return {}
        raise AssertionError(args)


class KakaoConfigurationTests(unittest.TestCase):
    def configure(self, cloud, values=(TEST_REST, TEST_CLIENT), **kwargs):
        supplied = iter(values)
        with patch("gcp_configure_kakao.check_site"):
            return configure_kakao(
                cloud,
                reader=lambda prompt: next(supplied),
                site_check=lambda url: None,
                login_check=lambda url: None,
                report=lambda message: None,
                **kwargs,
            )

    def test_new_keys_stored_privately_and_only_kakao_settings_updated(self):
        cloud = KakaoCloudModel()
        self.assertEqual(self.configure(cloud), BASE)
        self.assertEqual(cloud.values[KEYS[0][1]][1], TEST_REST)
        self.assertEqual(cloud.values[KEYS[1][1]][1], TEST_CLIENT)
        update = next(c for c in cloud.calls if c[:3] == ["run", "services", "update"])
        self.assertIn("--update-env-vars=KAKAO_LOGIN_ENABLED=True", update)
        self.assertTrue(
            any("KAKAO_RESET_API_KEY=yeongcha-kakao-rest-api-key:1" in a for a in update)
        )
        self.assertTrue(
            any("KAKAO_CLIENT_SECRET=yeongcha-kakao-login-secret:1" in a for a in update)
        )
        self.assertFalse(any(a.startswith(("--set-env-vars", "--set-secrets")) for a in update))
        self.assertNotIn(TEST_REST, " ".join(update))
        self.assertNotIn(TEST_CLIENT, " ".join(update))
        grants = [c for c in cloud.calls if c[:2] == ["secrets", "add-iam-policy-binding"]]
        self.assertEqual({c[2] for c in grants}, {row[1] for row in KEYS})

    def test_retry_after_update_failure_reuses_saved_keys_without_prompt(self):
        cloud = KakaoCloudModel()
        cloud.fail_update = True
        with self.assertRaises(SetupError):
            self.configure(cloud)
        snapshot = {k: dict(v) for k, v in cloud.values.items()}
        cloud.fail_update = False
        self.configure(cloud, values=())
        self.assertEqual(snapshot, cloud.values)

    def test_missing_second_key_does_not_enable_login(self):
        cloud = KakaoCloudModel()
        with self.assertRaises(SetupError):
            self.configure(cloud, values=(TEST_REST, ""))
        self.assertTrue(cloud.values.get(KEYS[0][1]))
        self.assertFalse(any(c[:3] == ["run", "services", "update"] for c in cloud.calls))
        self.configure(cloud, values=(TEST_CLIENT,))

    def test_explicit_replacement_adds_and_pins_new_versions(self):
        cloud = KakaoCloudModel()
        self.configure(cloud)
        self.configure(cloud, replace=True, values=("b" * 32, TEST_CLIENT + "-NEW"))
        self.assertEqual(cloud.values[KEYS[0][1]][1], TEST_REST)
        update = [c for c in cloud.calls if c[:3] == ["run", "services", "update"]][-1]
        self.assertTrue(any("yeongcha-kakao-rest-api-key:2" in a for a in update))
        self.assertTrue(any("yeongcha-kakao-login-secret:2" in a for a in update))

    def test_wrong_runtime_does_not_prompt_or_write(self):
        cloud = KakaoCloudModel()
        cloud.runtime_correct = False
        with self.assertRaises(SetupError):
            self.configure(cloud, values=())
        self.assertFalse(cloud.values)

    def test_no_echo_and_no_fallback_to_unprotected_input(self):
        with (
            patch("gcp_configure_kakao.sys.stdin.isatty", return_value=False),
            patch("gcp_configure_kakao.getpass.getpass") as read,
        ):
            with self.assertRaises(SetupError):
                hidden_input("input:")
            read.assert_not_called()
        cloud = KakaoCloudModel()
        values = iter([TEST_REST, TEST_CLIENT])
        output = io.StringIO()
        with redirect_stdout(output):
            configure_kakao(
                cloud,
                reader=lambda prompt: next(values),
                site_check=lambda url: None,
                login_check=lambda url: None,
            )
        self.assertNotIn(TEST_REST, output.getvalue())
        self.assertNotIn(TEST_CLIENT, output.getvalue())

    def test_wrong_key_name_and_control_characters_are_rejected_without_echo(self):
        for variable, value in [
            (KEYS[0][0], "Default Rest API Key"),
            (KEYS[1][0], "input-with-newline\n" + TEST_CLIENT),
        ]:
            with self.subTest(variable=variable), self.assertRaises(SetupError) as raised:
                validate_key(variable, value)
            self.assertNotIn(value, str(raised.exception))

    def test_login_check_requires_callback_host_state_and_nickname_only(self):
        params = {
            "client_id": TEST_REST,
            "redirect_uri": BASE + "/accounts/kakao/login/callback/",
            "response_type": "code",
            "scope": "profile_nickname",
            "state": "test-only-state",
        }
        endpoint = "https://kauth.kakao.com/oauth/authorize?"
        verify_authorize_url(endpoint + urlencode(params), BASE)
        for field, value in [
            ("redirect_uri", "https://wrong.example/callback/"),
            ("scope", "profile_nickname account_email"),
            ("state", ""),
        ]:
            with self.subTest(field=field), self.assertRaises(SetupError):
                verify_authorize_url(endpoint + urlencode({**params, field: value}), BASE)
        with self.assertRaises(SetupError):
            verify_authorize_url("https://wrong.example/oauth/authorize?" + urlencode(params), BASE)


class LoginHttpTests(unittest.TestCase):
    def test_real_http_csrf_and_redirect_without_contacting_kakao(self):
        observed = []

        class NeverContactKakao(HTTPSHandler):
            def https_open(self, request):
                raise AssertionError("The check must not contact the external OAuth endpoint.")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Set-Cookie", "csrftoken=test-only-cookie; Path=/")
                self.end_headers()
                self.wfile.write(b'<input name="csrfmiddlewaretoken" value="test-only-csrf">')

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                observed.append((self.path, self.headers.get("Cookie"), parse_qs(body)))
                params = {
                    "client_id": TEST_REST,
                    "redirect_uri": address + "/accounts/kakao/login/callback/",
                    "scope": "profile_nickname",
                    "response_type": "code",
                    "state": "test-only-state",
                }
                self.send_response(302)
                self.send_header(
                    "Location", "https://kauth.kakao.com/oauth/authorize?" + urlencode(params)
                )
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        address = f"http://127.0.0.1:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:

            def opener(*handlers):
                return build_opener(*handlers, NeverContactKakao())

            with patch("gcp_configure_kakao.build_opener", side_effect=opener):
                check_login_start(address)
            self.assertEqual(
                observed,
                [
                    (
                        "/accounts/start/organizer/",
                        "csrftoken=test-only-cookie",
                        {"csrfmiddlewaretoken": ["test-only-csrf"]},
                    )
                ],
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
