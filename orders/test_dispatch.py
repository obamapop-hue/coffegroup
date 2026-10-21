import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connections, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from cafes.models import Cafe
from cafes.services import close_for_seller

from .collection import add_submission, close_collection, edit_submission
from .dispatch import ConfirmationChanged, cancel_order, preview_order, send_order
from .models import FinalOrder, Room, RoomEvent, Submission, SubmissionRevision
from .services import create_room, delete_room, expire_due_rooms
from .test_collection import CollectionFixture


class DispatchFixture(CollectionFixture):
    def prepare(self):
        self.entry = self.add()
        close_collection(user=self.owner, room_id=self.room.pk)
        _, self.preview, self.token = preview_order(user=self.owner, room_id=self.room.pk)

    def send(self):
        return send_order(user=self.owner, room_id=self.room.pk, confirmation_token=self.token)

    def sent(self):
        self.prepare()
        return self.send()


class DispatchTests(DispatchFixture, TestCase):
    def test_closed_only_empty_and_open_orders_cannot_send(self):
        self.add()
        with self.assertRaises(ValidationError):
            preview_order(user=self.owner, room_id=self.room.pk)
        close_collection(user=self.owner, room_id=self.room.pk)
        self.assertIsNotNone(preview_order(user=self.owner, room_id=self.room.pk))
        empty = create_room(user=self.owner, cafe_id=self.cafe.pk)
        close_collection(user=self.owner, room_id=empty.pk)
        with self.assertRaises(ValidationError):
            preview_order(user=self.owner, room_id=empty.pk)
        self.assertFalse(FinalOrder.objects.exists())

    def test_aggregate_exact_menu_options_and_never_include_participant_identifiers(self):
        add_submission(
            room_id=self.room.pk,
            nickname="숨겨야할첫이름",
            items=self.items(2),
            request_key=uuid.uuid4(),
        )
        add_submission(
            room_id=self.room.pk,
            nickname="숨겨야할둘째이름",
            items=self.items(3),
            request_key=uuid.uuid4(),
        )
        items = self.items()
        hot = self.temp.choices.get(name="핫")
        items[0]["selections"] = {str(self.temp.pk): [hot.pk]}
        add_submission(
            room_id=self.room.pk, nickname="숨겨야할셋째이름", items=items, request_key=uuid.uuid4()
        )
        close_collection(user=self.owner, room_id=self.room.pk)
        _, payload, token = preview_order(user=self.owner, room_id=self.room.pk)
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["items"][0]["quantity"], 5)
        self.assertEqual(payload["total_quantity"], 6)
        self.assertEqual(payload["total_amount"], 18000)
        final = send_order(user=self.owner, room_id=self.room.pk, confirmation_token=token)
        self.assertEqual(final.organizer_nickname, self.owner.nickname)
        body = json.dumps(payload, ensure_ascii=False)
        for secret in [
            "숨겨야할첫이름",
            "숨겨야할둘째이름",
            "숨겨야할셋째이름",
            str(self.owner.account_code),
            self.owner.username,
            "request_key",
            "submission_id",
            "phone",
            "email",
        ]:
            self.assertNotIn(secret, body)
        self.assertEqual(
            set(payload), {"organizer_nickname", "items", "total_quantity", "total_amount"}
        )

    def test_different_option_counts_stay_separate_and_correctly_priced(self):
        self.shots.mode = "multiple"
        self.shots.save()
        self.room = create_room(user=self.owner, cafe_id=self.cafe.pk)
        for count in [2, 3]:
            add_submission(
                room_id=self.room.pk,
                nickname="숨김",
                items=[
                    {
                        "menu_id": self.menu.pk,
                        "quantity": 2,
                        "selections": {str(self.shots.pk): [self.shot.pk]},
                        "option_quantities": {str(self.shots.pk): {str(self.shot.pk): count}},
                    }
                ],
                request_key=uuid.uuid4(),
            )
        close_collection(user=self.owner, room_id=self.room.pk)
        _, payload, token = preview_order(user=self.owner, room_id=self.room.pk)
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["total_amount"], 17000)
        final = send_order(user=self.owner, room_id=self.room.pk, confirmation_token=token)
        self.assertEqual(final.total_amount, 17000)

    def test_closed_detail_has_privacy_notice_and_sends_without_confirmation_page(self):
        self.prepare()
        response = self.client.get(reverse("orders:detail", args=[self.room.pk]))
        self.assertContains(
            response, "총무님의 전화번호와 참여자의 이름(닉네임) 등 개인정보는 전달되지 않습니다."
        )
        self.assertContains(response, self.owner.nickname)
        self.assertContains(response, "비공개참여자")
        self.assertNotContains(response, 'href="' + reverse("orders:confirm", args=[self.room.pk]))
        self.assertLess(
            response.content.index(b'class="final-privacy"'),
            response.content.index(">주문서 전송</button>".encode()),
        )
        self.assertFalse(FinalOrder.objects.exists())
        token = response.context["confirmation_token"]
        self.assertTrue(token)
        response = self.client.post(
            reverse("orders:send", args=[self.room.pk]), {"confirmation_token": token}
        )
        self.assertRedirects(response, reverse("orders:detail", args=[self.room.pk]))
        final = FinalOrder.objects.get()
        self.assertEqual(final.total_amount, 3000)
        self.assertNotIn("비공개참여자", json.dumps(final.items, ensure_ascii=False))

    def test_close_returns_to_same_order_with_send_button_and_old_confirm_redirects(self):
        self.add()
        url = reverse("orders:detail", args=[self.room.pk])
        response = self.client.get(url)
        self.assertFalse(response.context["confirmation_token"])
        self.assertNotContains(response, ">주문서 전송</button>")
        response = self.client.post(reverse("orders:close", args=[self.room.pk]), follow=True)
        self.assertEqual(response.redirect_chain, [(url, 302)])
        self.assertContains(response, ">주문서 전송</button>")
        self.assertTrue(response.context["confirmation_token"])
        self.assertRedirects(self.client.get(reverse("orders:confirm", args=[self.room.pk])), url)

    def test_empty_expired_and_read_only_orders_have_no_send_form(self):
        close_collection(user=self.owner, room_id=self.room.pk)
        url = reverse("orders:detail", args=[self.room.pk])
        self.assertFalse(self.client.get(url).context["confirmation_token"])
        self.add(user=self.owner)
        self.client.force_login(self.other)
        self.assertFalse(self.client.get(url).context["confirmation_token"])
        self.client.force_login(self.owner)
        with patch("orders.services.timezone.now", return_value=self.room.expires_at):
            response = self.client.get(url)
        self.assertFalse(response.context["confirmation_token"])
        self.assertNotContains(response, ">주문서 전송</button>")

    def test_tampered_missing_expired_or_other_room_confirmation_cannot_send(self):
        self.prepare()
        other = create_room(user=self.owner, cafe_id=self.cafe.pk)
        self.add()
        for token in ["", "bad", self.token + "x"]:
            with self.subTest(token=token), self.assertRaises(ValidationError):
                send_order(user=self.owner, room_id=self.room.pk, confirmation_token=token)
        with self.assertRaises(ValidationError):
            send_order(user=self.owner, room_id=other.pk, confirmation_token=self.token)
        with (
            patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 601),
            self.assertRaises(ValidationError),
        ):
            self.send()
        self.assertFalse(FinalOrder.objects.exists())

    def test_changed_order_after_preview_requires_new_confirmation(self):
        self.prepare()
        detail_url = reverse("orders:detail", args=[self.room.pk])
        self.token = self.client.get(detail_url).context["confirmation_token"]
        edit_submission(
            user=self.owner,
            room_id=self.room.pk,
            submission_id=self.entry.pk,
            version=1,
            nickname="수정",
            items=self.items(2),
        )
        response = self.client.post(
            reverse("orders:send", args=[self.room.pk]), {"confirmation_token": self.token}
        )
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, 'href="' + detail_url, status_code=409)
        self.assertFalse(FinalOrder.objects.exists())
        refreshed = self.client.get(detail_url)
        self.assertEqual(refreshed.context["total_amount"], 6000)
        self.token = refreshed.context["confirmation_token"]
        self.assertEqual(self.send().total_amount, 6000)

    def test_version_change_requires_confirmation_even_if_aggregate_amount_unchanged(self):
        self.prepare()
        edit_submission(
            user=self.owner,
            room_id=self.room.pk,
            submission_id=self.entry.pk,
            version=1,
            nickname="변경된닉네임",
            items=self.items(),
        )
        with self.assertRaises(ConfirmationChanged):
            self.send()
        self.assertFalse(FinalOrder.objects.exists())

    def test_deleted_or_new_order_after_preview_cannot_silently_send(self):
        self.prepare()
        self.add(user=self.owner)
        with self.assertRaises(ConfirmationChanged):
            self.send()
        self.assertFalse(FinalOrder.objects.exists())
        edit_submission(
            user=self.owner,
            room_id=self.room.pk,
            submission_id=self.entry.pk,
            version=1,
            delete=True,
        )
        with self.assertRaises(ConfirmationChanged):
            self.send()

    def test_final_order_is_idempotent_and_deadline_is_exactly_five_minutes(self):
        final = self.sent()
        sent_time = final.sent_at
        for _ in range(2):
            self.assertEqual(self.send().pk, final.pk)
        final.refresh_from_db()
        self.room.refresh_from_db()
        self.assertEqual(final.sent_at, sent_time)
        self.assertEqual(final.respond_by - sent_time, timedelta(minutes=5))
        self.assertEqual(FinalOrder.objects.count(), 1)
        self.assertEqual(self.room.events.filter(action="sent").count(), 1)
        self.assertEqual(self.room.status, "pending")
        self.assertContains(self.client.get(reverse("accounts:organizer")), "주문 수락 대기")

    def test_closed_cafe_blocks_send_without_changing_existing_room_menu(self):
        self.prepare()
        before = self.room.menu_snapshot
        close_for_seller(user=self.seller, cafe_id=self.cafe.pk)
        with self.assertRaises(ValidationError):
            self.send()
        self.assertFalse(FinalOrder.objects.exists())
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "closed")
        self.assertEqual(self.room.menu_snapshot, before)
        Cafe.objects.filter(pk=self.cafe.pk).update(is_open=True)
        self.menu.price = 9000
        self.menu.is_available = False
        self.menu.save()
        self.menu.option_groups.all().delete()
        final = self.send()
        self.assertEqual(final.total_amount, 3000)

    def test_thirty_minute_boundary_blocks_send_and_commits_expiry(self):
        self.prepare()
        with patch("orders.dispatch.timezone.now", return_value=self.room.expires_at):
            self.assertIsNone(self.send())
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "expired")
        self.assertFalse(FinalOrder.objects.exists())
        self.assertEqual(self.room.events.filter(action="expired").count(), 1)

    def test_sent_order_uses_five_minute_deadline_even_after_original_expiry(self):
        with patch(
            "orders.services.timezone.now", return_value=timezone.now() - timedelta(minutes=29)
        ):
            self.room = create_room(user=self.owner, cafe_id=self.cafe.pk)
        final = self.sent()
        self.assertLess(self.room.expires_at, final.respond_by)
        self.assertEqual(expire_due_rooms(now=self.room.expires_at), 0)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "pending")
        self.assertEqual(expire_due_rooms(now=final.respond_by), 1)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "rejected")

    def test_timeout_boundary_persists_rejection_and_reason_once(self):
        final = self.sent()
        self.assertEqual(expire_due_rooms(now=final.respond_by - timedelta(microseconds=1)), 0)
        self.assertEqual(expire_due_rooms(now=final.respond_by), 1)
        self.assertEqual(expire_due_rooms(now=final.respond_by + timedelta(hours=1)), 0)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "rejected")
        self.assertEqual(self.room.rejection_reason, "5분 미응답")
        self.assertEqual(self.room.resolved_at, final.respond_by)
        self.assertEqual(self.room.events.filter(action="timed_out").count(), 1)
        self.assertEqual(FinalOrder.objects.get().items, final.items)
        response = self.client.get(reverse("orders:detail", args=[self.room.pk]))
        self.assertContains(response, "주문 거절 · 5분 미응답")
        self.assertNotContains(response, "주문 수락을 기다리고")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Submission.objects.filter(pk=self.entry.pk).update(items=[])
        with self.assertRaises(IntegrityError), transaction.atomic():
            Room.objects.filter(pk=self.room.pk).update(rejection_reason="변경된 사유")

    def test_status_poll_rechecks_deadline_and_returns_no_order_or_participant_data(self):
        final = self.sent()
        with patch("orders.services.timezone.now", return_value=final.respond_by):
            response = self.client.get(reverse("orders:state", args=[self.room.pk]))
        self.assertEqual(
            response.json(),
            {"status": "rejected", "label": "주문 거절", "rejection_reason": "5분 미응답"},
        )
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "rejected")

    def test_deadline_command_changes_db_without_page_requests(self):
        final = self.sent()
        output = StringIO()
        with patch("orders.services.timezone.now", return_value=final.respond_by):
            call_command("run_deadlines", once=True, stdout=output)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "rejected")
        self.assertIn("1건", output.getvalue())

    def test_cancel_is_only_before_accept_and_keeps_frozen_content(self):
        final = self.sent()
        before = final.items
        self.assertTrue(cancel_order(user=self.owner, room_id=self.room.pk))
        self.assertFalse(cancel_order(user=self.owner, room_id=self.room.pk))
        self.assertEqual(expire_due_rooms(now=final.respond_by), 0)
        self.room.refresh_from_db()
        final.refresh_from_db()
        self.assertEqual(self.room.status, "cancelled")
        self.assertEqual(final.items, before)
        self.assertEqual(self.room.events.filter(action="cancelled").count(), 1)
        self.assertEqual(self.send().pk, final.pk)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "cancelled")

    def test_cancel_at_deadline_rejects_instead_of_recording_cancel(self):
        final = self.sent()
        with patch("orders.dispatch.timezone.now", return_value=final.respond_by):
            self.assertFalse(cancel_order(user=self.owner, room_id=self.room.pk))
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "rejected")
        self.assertEqual(self.room.events.filter(action="cancelled").count(), 0)
        self.assertEqual(self.room.events.filter(action="timed_out").count(), 1)

    def test_accepted_state_is_not_cancellable_or_timed_out(self):
        from .selling import decide_order, ready_time_choices

        final = self.sent()
        decide_order(
            user=self.seller,
            room_id=self.room.pk,
            action="accept",
            ready_at=timezone.datetime.fromisoformat(ready_time_choices()[2][0]),
        )
        self.assertFalse(cancel_order(user=self.owner, room_id=self.room.pk))
        self.assertEqual(expire_due_rooms(now=final.respond_by + timedelta(hours=1)), 0)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "accepted")

    def test_sent_orders_block_add_edit_delete_close_and_guest_join(self):
        self.sent()
        self.assertIsNone(self.add(user=self.owner))
        self.assertIsNone(
            edit_submission(
                user=self.owner,
                room_id=self.room.pk,
                submission_id=self.entry.pk,
                version=1,
                nickname="위조",
                items=self.items(2),
            )
        )
        self.assertFalse(delete_room(user=self.owner, room_id=self.room.pk))
        self.assertFalse(close_collection(user=self.owner, room_id=self.room.pk))
        self.assertEqual(
            self.client.get(
                reverse("orders:order-edit", args=[self.room.pk, self.entry.pk])
            ).status_code,
            410,
        )
        self.assertEqual(Client().get(reverse("join", args=[self.room.token])).status_code, 410)
        self.assertNotContains(
            self.client.get(reverse("orders:detail", args=[self.room.pk])), "주문 수정"
        )

    def test_operator_other_organizer_and_seller_cannot_send_or_cancel(self):
        self.prepare()
        for user in [self.other, self.seller]:
            with self.assertRaises(PermissionDenied):
                preview_order(user=user, room_id=self.room.pk)
            with self.assertRaises(PermissionDenied):
                send_order(user=user, room_id=self.room.pk, confirmation_token=self.token)
            with self.assertRaises(PermissionDenied):
                cancel_order(user=user, room_id=self.room.pk)
            self.client.force_login(user)
            self.assertEqual(
                self.client.get(reverse("orders:confirm", args=[self.room.pk])).status_code, 404
            )
            self.assertEqual(
                self.client.post(
                    reverse("orders:send", args=[self.room.pk]), {"confirmation_token": self.token}
                ).status_code,
                404,
            )
            self.assertEqual(
                self.client.post(reverse("orders:cancel", args=[self.room.pk])).status_code, 404
            )
        self.assertFalse(FinalOrder.objects.exists())

    def test_post_csrf_and_status_scope(self):
        self.prepare()
        for route in ["send", "cancel"]:
            url = reverse("orders:" + route, args=[self.room.pk])
            self.assertEqual(self.client.get(url).status_code, 405)
            browser = Client(enforce_csrf_checks=True)
            browser.force_login(self.owner)
            self.assertEqual(browser.post(url, {"confirmation_token": self.token}).status_code, 403)
        self.assertEqual(
            Client().get(reverse("orders:state", args=[self.room.pk])).status_code, 302
        )
        self.client.force_login(self.seller)
        self.assertEqual(
            self.client.get(reverse("orders:state", args=[self.room.pk])).status_code, 404
        )

    def test_forged_post_prices_and_payload_are_ignored(self):
        self.prepare()
        response = self.client.post(
            reverse("orders:send", args=[self.room.pk]),
            {
                "confirmation_token": self.token,
                "items": "[]",
                "total_amount": 1,
                "organizer_nickname": "위조",
            },
        )
        self.assertEqual(response.status_code, 302)
        final = FinalOrder.objects.get()
        self.assertEqual(final.total_amount, 3000)
        self.assertEqual(final.organizer_nickname, self.owner.nickname)

    def test_failure_during_send_rolls_back_record_state_and_freeze(self):
        self.prepare()
        with (
            patch(
                "orders.dispatch.RoomEvent.objects.create", side_effect=RuntimeError("interrupted")
            ),
            self.assertRaises(RuntimeError),
        ):
            self.send()
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "closed")
        self.assertFalse(FinalOrder.objects.exists())
        edit_submission(
            user=self.owner,
            room_id=self.room.pk,
            submission_id=self.entry.pk,
            version=1,
            nickname="수정 가능",
            items=self.items(2),
        )
        _, _, self.token = preview_order(user=self.owner, room_id=self.room.pk)
        self.assertEqual(self.send().total_amount, 6000)

    def test_database_blocks_final_record_changes_and_delete(self):
        final = self.sent()
        for changes in [
            {"items": []},
            {"total_amount": 1},
            {"organizer_nickname": "위조"},
            {"respond_by": final.respond_by + timedelta(seconds=1)},
        ]:
            with (
                self.subTest(changes=changes),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                FinalOrder.objects.filter(pk=final.pk).update(**changes)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinalOrder.objects.filter(pk=final.pk)._raw_delete("default")

    def test_database_blocks_sent_source_inserts_updates_deletes_and_history_rewrite(self):
        self.sent()
        for changes in [
            {"nickname": "위조"},
            {"items": []},
            {"total_quantity": 5},
            {"deleted_at": timezone.now()},
        ]:
            with (
                self.subTest(changes=changes),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                Submission.objects.filter(pk=self.entry.pk).update(**changes)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Submission.objects.filter(pk=self.entry.pk)._raw_delete("default")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Submission.objects.create(
                room=self.room,
                nickname="추가",
                source="participant",
                request_key=uuid.uuid4(),
                items=self.entry.items,
                total_quantity=1,
                total_amount=3000,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            SubmissionRevision.objects.filter(submission=self.entry).update(snapshot={})
        with self.assertRaises(IntegrityError), transaction.atomic():
            SubmissionRevision.objects.create(
                submission=self.entry, version=2, action="edited", snapshot={}
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            RoomEvent.objects.filter(room=self.room, action="created").update(action="deleted")
        with self.assertRaises(IntegrityError), transaction.atomic():
            RoomEvent.objects.filter(room=self.room, action="sent")._raw_delete("default")
        for status in ["open", "closed", "expired", "deleted"]:
            with (
                self.subTest(status=status),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                Room.objects.filter(pk=self.room.pk).update(status=status)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Room.objects.filter(pk=self.room.pk).update(organizer=self.other)

    def test_frozen_records_stay_frozen_after_cancel_and_timeout(self):
        final = self.sent()
        cancel_order(user=self.owner, room_id=self.room.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Submission.objects.filter(pk=self.entry.pk).update(nickname="취소 후 변경")
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinalOrder.objects.filter(pk=final.pk).update(total_amount=5)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Room.objects.filter(pk=self.room.pk).update(status="pending", resolved_at=None)

    def test_account_erasure_can_unlink_actor_without_changing_order(self):
        self.entry = self.add(user=self.owner)
        close_collection(user=self.owner, room_id=self.room.pk)
        _, _, self.token = preview_order(user=self.owner, room_id=self.room.pk)
        self.send()
        SubmissionRevision.objects.filter(submission=self.entry).update(actor=None)
        Room.objects.filter(pk=self.room.pk).update(organizer=None)
        final = FinalOrder.objects.get()
        self.assertEqual(final.organizer_nickname, self.owner.nickname)
        self.assertEqual(self.entry.revisions.get().snapshot["total_amount"], 3000)


class DispatchConcurrencyTests(DispatchFixture, TransactionTestCase):
    def race(self, functions):
        barrier = Barrier(len(functions))

        def run(fn):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return fn()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(functions)) as pool:
            jobs = [pool.submit(run, fn) for fn in functions]
            return [job.result(timeout=15) for job in jobs]

    def test_duplicate_send_creates_one_record_event_and_deadline(self):
        self.prepare()
        results = self.race([self.send, self.send])
        self.assertEqual(results[0].pk, results[1].pk)
        self.assertEqual(FinalOrder.objects.count(), 1)
        self.assertEqual(self.room.events.filter(action="sent").count(), 1)

    def test_direct_database_write_vs_send_cannot_commit_after_freeze(self):
        self.prepare()
        changed = json.loads(json.dumps(self.entry.items))
        changed[0]["quantity"] = 2
        changed[0]["line_total"] = 6000

        def send():
            try:
                return self.send()
            except ConfirmationChanged:
                return None

        def raw_edit():
            try:
                return Submission.objects.filter(pk=self.entry.pk).update(
                    items=changed, total_quantity=2, total_amount=6000
                )
            except IntegrityError:
                return 0

        sent, edited = self.race([send, raw_edit])
        self.entry.refresh_from_db()
        if sent:
            self.assertEqual(edited, 0)
            self.assertEqual(self.entry.total_amount, sent.total_amount)
        else:
            self.assertEqual(edited, 1)
            self.assertFalse(FinalOrder.objects.exists())

    def test_edit_vs_send_cannot_change_frozen_content(self):
        self.prepare()

        def send():
            try:
                return self.send()
            except ConfirmationChanged:
                return None

        def edit():
            return edit_submission(
                user=self.owner,
                room_id=self.room.pk,
                submission_id=self.entry.pk,
                version=1,
                nickname="경합",
                items=self.items(2),
            )

        sent, edited = self.race([send, edit])
        self.room.refresh_from_db()
        self.entry.refresh_from_db()
        if sent:
            self.assertIsNone(edited)
            self.assertEqual(sent.total_amount, 3000)
            self.assertEqual(self.entry.total_amount, 3000)
        else:
            self.assertIsNotNone(edited)
            self.assertFalse(FinalOrder.objects.exists())
            self.assertEqual(self.room.status, "closed")

    def test_send_vs_logout_serializes_cafe_availability(self):
        self.prepare()

        def send():
            try:
                return self.send()
            except ValidationError:
                return None

        sent, _ = self.race(
            [send, lambda: close_for_seller(user=self.seller, cafe_id=self.cafe.pk)]
        )
        self.room.refresh_from_db()
        self.cafe.refresh_from_db()
        self.assertFalse(self.cafe.is_open)
        self.assertEqual(self.room.status, "pending" if sent else "closed")
        self.assertEqual(FinalOrder.objects.count(), int(sent is not None))

    def test_two_workers_and_cancel_at_deadline_record_one_rejection(self):
        final = self.sent()
        with patch("orders.services.timezone.now", return_value=final.respond_by):
            results = self.race(
                [
                    lambda: expire_due_rooms(now=final.respond_by),
                    lambda: expire_due_rooms(now=final.respond_by),
                    lambda: cancel_order(user=self.owner, room_id=self.room.pk),
                ]
            )
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "rejected")
        self.assertFalse(results[2])
        self.assertEqual(self.room.events.filter(action="timed_out").count(), 1)
        self.assertEqual(self.room.events.filter(action="cancelled").count(), 0)

    def test_send_vs_room_delete_has_only_one_winner(self):
        self.prepare()

        def send():
            try:
                return self.send()
            except ValidationError:
                return None

        sent, deleted = self.race(
            [send, lambda: delete_room(user=self.owner, room_id=self.room.pk)]
        )
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "pending" if sent else "deleted")
        self.assertEqual(bool(sent), not deleted)
