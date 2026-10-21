import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from cafes.models import Cafe, Menu
from cafes.services import close_for_seller, unlink_seller

from .collection import add_submission, close_collection
from .dispatch import cancel_order, preview_order, send_order
from .models import FinalOrder, Room
from .selling import decide_order, ready_time_choices, seller_context, seller_order, seller_version
from .services import create_room, expire_due_rooms
from .test_dispatch import DispatchFixture


class SellingFixture(DispatchFixture):
    def ready(self, now=None, index=2):
        return datetime.fromisoformat(ready_time_choices(now=now)[index][0])

    def accept(self, **kwargs):
        return decide_order(
            user=kwargs.pop("user", self.seller),
            room_id=self.room.pk,
            action="accept",
            ready_at=kwargs.pop("ready_at", self.ready()),
            **kwargs,
        )

    def reject(self, reason="재료 품절", **kwargs):
        return decide_order(
            user=kwargs.pop("user", self.seller),
            room_id=self.room.pk,
            action="reject",
            reason=reason,
            **kwargs,
        )

    def seller_client(self):
        client = Client()
        client.force_login(self.seller)
        return client

    def another_order(self, *, cafe=None, menu=None):
        room = create_room(user=self.owner, cafe_id=(cafe or self.cafe).pk)
        add_submission(
            room_id=room.pk,
            nickname="PRIVATE_PARTICIPANT_OTHER",
            items=[{"menu_id": (menu or self.menu).pk, "quantity": 2}],
            request_key=uuid.uuid4(),
        )
        close_collection(user=self.owner, room_id=room.pk)
        _, _, token = preview_order(user=self.owner, room_id=room.pk)
        return send_order(user=self.owner, room_id=room.pk, confirmation_token=token)


