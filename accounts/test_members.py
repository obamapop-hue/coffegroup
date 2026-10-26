import importlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from allauth.socialaccount.models import SocialAccount
from django.apps import apps
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cafes.models import Cafe

from .models import KakaoUnlinkJob, MembershipRecord, User
from .tests import PROVIDERS, json_response
from .withdrawal import process_unlink_jobs, withdraw_member


@override_settings(KAKAO_WITHDRAWAL_ENABLED=True, KAKAO_ADMIN_KEY="test-admin-only")
class MemberManagementTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            "member-operator", nickname="운영자", is_operator=True
        )
        self.joined = timezone.now() - timedelta(days=20)
        self.member = User.objects.create_user(
            "member-user", nickname="확인할 회원", date_joined=self.joined
        )
        self.uid = "PRIVATE_KAKAO_ID_NOT_FOR_DISPLAY"
        SocialAccount.objects.create(user=self.member, provider="kakao", uid=self.uid)
        self.client.force_login(self.operator)
        self.url = reverse("accounts:members")

    def test_existing_member_join_date_roles_and_cafe_are_visible_without_credentials(self):
        cafe = Cafe.objects.create(name="회원 확인 카페", seller=self.member)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        row = next(
            row for row in response.context["rows"] if row["nickname"] == self.member.nickname
        )
        self.assertEqual(row["joined_at"], self.joined)
        self.assertEqual(row["status"], "가입")
        self.assertIn("판매자", row["roles"])
        self.assertEqual(row["cafe"], cafe.name)
        self.assertEqual(row["connection"], "연결됨")
        self.assertNotContains(response, self.uid)
        self.assertNotContains(response, str(self.member.account_code))
        self.assertNotContains(response, self.member.username)
        self.assertIn("no-store", response["Cache-Control"])

    def test_withdrawal_keeps_named_history_and_pending_status_then_confirms_unlink(self):
        code = str(self.member.account_code)
        record_id = self.member.membership_record.pk
        job_id = withdraw_member(user=self.member)
        record = MembershipRecord.objects.get(pk=record_id)
        self.assertIsNone(record.user_id)
        self.assertEqual(record.nickname, "확인할 회원")
        self.assertEqual(record.joined_at, self.joined)
        self.assertIsNotNone(record.withdrawn_at)
        self.assertIsNone(record.unlink_confirmed_at)
        self.assertEqual(KakaoUnlinkJob.objects.get(pk=job_id).membership_record_id, record_id)
        response = self.client.get(self.url, {"status": "withdrawn"})
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["connection"], "해제 대기")
        self.assertNotContains(response, self.uid)
        self.assertNotContains(response, code)
        with patch("accounts.withdrawal.unlink_from_kakao", return_value=True):
            self.assertEqual(process_unlink_jobs(job_id=job_id), 1)
        record.refresh_from_db()
        self.assertIsNotNone(record.unlink_confirmed_at)
        response = self.client.get(self.url, {"status": "withdrawn"})
        self.assertEqual(response.context["rows"][0]["connection"], "해제 완료")
        self.assertFalse(KakaoUnlinkJob.objects.exists())

    def test_failed_or_repeated_withdrawal_does_not_add_false_or_duplicate_history(self):
        record_id = self.member.membership_record.pk
        with (
            patch("accounts.withdrawal.User.delete", side_effect=RuntimeError("fail")),
            self.assertRaises(RuntimeError),
        ):
            withdraw_member(user=self.member)
        record = MembershipRecord.objects.get(pk=record_id)
        self.assertEqual(record.user_id, self.member.pk)
        self.assertIsNone(record.withdrawn_at)
        self.assertFalse(KakaoUnlinkJob.objects.exists())
        withdraw_member(user=self.member)
        self.assertIsNone(withdraw_member(user=self.member))
        self.assertEqual(MembershipRecord.objects.filter(nickname="확인할 회원").count(), 1)

    def test_rejoining_creates_new_membership_without_relinking_old_history(self):
        old = self.member.membership_record.pk
        job = withdraw_member(user=self.member)
        with patch("accounts.withdrawal.unlink_from_kakao", return_value=True):
            process_unlink_jobs(job_id=job)
        new = User.objects.create_user("member-rejoined", nickname="확인할 회원")
        SocialAccount.objects.create(user=new, provider="kakao", uid=self.uid)
        self.assertNotEqual(new.membership_record.pk, old)
        response = self.client.get(self.url, {"q": "확인할 회원"})
        self.assertEqual({row["status"] for row in response.context["rows"]}, {"가입", "탈퇴"})
        self.assertEqual(len(response.context["rows"]), 2)

    def test_only_operator_can_view_and_no_management_write_endpoint_is_exposed(self):
        self.assertEqual(Client().get(self.url).status_code, 302)
        member_client = Client()
        member_client.force_login(self.member)
        self.assertEqual(member_client.get(self.url).status_code, 403)
        Cafe.objects.create(name="판매자", seller=self.member)
        self.assertEqual(member_client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"delete": self.member.pk}).status_code, 405)
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())

    def test_search_filter_pagination_and_html_are_safe(self):
        for index in range(28):
            User.objects.create_user(f"extra-{index}", nickname=f"추가 회원 {index}")
        self.assertEqual(len(self.client.get(self.url).context["rows"]), 25)
        self.assertEqual(len(self.client.get(self.url, {"page": 2}).context["rows"]), 5)
        record = self.member.membership_record
        row = self.client.get(self.url, {"q": str(record.pk)}).context["rows"]
        self.assertIn(record.pk, {item["number"] for item in row})
        self.member.nickname = "<script>alert(1)</script>"
        self.member.save(update_fields=["nickname"])
        response = self.client.get(self.url, {"q": self.member.nickname, "status": "current"})
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(self.client.get(self.url, {"status": "withdrawn"}).context["rows"], [])

    def test_inactive_is_not_marked_as_withdrawn_and_missing_old_account_is_not_invented(self):
        self.member.is_active = False
        self.member.save(update_fields=["is_active"])
        row = self.client.get(self.url, {"q": "확인할 회원"}).context["rows"][0]
        self.assertEqual(row["status"], "이용 중지")
        self.assertIsNone(row["withdrawn_at"])
        self.assertFalse(self.client.get(self.url, {"status": "withdrawn"}).context["rows"])
        self.assertContains(
            self.client.get(self.url),
            "기능 적용 전에 삭제된 계정은 이름·탈퇴 시각을 복원하지 못해요.",
        )

    def test_migration_backfill_and_rolling_deployment_repair_preserve_actual_join_dates(self):
        MembershipRecord.objects.all().delete()
        migration = importlib.import_module(
            "accounts.migrations.0004_membershiprecord_kakaounlinkjob_membership_record"
        )
        editor = SimpleNamespace(connection=connection)
        migration.backfill_memberships(apps, editor)
        migration.backfill_memberships(apps, editor)
        self.assertEqual(MembershipRecord.objects.count(), 2)
        self.assertEqual(MembershipRecord.objects.get(user=self.member).joined_at, self.joined)
        MembershipRecord.objects.filter(user=self.member).delete()
        self.client.get(self.url)
        self.assertEqual(MembershipRecord.objects.get(user=self.member).joined_at, self.joined)

    @override_settings(KAKAO_LOGIN_ENABLED=True, SOCIALACCOUNT_PROVIDERS=PROVIDERS)
    def test_real_kakao_callback_updates_existing_name_without_second_signup_record(self):
        self.client.logout()
        response = self.client.post(reverse("accounts:start", args=["organizer"]))
        state = parse_qs(urlparse(response.url).query)["state"][0]
        data = [
            json_response({"access_token": "test-token"}),
            json_response({"id": self.uid, "properties": {"nickname": "변경한 닉네임"}}),
        ]
        with patch("requests.sessions.Session.request", side_effect=data):
            self.client.get(reverse("kakao_callback"), {"state": state, "code": "test-code"})
        record = MembershipRecord.objects.get(user=self.member)
        self.assertEqual(record.nickname, "변경한 닉네임")
        self.assertEqual(record.joined_at, self.joined)
        self.assertEqual(MembershipRecord.objects.count(), 2)
