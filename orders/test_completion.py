from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .dispatch import cancel_order
from .models import FinalOrder, OrderNotification, Room
from .selling import complete_order, seller_context, seller_version
from .test_selling import SellingFixture


class CompletionTests(SellingFixture, TestCase):
    def complete(self, user=None):
        return complete_order(user=user or self.seller, room_id=self.room.pk)

    def accepted(self):
        final = self.sent()
        self.accept()
        return final

    def test_complete_preserves_order_and_ready_time_and_creates_one_durable_notice(self):
        final = self.accepted()
        self.room.refresh_from_db()
        original = (self.room.ready_at, self.room.resolved_at, final.items, final.total_amount)
        before = seller_version(self.cafe)
        self.assertTrue(self.complete())
        self.room.refresh_from_db()
        final.refresh_from_db()
        self.assertEqual(self.room.status, "completed")
        self.assertIsNotNone(self.room.completed_at)
        self.assertEqual(
            original, (self.room.ready_at, self.room.resolved_at, final.items, final.total_amount)
        )
        self.assertNotEqual(before, seller_version(self.cafe))
        self.assertFalse(self.complete())
        self.assertEqual(self.room.events.filter(action="completed").count(), 1)
        notice = OrderNotification.objects.get(room=self.room, kind="completed")
        self.assertEqual(notice.recipient, self.owner)
        self.assertIsNone(notice.read_at)
        self.assertFalse(cancel_order(user=self.owner, room_id=self.room.pk))
        self.assertFalse(seller_context(user=self.seller)["accepted_orders"])
        self.assertEqual(
            seller_context(user=self.seller)["order_history"][0]["status"], "completed"
        )
        self.assertContains(
            self.client.get(reverse("orders:detail", args=[self.room.pk])),
            "메뉴가 모두 준비됐어요.",
        )

    def test_only_accepted_orders_can_complete_and_unauthorized_sellers_cannot(self):
        self.assertFalse(self.complete())
        self.sent()
        self.assertFalse(self.complete())
        self.assertFalse(OrderNotification.objects.exists())
        self.accept()
        with self.assertRaises(PermissionDenied):
            self.complete(user=self.other)
        self.assertTrue(self.complete())
        self.assertFalse(self.reject())

    def test_completed_card_moves_to_history_and_accepted_has_post_button(self):
        self.accepted()
        url = reverse("orders:seller-complete", args=[self.room.pk])
        client = self.seller_client()
        before = client.get(reverse("accounts:seller"))
        self.assertContains(before, f'action="{url}"')
        self.assertContains(before, ">메뉴 완료</button>")
        self.assertRedirects(client.post(url), reverse("accounts:seller"))
        after = client.get(reverse("accounts:seller"))
        self.assertNotContains(after, f'action="{url}"')
        self.assertContains(after, "완료·거절·취소 내역")
        self.assertNotContains(after, "비공개참여자")

    def test_complete_requires_post_csrf_and_matching_cafe(self):
        self.accepted()
        url = reverse("orders:seller-complete", args=[self.room.pk])
        self.assertEqual(self.seller_client().get(url).status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.seller)
        self.assertEqual(csrf.post(url).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 403)
        from cafes.models import Cafe

        Cafe.objects.create(name="다른 카페", seller=self.other)
        other = Client()
        other.force_login(self.other)
        self.assertEqual(other.post(url).status_code, 404)

    def test_unread_notification_survives_relogin_and_is_scoped_and_explicitly_acknowledged(self):
        self.accepted()
        self.complete()
        feed = reverse("orders:notifications")
        self.assertEqual(self.seller_client().get(feed).json()["notifications"], [])
        self.client.logout()
        self.client.force_login(self.owner)
        response = self.client.get(feed)
        self.assertIn("no-store", response.headers["Cache-Control"])
        notice = response.json()["notifications"][0]
        self.assertIn("메뉴가 모두 준비됐어요.", notice["message"])
        self.assertEqual(notice["order_url"], reverse("orders:detail", args=[self.room.pk]))
        # Merely opening the order must not consume the unread notice.
        self.client.get(notice["order_url"])
        self.assertEqual(len(self.client.get(feed).json()["notifications"]), 2)
        self.assertEqual(self.seller_client().post(notice["read_url"]).status_code, 404)
        self.assertEqual(self.client.get(notice["read_url"]).status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(notice["read_url"]).status_code, 403)
        self.assertEqual(self.client.post(notice["read_url"]).status_code, 200)
        self.assertEqual(self.client.post(notice["read_url"]).status_code, 200)
        self.assertEqual(len(self.client.get(feed).json()["notifications"]), 1)
        self.assertIsNotNone(
            OrderNotification.objects.get(room=self.room, kind="completed").read_at
        )
        self.assertEqual(self.room.events.filter(action="completed").count(), 1)

    def test_notification_failure_rolls_back_completion_and_history(self):
        self.accepted()
        with (
            patch("orders.selling.OrderNotification.objects.create", side_effect=RuntimeError),
            self.assertRaises(RuntimeError),
        ):
            self.complete()
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "accepted")
        self.assertIsNone(self.room.completed_at)
        self.assertFalse(self.room.events.filter(action="completed").exists())

    def test_database_blocks_skip_reopen_timestamp_and_order_content_changes(self):
        self.sent()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Room.objects.filter(pk=self.room.pk).update(
                status="completed", completed_at=timezone.now()
            )
        self.accept()
        self.complete()
        for changes in [
            {"status": "accepted", "completed_at": None},
            {"completed_at": timezone.now() + timedelta(minutes=1)},
            {"ready_at": self.ready(index=4)},
            {"resolved_at": timezone.now()},
        ]:
            with (
                self.subTest(changes=changes),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                Room.objects.filter(pk=self.room.pk).update(**changes)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinalOrder.objects.filter(room=self.room).update(total_amount=1)


class CompletionConcurrencyTests(SellingFixture, TransactionTestCase):
    def test_two_completions_create_one_event_and_notification(self):
        from .test_dispatch import DispatchConcurrencyTests

        self.sent()
        self.accept()

        def action():
            return complete_order(user=self.seller, room_id=self.room.pk)

        results = DispatchConcurrencyTests.race(self, [action, action])
        self.assertEqual(sum(results), 1)
        self.assertEqual(
            OrderNotification.objects.filter(room=self.room, kind="completed").count(), 1
        )
        self.assertEqual(self.room.events.filter(action="completed").count(), 1)