class SellingTests(SellingFixture, TestCase):
    def test_time_choices_are_next_five_minutes_and_disambiguate_midnight(self):
        seoul = ZoneInfo("Asia/Seoul")
        now = datetime(2026, 10, 21, 15, 10, tzinfo=seoul)
        first = ready_time_choices(now=now)[0]
        self.assertEqual(first, ("2026-10-21T15:15:00+09:00", "15:15"))
        after_midnight = ready_time_choices(now=now.replace(hour=23, minute=58))[0]
        self.assertEqual(after_midnight, ("2026-10-22T00:00:00+09:00", "내일 00:00"))

    def test_accept_preserves_final_order_and_shows_immutable_ready_time_to_organizer(self):
        final = self.sent()
        original = (final.items, final.sent_at, final.respond_by, final.total_amount)
        ready = self.ready()
        self.assertTrue(self.accept(ready_at=ready))
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "accepted")
        self.assertEqual(self.room.ready_at, ready)
        self.assertIsNotNone(self.room.resolved_at)
        self.assertEqual(self.room.events.filter(action="accepted").count(), 1)
        self.assertFalse(cancel_order(user=self.owner, room_id=self.room.pk))
        self.assertEqual(expire_due_rooms(now=final.respond_by + timedelta(hours=1)), 0)
        final.refresh_from_db()
        self.assertEqual(
            original, (final.items, final.sent_at, final.respond_by, final.total_amount)
        )
        body = self.client.get(reverse("orders:detail", args=[self.room.pk]))
        self.assertContains(body, "카페가 주문을 수락했어요.")
        self.assertContains(body, timezone.localtime(ready).strftime("%m.%d %H:%M") + " 완료 예정")
        self.assertNotContains(body, ">주문 취소</button>")

    def test_reject_stores_trimmed_reason_event_and_escapes_html_for_both_roles(self):
        self.sent()
        self.assertTrue(self.reject("  <b>품절</b>  "))
        self.room.refresh_from_db()
        self.assertEqual(self.room.rejection_reason, "<b>품절</b>")
        self.assertEqual(self.room.status, "rejected")
        self.assertIsNone(self.room.ready_at)
        self.assertEqual(self.room.events.filter(action="seller_rejected").count(), 1)
        for client, url in [
            (self.client, reverse("orders:detail", args=[self.room.pk])),
            (self.seller_client(), reverse("accounts:seller")),
        ]:
            response = client.get(url)
            self.assertContains(response, "&lt;b&gt;품절&lt;/b&gt;")
            self.assertNotContains(response, "<b>품절</b>")

    def test_invalid_ready_times_do_not_mutate_or_record_decision(self):
        self.sent()
        valid = self.ready()
        for invalid in [
            None,
            "not-a-time",
            valid.replace(tzinfo=None),
            timezone.now() - timedelta(hours=1),
            valid.replace(minute=valid.minute + 1),
            valid.replace(second=1),
            valid.replace(microsecond=1),
        ]:
            with self.subTest(ready_at=invalid), self.assertRaises(ValidationError):
                self.accept(ready_at=invalid)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "pending")
        self.assertIsNone(self.room.ready_at)
        self.assertFalse(self.room.events.filter(action="accepted").exists())

    def test_blank_long_and_control_character_reasons_are_rejected(self):
        self.sent()
        for reason in ["", "   ", "가" * 31, "사유\n추가", None]:
            with self.subTest(reason=reason), self.assertRaises(ValidationError):
                self.reject(reason)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "pending")
        self.assertTrue(self.reject("가" * 30))

    def test_repeated_or_conflicting_decisions_do_not_rewrite_time_reason_or_history(self):
        self.sent()
        original = self.ready()
        self.assertTrue(self.accept(ready_at=original))
        self.assertFalse(self.accept(ready_at=original + timedelta(minutes=5)))
        self.assertFalse(self.reject())
        self.room.refresh_from_db()
        self.assertEqual(self.room.ready_at, original)
        self.assertEqual(self.room.events.filter(action="accepted").count(), 1)
        self.assertFalse(self.room.events.filter(action="seller_rejected").exists())

    def test_rejected_and_cancelled_orders_cannot_be_accepted(self):
        self.sent()
        self.assertTrue(self.reject("첫 사유"))
        self.assertFalse(self.reject("다른 사유"))
        self.assertFalse(self.accept())
        self.room.refresh_from_db()
        self.assertEqual(self.room.rejection_reason, "첫 사유")
        second = self.another_order()
        self.assertTrue(cancel_order(user=self.owner, room_id=second.room_id))
        self.assertFalse(
            decide_order(
                user=self.seller, room_id=second.room_id, action="accept", ready_at=self.ready()
            )
        )

    def test_deadline_wins_over_accept_or_manual_reject_and_commits_automatic_rejection(self):
        final = self.sent()
        ready = self.ready(now=final.respond_by)
        with patch("orders.selling.timezone.now", return_value=final.respond_by):
            self.assertFalse(self.accept(ready_at=ready))
            self.assertFalse(self.reject("手動"))
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "rejected")
        self.assertEqual(self.room.rejection_reason, "5분 미응답")
        self.assertEqual(self.room.events.filter(action="timed_out").count(), 1)
        self.assertFalse(
            self.room.events.filter(action__in=["accepted", "seller_rejected"]).exists()
        )

    def test_accept_immediately_before_deadline_is_allowed(self):
        final = self.sent()
        ready = self.ready(now=final.respond_by)
        with patch(
            "orders.selling.timezone.now", return_value=final.respond_by - timedelta(microseconds=1)
        ):
            self.assertTrue(self.accept(ready_at=ready))
        self.assertEqual(expire_due_rooms(now=final.respond_by), 0)

    def test_owner_operator_and_other_seller_cannot_decide(self):
        self.sent()
        for user in [self.owner, self.other]:
            with self.assertRaises(PermissionDenied):
                self.accept(user=user)
            with self.assertRaises(PermissionDenied):
                self.reject(user=user)
        self.seller.is_active = False
        with self.assertRaises(PermissionDenied):
            self.accept()

    def test_closed_or_unlinked_cafe_blocks_decision_but_not_deadline_expiry(self):
        final = self.sent()
        close_for_seller(user=self.seller, cafe_id=self.cafe.pk)
        with self.assertRaises(ValidationError):
            self.accept()
        ready = self.ready(now=final.respond_by)
        with patch("orders.selling.timezone.now", return_value=final.respond_by):
            self.assertFalse(self.accept(ready_at=ready))
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "rejected")
        unlink_seller(actor=self.other, cafe_id=self.cafe.pk)
        with self.assertRaises(PermissionDenied):
            self.reject()

    def test_same_account_can_be_seller_and_organizer_without_participant_data(self):
        self.cafe.seller = self.owner
        self.cafe.save()
        self.sent()
        self.assertTrue(self.accept(user=self.owner))
        response = self.client.get(reverse("accounts:seller"))
        self.assertContains(response, "완료 예정")
        self.assertNotContains(response, "비공개참여자")

    def test_dashboard_and_feed_are_scoped_to_cafe_and_exclude_participant_information(self):
        final = self.sent()
        other_cafe = Cafe.objects.create(name="비공개다른카페", seller=self.other, is_open=True)
        other_menu = Menu.objects.create(cafe=other_cafe, name="비공개다른메뉴", price=999)
        hidden = self.another_order(cafe=other_cafe, menu=other_menu)
        client = self.seller_client()
        response = client.get(reverse("accounts:seller"))
        self.assertContains(response, self.owner.nickname)
        for secret in [
            "비공개참여자",
            "PRIVATE_PARTICIPANT_OTHER",
            "비공개다른카페",
            "비공개다른메뉴",
            self.room.token,
            str(self.owner.account_code),
            self.owner.username,
        ]:
            self.assertNotContains(response, secret)
        data = seller_order(final)
        self.assertEqual(
            set(data),
            {
                "id",
                "organizer_nickname",
                "sent_at",
                "respond_by",
                "total_quantity",
                "total_amount",
                "status",
                "status_label",
                "resolved_at",
                "ready_at",
                "rejection_reason",
                "items",
            },
        )
        self.assertEqual(set(data["items"][0]), {"name", "quantity", "amount", "options"})
        self.assertEqual(data["items"][0]["options"][0]["group"], self.temp.name)
        self.assertEqual(set(data["items"][0]["options"][0]), {"group", "name", "quantity"})
        feed = client.get(reverse("orders:seller-feed"))
        self.assertEqual(set(feed.json()), {"version", "pending"})
        self.assertEqual(feed.json()["pending"], [self.room.pk])
        self.assertNotIn(hidden.room_id, feed.json()["pending"])
        self.assertIn("no-store", feed.headers["Cache-Control"])
        for route in ["seller-accept", "seller-reject"]:
            response = client.post(
                reverse("orders:" + route, args=[hidden.room_id]),
                {"ready_at": self.ready().isoformat(), "reason": "불가"},
            )
            self.assertEqual(response.status_code, 404)

    def test_feed_expiry_clears_pending_and_changes_version_once(self):
        final = self.sent()
        before = seller_version(self.cafe)
        client = self.seller_client()
        with patch("orders.services.timezone.now", return_value=final.respond_by):
            response = client.get(reverse("orders:seller-feed"))
        self.assertEqual(response.json()["pending"], [])
        self.assertNotEqual(before, response.json()["version"])
        self.assertEqual(self.room.events.filter(action="timed_out").count(), 1)
        again = client.get(reverse("orders:seller-feed"))
        self.assertEqual(again.json()["version"], response.json()["version"])

    def test_only_sent_orders_appear_and_all_pending_orders_are_kept(self):
        self.add()
        self.assertFalse(seller_context(user=self.seller)["pending_orders"])
        self.prepare()
        self.send()
        for _ in range(21):
            self.another_order()
        context = seller_context(user=self.seller)
        self.assertEqual(len(context["pending_orders"]), 22)
        self.assertFalse(context["accepted_orders"])

    def test_decision_post_csrf_and_role_guards(self):
        self.sent()
        for route in ["seller-accept", "seller-reject"]:
            url = reverse("orders:" + route, args=[self.room.pk])
            client = self.seller_client()
            self.assertEqual(client.get(url).status_code, 405)
            csrf = Client(enforce_csrf_checks=True)
            csrf.force_login(self.seller)
            self.assertEqual(csrf.post(url, {"reason": "품절"}).status_code, 403)
            self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(Client().get(reverse("orders:seller-feed")).status_code, 302)
        self.assertEqual(self.client.get(reverse("orders:seller-feed")).status_code, 403)

    def test_accepted_pagination_keeps_recent_orders_visible_and_older_orders_accessible(self):
        ids = []
        for _ in range(21):
            final = self.another_order()
            decide_order(
                user=self.seller, room_id=final.room_id, action="accept", ready_at=self.ready()
            )
            ids.append(final.room_id)
        first = seller_context(user=self.seller)["accepted_orders"]
        second = seller_context(user=self.seller, accepted_page=2)["accepted_orders"]
        self.assertEqual(first.object_list[0]["id"], ids[-1])
        self.assertEqual(len(first), 20)
        self.assertEqual([order["id"] for order in second], ids[:1])
        self.assertEqual(
            {order["id"] for order in first} | {order["id"] for order in second}, set(ids)
        )

    def test_rejected_history_pagination_preserves_reasons(self):
        ids = []
        for index in range(21):
            final = self.another_order()
            decide_order(
                user=self.seller, room_id=final.room_id, action="reject", reason=f"사유 {index}"
            )
            ids.append(final.room_id)
        second = seller_context(user=self.seller, page=2)["order_history"]
        self.assertEqual(len(second), 1)
        self.assertEqual(second.object_list[0]["id"], ids[0])
        self.assertEqual(second.object_list[0]["rejection_reason"], "사유 0")

    def test_invalid_form_stays_on_own_order_and_valid_post_updates_both_roles(self):
        self.sent()
        client = self.seller_client()
        url = reverse("orders:seller-accept", args=[self.room.pk])
        response = client.post(url, {"ready_at": "garbage"})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context["pending_orders"][0]["accept_form"].errors)
        reject_url = reverse("orders:seller-reject", args=[self.room.pk])
        self.assertEqual(client.post(reject_url, {"reason": " "}).status_code, 400)
        self.assertRedirects(
            client.post(url, {"ready_at": self.ready().isoformat()}), reverse("accounts:seller")
        )
        self.assertContains(
            self.client.get(reverse("orders:detail", args=[self.room.pk])), "완료 예정"
        )
        self.assertRedirects(
            client.post(url, {"ready_at": self.ready(index=4).isoformat()}),
            reverse("accounts:seller"),
        )
        self.assertEqual(self.room.events.filter(action="accepted").count(), 1)

    def test_pending_cancellation_and_manual_rejection_stay_in_seller_history(self):
        self.sent()
        cancel_order(user=self.owner, room_id=self.room.pk)
        other = self.another_order()
        decide_order(
            user=self.seller, room_id=other.room_id, action="reject", reason="오늘 재료 품절"
        )
        context = seller_context(user=self.seller)
        self.assertFalse(context["pending_orders"])
        self.assertEqual(
            {order["status"] for order in context["order_history"]}, {"cancelled", "rejected"}
        )
        self.assertEqual(len(context["order_history"]), 2)

    def test_ready_time_constraints_and_freeze_apply_to_direct_database_writes(self):
        self.sent()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Room.objects.filter(pk=self.room.pk).update(
                status="accepted", resolved_at=timezone.now()
            )
        valid = self.ready()
        for invalid in [
            valid.replace(second=1),
            valid.replace(microsecond=1),
            valid + timedelta(minutes=1),
            valid - timedelta(days=1),
        ]:
            with (
                self.subTest(time=invalid),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                Room.objects.filter(pk=self.room.pk).update(
                    status="accepted", resolved_at=timezone.now(), ready_at=invalid
                )
        self.assertTrue(self.accept(ready_at=valid))
        for changes in [
            {"ready_at": None},
            {"ready_at": valid + timedelta(minutes=5)},
            {"resolved_at": timezone.now()},
            {"status": "pending"},
            {"rejection_reason": "고침"},
        ]:
            with (
                self.subTest(changes=changes),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                Room.objects.filter(pk=self.room.pk).update(**changes)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinalOrder.objects.filter(room=self.room).update(total_amount=1)

    def test_decision_failure_rolls_back_state_ready_time_and_event(self):
        final = self.sent()
        with (
            patch(
                "orders.selling.RoomEvent.objects.create", side_effect=RuntimeError("interrupted")
            ),
            self.assertRaises(RuntimeError),
        ):
            self.accept()
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "pending")
        self.assertIsNone(self.room.ready_at)
        self.assertIsNone(self.room.resolved_at)
        final.refresh_from_db()
        self.assertEqual(final.total_amount, 3000)


class SellingConcurrencyTests(SellingFixture, TransactionTestCase):
    def race(self, functions):
        from .test_dispatch import DispatchConcurrencyTests

        return DispatchConcurrencyTests.race(self, functions)

    def test_accept_and_cancel_have_one_winner(self):
        self.sent()
        results = self.race(
            [self.accept, lambda: cancel_order(user=self.owner, room_id=self.room.pk)]
        )
        self.assertEqual(sum(results), 1)
        self.room.refresh_from_db()
        self.assertIn(self.room.status, ["accepted", "cancelled"])
        self.assertEqual(self.room.events.filter(action__in=["accepted", "cancelled"]).count(), 1)

    def test_accept_and_reject_have_one_winner(self):
        self.sent()
        self.assertEqual(sum(self.race([self.accept, self.reject])), 1)
        self.room.refresh_from_db()
        self.assertIn(self.room.status, ["accepted", "rejected"])
        self.assertEqual(
            self.room.events.filter(action__in=["accepted", "seller_rejected"]).count(), 1
        )

    def test_two_acceptances_do_not_change_first_ready_time(self):
        self.sent()
        times = [self.ready(index=2), self.ready(index=3)]
        results = self.race(
            [lambda: self.accept(ready_at=times[0]), lambda: self.accept(ready_at=times[1])]
        )
        self.assertEqual(sum(results), 1)
        self.room.refresh_from_db()
        self.assertEqual(self.room.ready_at, times[results.index(True)])
        self.assertEqual(self.room.events.filter(action="accepted").count(), 1)

    def test_exact_deadline_accept_reject_and_worker_leave_one_timeout(self):
        final = self.sent()
        ready = self.ready(now=final.respond_by)
        with patch("orders.selling.timezone.now", return_value=final.respond_by):
            results = self.race(
                [lambda: self.accept(ready_at=ready), self.reject, expire_due_rooms]
            )
        self.assertFalse(results[0])
        self.assertFalse(results[1])
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "rejected")
        self.assertEqual(self.room.rejection_reason, "5분 미응답")
        self.assertEqual(self.room.events.filter(action="timed_out").count(), 1)
        self.assertFalse(
            self.room.events.filter(action__in=["accepted", "seller_rejected"]).exists()
        )

    def test_logout_and_accept_are_serialized(self):
        self.sent()

        def attempt():
            try:
                return self.accept()
            except ValidationError:
                return False

        results = self.race(
            [attempt, lambda: close_for_seller(user=self.seller, cafe_id=self.cafe.pk)]
        )
        self.room.refresh_from_db()
        self.cafe.refresh_from_db()
        self.assertFalse(self.cafe.is_open)
        self.assertEqual(self.room.status, "accepted" if results[0] else "pending")

    def test_unlink_and_accept_are_serialized(self):
        self.sent()

        def attempt():
            try:
                return self.accept()
            except PermissionDenied:
                return False

        results = self.race(
            [attempt, lambda: unlink_seller(actor=self.other, cafe_id=self.cafe.pk)]
        )
        self.room.refresh_from_db()
        self.cafe.refresh_from_db()
        self.assertIsNone(self.cafe.seller_id)
        self.assertEqual(self.room.status, "accepted" if results[0] else "pending")
