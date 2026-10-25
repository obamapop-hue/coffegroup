import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import requests
from allauth.socialaccount.models import SocialAccount, SocialToken
from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connections, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cafes.services import open_for_seller
from orders.models import FinalOrder, Room, SubmissionRevision
from orders.selling import complete_order
from orders.services import create_room, delete_room
from orders.test_selling import SellingFixture

from .models import KakaoUnlinkJob, User
from .tests import PROVIDERS, json_response
from .withdrawal import (
    process_unlink_jobs,
    unlink_from_kakao,
    verify_admin_member,
    withdraw_member,
)


@override_settings(
    KAKAO_LOGIN_ENABLED=True,
    KAKAO_WITHDRAWAL_ENABLED=True,
    KAKAO_ADMIN_KEY="synthetic-admin-key",
    SOCIALACCOUNT_PROVIDERS=PROVIDERS,
)
class WithdrawalTests(SellingFixture, TestCase):
    def setUp(self):
        super().setUp()
        SocialAccount.objects.create(user=self.owner, provider="kakao", uid="12345")
        SocialAccount.objects.create(user=self.seller, provider="kakao", uid="67890")

    def proof(self, client=None, *, age=0, user=None):
        client = client or self.client
        session = client.session
        session["withdrawal_verified"] = {
            "user": (user or self.owner).pk,
            "at": time.time() - age,
        }
        session.save()

    def test_privacy_draft_is_operator_only_and_not_published_in_public_navigation(self):
        url = reverse("accounts:privacy-preview")
        self.assertEqual(Client().get(url).status_code, 302)
        self.assertEqual(self.client.get(url).status_code, 403)
        admin = Client()
        admin.force_login(self.other)
        self.assertContains(admin.get(url), "아직 공개하지 않은 초안")
        self.assertNotContains(Client().get(reverse("home")), url)

    def test_operator_can_verify_kakao_connection_without_withdrawing_any_account(self):
        SocialAccount.objects.create(user=self.other, provider="kakao", uid="55555")
        admin = Client()
        admin.force_login(self.other)
        count = User.objects.count()
        with patch("accounts.withdrawal_views.verify_admin_member", return_value=True) as remote:
            response = admin.post(reverse("accounts:withdraw-check"))
            remote.assert_called_once_with("55555")
        self.assertRedirects(response, reverse("accounts:privacy-preview"))
        self.assertEqual(User.objects.count(), count)
        self.assertFalse(KakaoUnlinkJob.objects.exists())
        self.assertEqual(self.client.post(reverse("accounts:withdraw-check")).status_code, 403)

    def finished(self):
        final = self.sent()
        self.accept()
        complete_order(user=self.seller, room_id=self.room.pk)
        return final

    def test_delete_membership_retains_final_content_names_history_and_protection(self):
        final = self.finished()
        original = (final.items, final.organizer_nickname, final.total_amount, final.sent_at)
        events = list(self.room.events.values_list("action", "created_at"))
        revisions = list(SubmissionRevision.objects.values_list("snapshot", flat=True))
        other_device = Client()
        other_device.force_login(self.owner)
        owned_session = other_device.session.session_key
        untouched = Client()
        untouched.force_login(self.other)
        foreign_session = untouched.session.session_key
        member_id = self.owner.pk
        job_id = withdraw_member(user=self.owner)
        self.assertFalse(User.objects.filter(pk=member_id).exists())
        self.assertFalse(SocialAccount.objects.filter(uid="12345").exists())
        self.assertFalse(Session.objects.filter(pk=owned_session).exists())
        self.assertTrue(Session.objects.filter(pk=foreign_session).exists())
        self.room.refresh_from_db()
        final.refresh_from_db()
        self.assertIsNone(self.room.organizer_id)
        self.assertEqual(
            original, (final.items, final.organizer_nickname, final.total_amount, final.sent_at)
        )
        self.assertEqual(events, list(self.room.events.values_list("action", "created_at")))
        self.assertEqual(
            revisions, list(SubmissionRevision.objects.values_list("snapshot", flat=True))
        )
        self.assertTrue(KakaoUnlinkJob.objects.filter(pk=job_id, kakao_uid="12345").exists())
        self.assertIsNone(withdraw_member(user=self.owner))
        self.assertEqual(KakaoUnlinkJob.objects.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinalOrder.objects.filter(pk=final.pk).update(organizer_nickname="changed")

    def test_active_organizer_orders_are_blocked_in_all_four_states(self):
        for action in [None, "closed", "pending", "accepted"]:
            if action == "closed":
                self.prepare()
            elif action == "pending":
                from orders.dispatch import send_order

                send_order(user=self.owner, room_id=self.room.pk, confirmation_token=self.token)
            elif action == "accepted":
                self.accept()
            with self.assertRaises(ValidationError):
                withdraw_member(user=self.owner)
            self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())
            self.assertFalse(KakaoUnlinkJob.objects.exists())

    def test_operator_and_cafe_pending_or_accepted_orders_are_blocked(self):
        SocialAccount.objects.create(user=self.other, provider="kakao", uid="55555")
        with self.assertRaises(ValidationError):
            withdraw_member(user=self.other)
        self.sent()
        with self.assertRaises(ValidationError):
            withdraw_member(user=self.seller)
        self.accept()
        with self.assertRaises(ValidationError):
            withdraw_member(user=self.seller)

    def test_seller_without_active_orders_is_unlinked_closed_and_old_session_cannot_reopen(self):
        # The organizer's unsent order is not a pending cafe order and keeps its own expiry.
        job = withdraw_member(user=self.seller)
        self.cafe.refresh_from_db()
        self.assertFalse(self.cafe.is_open)
        self.assertIsNone(self.cafe.seller_id)
        self.assertTrue(KakaoUnlinkJob.objects.filter(pk=job).exists())
        with self.assertRaises(PermissionDenied):
            open_for_seller(user=self.seller)
        with self.assertRaises(PermissionDenied):
            create_room(user=self.seller, cafe_id=self.cafe.pk)

    def test_post_needs_csrf_confirmation_and_recent_proof_bound_to_current_member(self):
        delete_room(user=self.owner, room_id=self.room.pk)
        url = reverse("accounts:withdraw")
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(url, {"confirm": "on"}).status_code, 403)
        for data, age, user in [
            ({"confirm": "on"}, 301, None),
            ({"confirm": "on"}, 0, self.other),
            ({}, 0, None),
        ]:
            self.proof(age=age, user=user)
            with patch("accounts.withdrawal_views.verify_admin_member") as remote:
                self.assertEqual(self.client.post(url, data).status_code, 400)
                remote.assert_not_called()
            self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())
        self.assertEqual(Client().post(url).status_code, 302)

    def test_wrong_admin_key_does_not_delete_local_member_or_send_unlink(self):
        delete_room(user=self.owner, room_id=self.room.pk)
        self.proof()
        with patch("accounts.withdrawal_views.verify_admin_member", return_value=False):
            response = self.client.post(reverse("accounts:withdraw"), {"confirm": "on"})
        self.assertEqual(response.status_code, 503)
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())
        self.assertFalse(KakaoUnlinkJob.objects.exists())

    def test_real_http_view_success_deletes_and_logs_out_without_exposing_ids(self):
        delete_room(user=self.owner, room_id=self.room.pk)
        self.proof()
        with (
            patch("accounts.withdrawal_views.verify_admin_member", return_value=True),
            patch("accounts.withdrawal.unlink_from_kakao", return_value=True),
        ):
            response = self.client.post(reverse("accounts:withdraw"), {"confirm": "on"})
        self.assertRedirects(response, reverse("accounts:withdraw-done"))
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertFalse(KakaoUnlinkJob.objects.exists())

    def test_network_failure_persists_queue_and_retry_uses_backoff_and_then_erases_uid(self):
        delete_room(user=self.owner, room_id=self.room.pk)
        job_id = withdraw_member(user=self.owner)
        now = timezone.now()
        with patch("accounts.withdrawal.unlink_from_kakao", return_value=False) as remote:
            self.assertEqual(process_unlink_jobs(now=now), 0)
            job = KakaoUnlinkJob.objects.get(pk=job_id)
            self.assertEqual(job.attempts, 1)
            self.assertEqual(job.next_attempt_at, now + timedelta(seconds=60))
            process_unlink_jobs(now=now + timedelta(seconds=59))
            remote.assert_called_once()
        with patch("accounts.withdrawal.unlink_from_kakao", return_value=True):
            self.assertEqual(process_unlink_jobs(now=now + timedelta(seconds=60)), 1)
            self.assertEqual(process_unlink_jobs(now=now + timedelta(seconds=60)), 0)
        self.assertFalse(KakaoUnlinkJob.objects.exists())

    def test_changed_key_never_turns_unknown_membership_into_confirmed_unlink(self):
        KakaoUnlinkJob.objects.create(kakao_uid="12345", credential_fingerprint="old-key")
        with patch("accounts.withdrawal.unlink_from_kakao") as remote:
            process_unlink_jobs()
            remote.assert_not_called()
        self.assertEqual(KakaoUnlinkJob.objects.get().attempts, 1)

    def test_api_requests_are_fixed_private_bounded_and_validate_exact_member(self):
        with patch(
            "requests.sessions.Session.request", return_value=json_response({"id": 12345})
        ) as remote:
            self.assertTrue(verify_admin_member("12345"))
            self.assertTrue(unlink_from_kakao("12345"))
        method, url = remote.call_args.args[:2]
        self.assertEqual((method, url), ("POST", "https://kapi.kakao.com/v1/user/unlink"))
        self.assertEqual(
            remote.call_args.kwargs["data"], {"target_id_type": "user_id", "target_id": "12345"}
        )
        self.assertFalse(remote.call_args.kwargs["allow_redirects"])
        self.assertEqual(remote.call_args.kwargs["timeout"], 8)
        for payload, status, success in [
            ({"id": 67890}, 200, False),
            ({"code": -101}, 400, True),
            ({"code": -401}, 401, False),
            ({"id": 12345}, 302, False),
            ([], 200, False),
        ]:
            with patch(
                "requests.sessions.Session.request", return_value=json_response(payload, status)
            ):
                self.assertEqual(unlink_from_kakao("12345"), success)
        with patch("requests.sessions.Session.request", side_effect=requests.Timeout("private")):
            self.assertFalse(unlink_from_kakao("12345"))

    def test_oauth_reauthentication_is_one_use_and_cannot_switch_accounts(self):
        delete_room(user=self.owner, room_id=self.room.pk)
        response = self.client.post(
            reverse("accounts:withdraw-verify"), {"next": "https://evil.example", "scope": "email"}
        )
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query["prompt"], ["login"])
        self.assertEqual(query["scope"], ["profile_nickname"])
        responses = [
            json_response({"access_token": "test-token"}),
            json_response({"id": 12345, "properties": {"nickname": "검증"}}),
        ]
        with patch("requests.sessions.Session.request", side_effect=responses):
            self.client.get(reverse("kakao_callback"), {"state": query["state"][0], "code": "test"})
        self.assertEqual(self.client.session["withdrawal_verified"]["user"], self.owner.pk)
        self.assertEqual(SocialToken.objects.count(), 0)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.owner.pk))
        with patch("requests.sessions.Session.request") as remote:
            self.client.get(
                reverse("kakao_callback"), {"state": query["state"][0], "code": "replay"}
            )
            remote.assert_not_called()
        self.client.post(reverse("accounts:withdraw-verify"))
        state = next(reversed(self.client.session["socialaccount_states"]))
        responses[1] = json_response({"id": 67890, "properties": {"nickname": "다른 회원"}})
        with patch("requests.sessions.Session.request", side_effect=responses):
            self.client.get(reverse("kakao_callback"), {"state": state, "code": "other"})
        self.assertNotIn("withdrawal_verified", self.client.session)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.owner.pk))

    def test_pending_unlink_blocks_normal_registration_until_remote_confirmation(self):
        delete_room(user=self.owner, room_id=self.room.pk)
        withdraw_member(user=self.owner)
        self.client.logout()
        begin = self.client.post(reverse("accounts:start", args=["organizer"]))
        state = parse_qs(urlparse(begin.url).query)["state"][0]
        responses = [
            json_response({"access_token": "test-token"}),
            json_response({"id": 12345, "properties": {"nickname": "재가입"}}),
        ]
        with patch("requests.sessions.Session.request", side_effect=responses):
            result = self.client.get(reverse("kakao_callback"), {"state": state, "code": "new"})
        self.assertRedirects(result, reverse("accounts:login-error"), fetch_redirect_response=False)
        self.assertFalse(SocialAccount.objects.filter(uid="12345").exists())

    def test_disabled_feature_has_no_destructive_requests(self):
        delete_room(user=self.owner, room_id=self.room.pk)
        self.proof()
        with (
            override_settings(KAKAO_WITHDRAWAL_ENABLED=False),
            patch("requests.sessions.Session.request") as remote,
        ):
            response = self.client.post(reverse("accounts:withdraw"), {"confirm": "on"})
            self.assertEqual(response.status_code, 400)
            remote.assert_not_called()
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())

    def test_local_transaction_failure_does_not_call_kakao_or_leave_partial_deletion(self):
        delete_room(user=self.owner, room_id=self.room.pk)
        with (
            patch("accounts.withdrawal.User.delete", side_effect=RuntimeError("DB failure")),
            self.assertRaises(RuntimeError),
        ):
            withdraw_member(user=self.owner)
        self.assertFalse(KakaoUnlinkJob.objects.exists())
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())


@override_settings(KAKAO_WITHDRAWAL_ENABLED=True, KAKAO_ADMIN_KEY="synthetic-admin-key")
class WithdrawalConcurrencyTests(SellingFixture, TransactionTestCase):
    def test_new_order_and_withdrawal_cannot_create_an_active_order_without_owner(self):
        SocialAccount.objects.create(user=self.owner, provider="kakao", uid="12345")
        delete_room(user=self.owner, room_id=self.room.pk)
        barrier = Barrier(2)

        def run(action):
            close_old_connections()
            try:
                member = User.objects.get(pk=self.owner.pk)
                barrier.wait(timeout=5)
                if action == "order":
                    try:
                        create_room(user=member, cafe_id=self.cafe.pk)
                        return "created"
                    except PermissionDenied:
                        return "withdrawn"
                try:
                    withdraw_member(user=member)
                    return "deleted"
                except ValidationError:
                    return "active-order"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, ["order", "withdraw"]))
        self.assertIn(results, [["created", "active-order"], ["withdrawn", "deleted"]])
        self.assertFalse(Room.objects.filter(status="open", organizer__isnull=True).exists())
