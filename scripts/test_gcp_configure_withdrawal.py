import unittest

from gcp_configure_withdrawal import SECRET, configure_withdrawal
from gcp_prepare_database import SetupError
from test_gcp_configure_kakao import BASE, KakaoCloudModel


class WithdrawalSetupTests(unittest.TestCase):
    def configure(self, cloud, **kwargs):
        return configure_withdrawal(
            cloud,
            reader=lambda prompt: "b" * 32,
            route_check=lambda url: None,
            site_check=lambda url: None,
            report=lambda message: None,
            **kwargs,
        )

    def test_only_one_secret_and_feature_flag_are_merged_into_web_not_migration_job(self):
        cloud = KakaoCloudModel()
        self.configure(cloud)
        self.assertEqual(cloud.values[SECRET][1], "b" * 32)
        update = next(call for call in cloud.calls if call[:3] == ["run", "services", "update"])
        self.assertIn(f"--update-secrets=KAKAO_ADMIN_KEY={SECRET}:1", update)
        self.assertIn("--update-env-vars=KAKAO_WITHDRAWAL_ENABLED=True", update)
        self.assertFalse(
            any("--set-env-vars" in value or "--set-secrets" in value for value in update)
        )
        self.assertFalse(any("jobs" in call for call in cloud.calls))
        self.assertTrue(
            any("--role=roles/secretmanager.secretAccessor" in call for call in cloud.calls)
        )

    def test_existing_secret_is_reused_without_reading_value_or_prompting(self):
        cloud = KakaoCloudModel()
        self.configure(cloud)
        configure_withdrawal(
            cloud,
            reader=lambda prompt: self.fail("re-prompt"),
            route_check=lambda url: None,
            site_check=lambda url: None,
            report=lambda message: None,
        )
        self.assertEqual(len(cloud.values[SECRET]), 1)
        self.assertFalse(any(call[:3] == ["secrets", "versions", "access"] for call in cloud.calls))

    def test_invalid_value_does_not_store_or_update_and_can_be_redacted(self):
        cloud = KakaoCloudModel()
        with self.assertRaises(SetupError):
            configure_withdrawal(
                cloud, reader=lambda prompt: "invalid-private-value", route_check=lambda url: None
            )
        self.assertFalse(cloud.values)
        self.assertNotIn("invalid-private-value", cloud.redact("invalid-private-value"))

    def test_wrong_runtime_or_missing_site_code_stops_before_secret_input(self):
        cloud = KakaoCloudModel()
        cloud.runtime_correct = False
        with self.assertRaises(SetupError):
            self.configure(cloud)
        self.assertFalse(cloud.values)
        cloud.runtime_correct = True
        with self.assertRaises(SetupError):
            configure_withdrawal(
                cloud,
                reader=lambda prompt: self.fail("premature input"),
                route_check=lambda url: (_ for _ in ()).throw(SetupError("old version")),
            )
        self.assertFalse(cloud.values)

    def test_deployment_failure_keeps_saved_secret_and_repeat_does_not_rotate_key(self):
        cloud = KakaoCloudModel()
        cloud.fail_update = True
        with self.assertRaises(SetupError):
            self.configure(cloud)
        cloud.fail_update = False
        self.configure(cloud)
        self.assertEqual(len(cloud.values[SECRET]), 1)

    def test_setup_messages_never_claim_real_membership_deletion_or_expose_key(self):
        cloud = KakaoCloudModel()
        messages = []
        configure_withdrawal(
            cloud,
            reader=lambda prompt: "b" * 32,
            route_check=lambda url: None,
            site_check=lambda url: self.assertEqual(url, BASE),
            report=messages.append,
        )
        self.assertNotIn("b" * 32, "\n".join(messages))
        self.assertTrue(any("성공까지 확인한 것은 아닙니다" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
