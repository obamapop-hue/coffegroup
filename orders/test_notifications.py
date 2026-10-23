from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from .models import OrderNotification
from .selling import complete_order
from .services import expire_due_rooms
from .test_selling import SellingFixture


class OrderNotificationTests(SellingFixture, TestCase):
    def notices(self):
        return self.client.get(reverse("orders:notifications")).json()["notifications"]

    def test_accept_and_completion_have_independent_unread_notices(self):
        self.sent()
        self.accept()
        first = self.notices()[0]
        self.assertIn("주문을 수락했어요", first["message"])
        self.assertIn("완료 예정", first["message"])
        self.client.post(first["read_url"])
        self.assertFalse(self.accept())
        complete_order(user=self.seller, room_id=self.room.pk)
        self.assertEqual(len(self.notices()), 1)
        self.assertIn("모두 준비됐어요", self.notices()[0]["message"])
        self.assertEqual(
            set(self.room.notifications.values_list("kind", flat=True)), {"accepted", "completed"}
        )

    def test_rejection_reason_is_in_organizer_notice_but_not_seller_feed(self):
        self.sent()
        self.reject("원두 품절")
        self.assertIn("사유: 원두 품절", self.notices()[0]["message"])
        feed = self.seller_client().get(reverse("orders:seller-feed"))
        self.assertNotIn("원두 품절", feed.content.decode())
        self.assertEqual(feed.json()["pending"], [])

    def test_worker_timeout_creates_one_notice_even_without_an_organizer_visit(self):
        final = self.sent()
        self.assertEqual(expire_due_rooms(now=final.respond_by), 1)
        self.assertEqual(expire_due_rooms(now=final.respond_by + timedelta(seconds=5)), 0)
        notice = OrderNotification.objects.get(room=self.room, kind="rejected")
        self.assertEqual(notice.recipient, self.owner)
        self.assertIn("5분 미응답", self.notices()[0]["message"])

    def test_unsent_expiry_persists_notice_for_later_login(self):
        self.assertEqual(expire_due_rooms(now=self.room.expires_at), 1)
        self.assertEqual(self.room.notifications.filter(kind="expired").count(), 1)
        self.client.logout()
        self.client.force_login(self.owner)
        self.assertIn("30분 동안 전송되지 않아 폐기", self.notices()[0]["message"])

    def test_failure_to_store_notice_rolls_back_seller_decision(self):
        self.sent()
        with (
            patch(
                "orders.notifications.OrderNotification.objects.create", side_effect=RuntimeError
            ),
            self.assertRaises(RuntimeError),
        ):
            self.accept()
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "pending")
        self.assertFalse(self.room.events.filter(action="accepted").exists())

    def test_feed_reports_business_closed_without_changing_business_state(self):
        self.sent()
        self.cafe.is_open = False
        self.cafe.save(update_fields=["is_open"])
        feed = self.seller_client().get(reverse("orders:seller-feed")).json()
        self.assertFalse(feed["is_open"])
        self.assertEqual(feed["pending"], [self.room.pk])
        self.cafe.refresh_from_db()
        self.assertFalse(self.cafe.is_open)
