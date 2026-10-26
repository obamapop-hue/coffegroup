import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from orders.collection import add_submission, close_collection, edit_submission
from orders.dispatch import send_order
from orders.models import FinalOrder, Submission
from orders.selling import complete_order
from orders.services import create_room, expire_due_rooms
from orders.test_selling import SellingFixture

from .models import OrderRestrictionEvent, User
from .order_restrictions import set_ordering_suspension
from .ordering import OrderingSuspended


class OrderRestrictionTests(SellingFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = Client()
        self.admin.force_login(self.other)
        self.action_url = reverse(
            "accounts:member-ordering", args=[self.owner.membership_record.pk]
        )

    def restrict(self, suspended=True, member=None):
        return set_ordering_suspension(
            actor=self.other,
            membership_id=(member or self.owner).membership_record.pk,
            suspended=suspended,
        )

    def test_operator_suspend_restore_is_idempotent_audited_and_keeps_login(self):
        for action, flag in [
            ("suspend", True),
            ("suspend", True),
            ("restore", False),
            ("restore", False),
        ]:
            response = self.admin.post(
                self.action_url,
                {
                    "action": action,
                    "q": "비공개총무",
                    "status": "current",
                    "page": "1",
                    "next": "https://evil.example",
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse("accounts:members") + "?"))
            self.assertNotIn("evil.example", response.url)
            member = User.objects.get(pk=self.owner.pk)
            self.assertTrue(member.is_active)
            self.assertEqual(member.ordering_suspended, flag)
            self.assertIn(SESSION_KEY, self.client.session)
        self.assertEqual(
            list(OrderRestrictionEvent.objects.order_by("pk").values_list("action", flat=True)),
            ["suspended", "restored"],
        )
        self.assertEqual(OrderRestrictionEvent.objects.first().actor, self.other)
        self.assertEqual(
            OrderRestrictionEvent.objects.first().actor_nickname, self.other.display_name
        )

    def test_only_operator_post_with_csrf_can_change_and_inactive_or_missing_target_is_rejected(
        self,
    ):
        self.assertEqual(Client().post(self.action_url, {"action": "suspend"}).status_code, 302)
        self.assertEqual(self.client.post(self.action_url, {"action": "suspend"}).status_code, 403)
        self.assertEqual(self.admin.get(self.action_url).status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.other)
        self.assertEqual(csrf.post(self.action_url, {"action": "suspend"}).status_code, 403)
        self.admin.post(self.action_url, {"action": "invalid"})
        self.assertFalse(OrderRestrictionEvent.objects.exists())
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        with self.assertRaises(ValidationError):
            self.restrict()
        with self.assertRaises(ValidationError):
            set_ordering_suspension(actor=self.other, membership_id=999999, suspended=True)
        self.assertFalse(User.objects.get(pk=self.owner.pk).ordering_suspended)

    def test_revoked_operator_and_failed_audit_cannot_leave_partial_suspension(self):
        with (
            patch(
                "accounts.order_restrictions.OrderRestrictionEvent.objects.create",
                side_effect=RuntimeError("fail"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.restrict()
        self.assertFalse(User.objects.get(pk=self.owner.pk).ordering_suspended)
        User.objects.filter(pk=self.other.pk).update(is_operator=False)
        with self.assertRaises(PermissionDenied):
            self.restrict()
        self.assertFalse(OrderRestrictionEvent.objects.exists())

    def test_operator_can_suspend_own_ordering_and_restore_without_losing_management(self):
        url = reverse("accounts:member-ordering", args=[self.other.membership_record.pk])
        self.admin.post(url, {"action": "suspend"})
        self.assertTrue(User.objects.get(pk=self.other.pk).ordering_suspended)
        self.assertEqual(self.admin.get(reverse("accounts:members")).status_code, 200)
        self.assertEqual(self.admin.get(reverse("accounts:operator")).status_code, 200)
        self.admin.post(url, {"action": "restore"})
        self.assertFalse(User.objects.get(pk=self.other.pk).ordering_suspended)

    def test_existing_sessions_and_direct_services_cannot_place_edit_close_or_send(self):
        entry = self.add()
        self.restrict()
        calls = [
            lambda: create_room(user=self.owner, cafe_id=self.cafe.pk),
            lambda: add_submission(
                user=self.owner,
                room_id=self.room.pk,
                nickname="총무",
                items=self.items(),
                request_key=uuid.uuid4(),
            ),
            lambda: edit_submission(
                user=self.owner,
                room_id=self.room.pk,
                submission_id=entry.pk,
                version=entry.version,
                nickname="변경",
                items=self.items(2),
            ),
            lambda: close_collection(user=self.owner, room_id=self.room.pk),
            lambda: send_order(
                user=self.owner, room_id=self.room.pk, confirmation_token="old-token"
            ),
        ]
        for operation in calls:
            with self.assertRaises(OrderingSuspended):
                operation()
        self.assertEqual(Submission.objects.count(), 1)
        entry.refresh_from_db()
        self.assertEqual(entry.version, 1)
        self.assertFalse(FinalOrder.objects.exists())
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "open")
        for url in [
            reverse("orders:create", args=[self.cafe.pk]),
            reverse("orders:order-add", args=[self.room.pk]),
            reverse("orders:order-edit", args=[self.room.pk, entry.pk]),
            reverse("orders:order-delete", args=[self.room.pk, entry.pk]),
            reverse("orders:close", args=[self.room.pk]),
            reverse("orders:send", args=[self.room.pk]),
        ]:
            self.assertContains(
                self.client.post(url), "주문 이용이 정지되어 있어요.", status_code=403
            )

    def test_closed_order_stays_readable_without_edit_send_or_share_controls_and_can_be_deleted(
        self,
    ):
        self.prepare()
        self.restrict()
        response = self.client.get(reverse("orders:detail", args=[self.room.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_edit"])
        self.assertFalse(response.context["can_send"])
        self.assertNotContains(response, "data-final-confirm")
        self.assertNotContains(response, "data-copy-share-link")
        self.assertContains(response, "주문서 삭제")
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)
        self.assertEqual(self.client.get(reverse("support:inbox")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:withdraw")).status_code, 200)
        self.assertEqual(
            self.client.post(reverse("orders:delete", args=[self.room.pk])).status_code, 302
        )
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "deleted")

    def test_shared_intake_stops_on_suspension_and_restore_preserves_room(
        self,
    ):
        original = (self.room.menu_snapshot, self.room.expires_at)
        data = self.post_data()
        self.restrict()
        guest = Client()
        self.assertContains(guest.get(self.url), "참여할 수 없는 주문서입니다.", status_code=410)
        self.assertEqual(guest.post(self.url, data).status_code, 410)
        self.assertIsNone(
            add_submission(
                room_id=self.room.pk, nickname="익명", items=self.items(), request_key=uuid.uuid4()
            )
        )
        self.restrict(False)
        self.assertEqual(guest.get(self.url).status_code, 200)
        self.assertEqual(guest.post(self.url, data).status_code, 302)
        self.room.refresh_from_db()
        self.assertEqual(original, (self.room.menu_snapshot, self.room.expires_at))
        self.restrict()
        expire_due_rooms(now=self.room.expires_at)
        self.restrict(False)
        self.assertEqual(guest.get(self.url).status_code, 410)

    def test_logged_in_suspended_participant_is_blocked_but_unidentified_guests_remain_supported(
        self,
    ):
        self.restrict(member=self.seller)
        participant = Client()
        participant.force_login(self.seller)
        self.assertEqual(participant.get(self.url).status_code, 403)
        self.assertEqual(participant.post(self.url, self.post_data()).status_code, 403)
        with self.assertRaises(OrderingSuspended):
            add_submission(
                room_id=self.room.pk,
                nickname="회원",
                items=self.items(),
                request_key=uuid.uuid4(),
                participant_user=self.seller,
            )
        self.assertEqual(Client().post(self.url, self.post_data()).status_code, 302)
        self.assertEqual(Submission.objects.count(), 1)
        self.assertIsNone(Submission.objects.get().revisions.first().actor_id)

    def test_prepared_send_token_is_blocked_after_suspend_and_works_again_after_restore(self):
        self.prepare()
        self.restrict()
        with self.assertRaises(OrderingSuspended):
            self.send()
        self.assertFalse(FinalOrder.objects.exists())
        self.restrict(False)
        self.assertIsNotNone(self.send())

    def test_seller_fulfillment_notifications_and_pre_acceptance_cancellation_continue(self):
        final = self.sent()
        original = (final.items, final.total_amount, final.organizer_nickname)
        self.restrict()
        self.restrict(member=self.seller)
        self.assertTrue(self.accept())
        self.assertTrue(complete_order(user=self.seller, room_id=self.room.pk))
        final.refresh_from_db()
        self.assertEqual(original, (final.items, final.total_amount, final.organizer_nickname))
        self.assertEqual(self.client.get(reverse("orders:notifications")).status_code, 200)
        self.cafe.refresh_from_db()
        self.assertTrue(self.cafe.is_open)
        self.restrict(False)
        self.room = create_room(user=self.owner, cafe_id=self.cafe.pk)
        self.sent()
        self.restrict()
        self.assertEqual(
            self.client.post(reverse("orders:cancel", args=[self.room.pk])).status_code, 302
        )
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "cancelled")

    def test_member_list_exposes_controls_status_filter_and_escaped_audit(self):
        self.other.nickname = "<script>actor</script>"
        self.other.save(update_fields=["nickname"])
        self.restrict()
        response = self.admin.get(reverse("accounts:members"), {"status": "suspended"})
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertContains(response, "정지 해제")
        self.assertContains(response, "&lt;script&gt;actor&lt;/script&gt;")
        self.assertNotContains(response, "<script>actor</script>")


class RestrictionRaceTests(SellingFixture, TransactionTestCase):
    def test_send_and_suspension_serialize_and_never_send_after_suspension_commits(self):
        self.prepare()
        barrier = Barrier(2)

        def race(action):
            close_old_connections()
            try:
                owner = User.objects.get(pk=self.owner.pk)
                operator = User.objects.get(pk=self.other.pk)
                record_id = owner.membership_record.pk
                barrier.wait(timeout=5)
                if action == "suspend":
                    set_ordering_suspension(actor=operator, membership_id=record_id, suspended=True)
                    return "suspended"
                try:
                    send_order(user=owner, room_id=self.room.pk, confirmation_token=self.token)
                    return "sent"
                except OrderingSuspended:
                    return "blocked"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(race, ["send", "suspend"]))
        self.assertIn(result, [["sent", "suspended"], ["blocked", "suspended"]])
        self.assertEqual(FinalOrder.objects.count(), 1 if result[0] == "sent" else 0)
        self.assertTrue(User.objects.get(pk=self.owner.pk).ordering_suspended)
        self.assertEqual(OrderRestrictionEvent.objects.count(), 1)
