import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import User
from cafes.catalog import add_basic_options
from cafes.models import Cafe, Menu, OptionChoice, OptionGroup

from .collection import (
    EditConflict,
    add_submission,
    close_collection,
    edit_submission,
    editable_items,
)
from .forms import submission_token
from .models import Room, Submission, SubmissionRevision
from .services import create_room, delete_room


class CollectionFixture:
    def setUp(self):
        self.owner = User.objects.create_user("collect-owner", nickname="비공개총무")
        self.other = User.objects.create_user("collect-other", is_operator=True)
        self.seller = User.objects.create_user("collect-seller")
        self.cafe = Cafe.objects.create(name="취합 카페", seller=self.seller, is_open=True)
        self.menu = Menu.objects.create(cafe=self.cafe, name="아메리카노", price=3000)
        add_basic_options(self.menu)
        self.temp = self.menu.option_groups.first()
        self.shots = self.menu.option_groups.last()
        self.shot = OptionChoice.objects.create(group=self.shots, name="샷 +1", extra_price=500)
        self.extras = OptionGroup.objects.create(
            menu=self.menu, name="추가", mode="multiple", required=False
        )
        self.extra = OptionChoice.objects.create(group=self.extras, name="시럽", extra_price=200)
        self.room = create_room(user=self.owner, cafe_id=self.cafe.pk)
        self.url = reverse("join", args=[self.room.token])
        self.client.force_login(self.owner)

    def items(self, quantity=1):
        return [{"menu_id": self.menu.pk, "quantity": quantity}]

    def add(self, **kwargs):
        return add_submission(
            room_id=self.room.pk,
            nickname="비공개참여자",
            items=self.items(),
            request_key=uuid.uuid4(),
            **kwargs,
        )

    def post_data(self, items=None, nickname="비공개참여자", token=None):
        return {
            "nickname": nickname,
            "items": json.dumps(items if items is not None else self.items()),
            "request_token": token or submission_token(self.room.pk),
        }


class CollectionTests(CollectionFixture, TestCase):
    def test_submission_delete_is_available_from_editor_and_preserves_history(self):
        entry = self.add()
        response = self.client.get(reverse("orders:order-edit", args=[self.room.pk, entry.pk]))
        delete_url = reverse("orders:order-delete", args=[self.room.pk, entry.pk])
        self.assertContains(response, 'action="' + delete_url)
        self.assertContains(response, "이 참여자 주문 삭제")
        edit_submission(
            user=self.owner,
            room_id=self.room.pk,
            submission_id=entry.pk,
            version=entry.version,
            nickname=entry.nickname,
            items=self.items(2),
        )
        stale = self.post_data()
        stale["version"] = entry.version
        response = self.client.post(
            reverse("orders:order-edit", args=[self.room.pk, entry.pk]), stale
        )
        self.assertContains(response, 'name="version" value="1"', status_code=409)
        self.assertEqual(self.client.post(delete_url, {"version": 1}).status_code, 409)
        entry.refresh_from_db()
        self.assertIsNone(entry.deleted_at)
        response = self.client.post(delete_url, {"version": entry.version})
        self.assertRedirects(response, reverse("orders:detail", args=[self.room.pk]))
        entry.refresh_from_db()
        self.assertIsNotNone(entry.deleted_at)
        self.assertTrue(entry.revisions.filter(action="deleted").exists())

    def quantity_room(self):
        self.shots.mode = "multiple"
        self.shots.save()
        return create_room(user=self.owner, cafe_id=self.cafe.pk)

    def test_independent_option_quantities_multiply_per_drink_and_round_trip_edit(self):
        room = self.quantity_room()
        items = [
            {
                "menu_id": self.menu.pk,
                "quantity": 3,
                "selections": {
                    str(self.shots.pk): [self.shot.pk],
                    str(self.extras.pk): [self.extra.pk],
                },
                "option_quantities": {
                    str(self.shots.pk): {str(self.shot.pk): 2},
                    str(self.extras.pk): {str(self.extra.pk): 3},
                },
            }
        ]
        result = add_submission(
            room_id=room.pk, nickname="수량", items=items, request_key=uuid.uuid4()
        )
        self.assertEqual(result.items[0]["unit_price"], 4600)
        self.assertEqual(result.total_amount, 13800)
        self.assertEqual(result.items[0]["options"][1]["choices"][0]["quantity"], 2)
        self.assertEqual(result.items[0]["options"][2]["choices"][0]["quantity"], 3)
        edited = edit_submission(
            user=self.owner,
            room_id=room.pk,
            submission_id=result.pk,
            version=1,
            nickname="수량 수정",
            items=editable_items(result),
        )
        self.assertEqual(edited.total_amount, 13800)
        response = self.client.get(reverse("orders:detail", args=[room.pk]))
        self.assertContains(response, "샷 +1 × 2")
        self.assertContains(response, "시럽 × 3")

    def test_option_quantity_one_keeps_legacy_payload_and_saved_order_compatible(self):
        room = self.quantity_room()
        items = [
            {
                "menu_id": self.menu.pk,
                "quantity": 2,
                "selections": {str(self.shots.pk): [self.shot.pk]},
            }
        ]
        key = uuid.uuid4()
        original = add_submission(room_id=room.pk, nickname="기존", items=items, request_key=key)
        self.assertNotIn("quantity", original.items[0]["options"][1]["choices"][0])
        items[0]["option_quantities"] = {str(self.shots.pk): {str(self.shot.pk): 1}}
        replay = add_submission(room_id=room.pk, nickname="기존", items=items, request_key=key)
        self.assertEqual(replay.pk, original.pk)
        self.assertEqual(replay.total_amount, 7000)

    def test_invalid_option_quantities_cannot_change_price_or_unselected_choices(self):
        room = self.quantity_room()
        bad = [
            {str(self.shots.pk): {str(self.shot.pk): count}}
            for count in [0, -1, 100, 1.5, "2", True, None]
        ]
        bad += [
            {"999999": {}},
            {str(self.shots.pk): []},
            {str(self.shots.pk): {str(self.extra.pk): 2}},
            {str(self.extras.pk): {str(self.extra.pk): 2}},
            {str(self.temp.pk): {str(self.temp.choices.first().pk): 2}},
        ]
        for quantities in bad:
            with self.subTest(quantities=quantities), self.assertRaises(ValidationError):
                add_submission(
                    room_id=room.pk,
                    nickname="오류",
                    items=[
                        {
                            "menu_id": self.menu.pk,
                            "quantity": 1,
                            "selections": {str(self.shots.pk): [self.shot.pk]},
                            "option_quantities": quantities,
                        }
                    ],
                    request_key=uuid.uuid4(),
                )
        self.assertFalse(room.submissions.exists())

    def test_categories_follow_frozen_menu_and_uncategorized_items_use_other(self):
        self.menu.category = "커피"
        self.menu.save()
        Menu.objects.create(cafe=self.cafe, name="차 메뉴", category="차", price=3500)
        Menu.objects.create(cafe=self.cafe, name="쿠키", category="디저트", price=2000)
        Menu.objects.create(cafe=self.cafe, name="분류 없음", category="", price=1000)
        room = create_room(user=self.owner, cafe_id=self.cafe.pk)
        self.menu.category = "변경된 분류"
        self.menu.save()
        response = Client().get(reverse("join", args=[room.token]))
        self.assertEqual(
            {c["name"] for c in response.context["menu_categories"]},
            {"커피", "차", "디저트", "기타"},
        )
        self.assertNotContains(response, "변경된 분류")

    def test_order_header_has_cafe_contact_and_timed_collection_with_hidden_copy_link(self):
        from django.utils import timezone

        response = self.client.get(reverse("orders:detail", args=[self.room.pk]))
        title = f"[{timezone.localtime(self.room.created_at):%H:%M}] 주문서"
        self.assertContains(response, f"<h1>{self.cafe.name}</h1>", html=True)
        self.assertContains(response, title)
        self.assertContains(response, 'aria-label="참여 링크 복사"')
        self.assertNotContains(response, "주문서 상태")
        self.assertNotContains(response, "주문서 만료")
        body = response.content.decode()
        self.assertLess(body.index(title), body.index('id="share-link"'))
        self.assertLess(body.index('class="order-cafe-contact"'), body.index('id="share-link"'))
        self.assertContains(response, 'id="share-link" type="hidden"')

    def test_snapshot_prices_defaults_multiple_lines_and_original_changes(self):
        self.menu.price = 9000
        self.menu.save()
        self.shot.extra_price = 900
        self.shot.save()
        items = self.items(2) + [
            {
                "menu_id": self.menu.pk,
                "quantity": 3,
                "selections": {
                    str(self.shots.pk): [self.shot.pk],
                    str(self.extras.pk): [self.extra.pk],
                },
                "unit_price": 1,
                "line_total": 1,
            }
        ]
        result = add_submission(
            room_id=self.room.pk, nickname=" 이름 ", items=items, request_key=uuid.uuid4()
        )
        self.assertEqual(result.nickname, "이름")
        self.assertEqual(result.total_quantity, 5)
        self.assertEqual(result.total_amount, 17100)
        self.assertEqual(result.items[0]["unit_price"], 3000)
        self.assertEqual(result.items[0]["options"][0]["choices"][0]["name"], "아이스")
        self.assertEqual(result.items[0]["options"][1]["choices"][0]["name"], "샷 +0")
        self.assertEqual(result.items[1]["unit_price"], 3700)
        self.assertEqual(result.revisions.count(), 1)

    def test_rejects_invalid_quantity_catalog_and_option_shapes_atomically(self):
        variants = [
            [],
            None,
            {},
            self.items(True),
            self.items(0),
            self.items(100),
            self.items(1.5),
            self.items("1"),
            [{"menu_id": True, "quantity": 1}],
            [{"menu_id": 999999, "quantity": 1}],
            self.items() * 51,
        ]
        for selections in [
            {str(self.temp.pk): []},
            {str(self.temp.pk): [c.pk for c in self.temp.choices.all()]},
            {"999999": []},
            {str(self.shots.pk): [self.shot.pk, self.shot.pk]},
            {str(self.shots.pk): [self.extra.pk]},
            {str(self.temp.pk): [{}]},
            {str(self.temp.pk): "bad"},
        ]:
            variants.append([{"menu_id": self.menu.pk, "quantity": 1, "selections": selections}])
        for items in variants:
            with self.subTest(items=items), self.assertRaises(ValidationError):
                add_submission(
                    room_id=self.room.pk, nickname="테스트", items=items, request_key=uuid.uuid4()
                )
        self.assertFalse(Submission.objects.exists())
        self.assertFalse(SubmissionRevision.objects.exists())

    def test_names_trim_limit_and_escape_in_private_detail(self):
        for name in ["", " " * 3, "가" * 31, "a\nb", None]:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                add_submission(
                    room_id=self.room.pk,
                    nickname=name,
                    items=self.items(),
                    request_key=uuid.uuid4(),
                )
        add_submission(
            room_id=self.room.pk,
            nickname="<script>alert(1)</script>",
            items=self.items(),
            request_key=uuid.uuid4(),
        )
        response = self.client.get(reverse("orders:detail", args=[self.room.pk]))
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_idempotent_submit_and_changed_payload_does_not_overwrite(self):
        key = uuid.uuid4()
        first = add_submission(
            room_id=self.room.pk, nickname="같은이름", items=self.items(), request_key=key
        )
        second = add_submission(
            room_id=self.room.pk, nickname="같은이름", items=self.items(), request_key=key
        )
        self.assertEqual(first.pk, second.pk)
        with self.assertRaises(ValidationError):
            add_submission(
                room_id=self.room.pk, nickname="같은이름", items=self.items(2), request_key=key
            )
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(SubmissionRevision.objects.count(), 1)
        add_submission(
            room_id=self.room.pk, nickname="같은이름", items=self.items(), request_key=uuid.uuid4()
        )
        self.assertEqual(Submission.objects.count(), 2)

    def test_closed_blocks_participants_but_owner_can_add_edit_delete(self):
        first = self.add()
        self.assertTrue(close_collection(user=self.owner, room_id=self.room.pk))
        self.assertFalse(close_collection(user=self.owner, room_id=self.room.pk))
        self.assertIsNone(self.add())
        own = self.add(user=self.owner)
        self.assertEqual(own.source, "organizer")
        edit_submission(
            user=self.owner,
            room_id=self.room.pk,
            submission_id=first.pk,
            version=1,
            nickname="수정",
            items=self.items(3),
        )
        first.refresh_from_db()
        self.assertEqual(first.total_amount, 9000)
        edit_submission(
            user=self.owner, room_id=self.room.pk, submission_id=first.pk, version=2, delete=True
        )
        first.refresh_from_db()
        self.assertIsNotNone(first.deleted_at)
        self.assertEqual(first.revisions.count(), 3)
        self.assertEqual(self.room.events.filter(action="closed").count(), 1)

    def test_expiry_on_submit_edit_and_close_commits_without_losing_orders(self):
        first = self.add()
        with patch("orders.collection.timezone.now", return_value=self.room.expires_at):
            self.assertIsNone(self.add())
            self.assertIsNone(
                edit_submission(
                    user=self.owner,
                    room_id=self.room.pk,
                    submission_id=first.pk,
                    version=1,
                    delete=True,
                )
            )
            self.assertFalse(close_collection(user=self.owner, room_id=self.room.pk))
        self.room.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual(self.room.status, Room.Status.EXPIRED)
        self.assertEqual(self.room.events.filter(action="expired").count(), 1)
        self.assertEqual(first.total_amount, 3000)
        self.assertIsNone(first.deleted_at)

    def test_expiry_immediately_before_and_at_boundary(self):
        with patch(
            "orders.collection.timezone.now",
            return_value=self.room.expires_at - timedelta(microseconds=1),
        ):
            self.assertIsNotNone(self.add())
        with patch("orders.collection.timezone.now", return_value=self.room.expires_at):
            self.assertIsNone(self.add(user=self.owner))
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "expired")

    def test_other_user_operator_and_seller_cannot_change_owner_orders(self):
        first = self.add()
        for user in [self.other, self.seller]:
            with self.subTest(user=user.pk), self.assertRaises(PermissionDenied):
                self.add(user=user)
            with self.assertRaises(PermissionDenied):
                close_collection(user=user, room_id=self.room.pk)
            with self.assertRaises(PermissionDenied):
                edit_submission(
                    user=user, room_id=self.room.pk, submission_id=first.pk, version=1, delete=True
                )
            self.client.force_login(user)
            for path in [
                reverse("orders:order-add", args=[self.room.pk]),
                reverse("orders:order-edit", args=[self.room.pk, first.pk]),
            ]:
                self.assertEqual(self.client.get(path).status_code, 404)
                self.assertEqual(self.client.post(path, self.post_data()).status_code, 404)
            self.assertEqual(
                self.client.post(reverse("orders:close", args=[self.room.pk])).status_code, 404
            )
            self.assertEqual(
                self.client.post(
                    reverse("orders:order-delete", args=[self.room.pk, first.pk]), {"version": 1}
                ).status_code,
                404,
            )
        response = self.client.get(reverse("orders:detail", args=[self.room.pk]))
        self.assertEqual(response.status_code, 404)
        self.client.force_login(self.other)
        response = self.client.get(reverse("orders:detail", args=[self.room.pk]))
        self.assertContains(response, "비공개참여자")
        self.assertNotContains(response, "주문 수정")

    def test_stale_edit_and_delete_do_not_overwrite_and_keep_form_input(self):
        first = self.add()
        edit_submission(
            user=self.owner,
            room_id=self.room.pk,
            submission_id=first.pk,
            version=1,
            nickname="수정1",
            items=self.items(2),
        )
        data = self.post_data(items=self.items(4), nickname="내입력유지")
        data["version"] = 1
        response = self.client.post(
            reverse("orders:order-edit", args=[self.room.pk, first.pk]), data
        )
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "내입력유지", status_code=409)
        response = self.client.post(
            reverse("orders:order-delete", args=[self.room.pk, first.pk]), {"version": 1}
        )
        self.assertEqual(response.status_code, 409)
        first.refresh_from_db()
        self.assertEqual(first.total_amount, 6000)
        self.assertEqual(first.revisions.count(), 2)

    def test_public_post_redirect_get_replay_and_no_private_response_data(self):
        self.add(user=self.owner)
        visitor = Client()
        data = self.post_data(nickname="다른비공개참여자")
        for _ in range(2):
            response = visitor.post(self.url, data)
            self.assertRedirects(response, reverse("join-submitted", args=[self.room.token]))
        self.assertEqual(Submission.objects.count(), 2)
        for url in [self.url, reverse("join-submitted", args=[self.room.token])]:
            response = visitor.get(url)
            for name in [
                "비공개참여자",
                "다른비공개참여자",
                "비공개총무",
                str(self.owner.account_code),
            ]:
                self.assertNotContains(response, name)
            self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(
            visitor.get(reverse("orders:detail", args=[self.room.pk])).status_code, 302
        )

    def test_nonce_tampering_missing_and_wrong_room_fail(self):
        other = create_room(user=self.owner, cafe_id=self.cafe.pk)
        for token in ["", "invalid", submission_token(other.pk)]:
            data = self.post_data()
            data["request_token"] = token
            response = Client().post(self.url, data)
            self.assertEqual(response.status_code, 400)
        self.assertFalse(Submission.objects.exists())

    def test_csrf_required_on_public_and_private_writes_and_get_no_mutations(self):
        visitor = Client(enforce_csrf_checks=True)
        self.assertEqual(visitor.post(self.url, self.post_data()).status_code, 403)
        visitor.force_login(self.owner)
        self.assertEqual(
            visitor.post(reverse("orders:close", args=[self.room.pk])).status_code, 403
        )
        self.assertEqual(visitor.get(reverse("orders:close", args=[self.room.pk])).status_code, 405)
        self.assertEqual(Submission.objects.count(), 0)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "open")

    def test_closed_deleted_expired_links_always_show_exact_message(self):
        self.add()
        close_collection(user=self.owner, room_id=self.room.pk)
        for method in ["get", "post"]:
            response = getattr(Client(), method)(
                self.url, self.post_data() if method == "post" else {}
            )
            self.assertContains(response, "참여할 수 없는 주문서입니다.", status_code=410)
        delete_room(user=self.owner, room_id=self.room.pk)
        self.assertContains(Client().get(self.url), "참여할 수 없는 주문서입니다.", status_code=410)
        self.assertEqual(Submission.objects.count(), 1)

    def test_detail_total_excludes_soft_deleted_entries_and_retains_history(self):
        first = self.add()
        self.add(user=self.owner)
        response = self.client.get(reverse("orders:detail", args=[self.room.pk]))
        self.assertContains(response, "총 2잔")
        self.assertContains(response, "6000원")
        edit_submission(
            user=self.owner, room_id=self.room.pk, submission_id=first.pk, version=1, delete=True
        )
        response = self.client.get(reverse("orders:detail", args=[self.room.pk]))
        self.assertContains(response, "총 1잔")
        self.assertEqual(Submission.objects.count(), 2)
        self.assertEqual(SubmissionRevision.objects.count(), 3)

    def test_edit_uses_snapshot_after_source_options_deleted_and_cafe_closed(self):
        first = self.add()
        self.menu.option_groups.all().delete()
        self.cafe.is_open = False
        self.cafe.save()
        response = self.client.get(reverse("orders:order-edit", args=[self.room.pk, first.pk]))
        self.assertEqual(response.context["menu"][0]["groups"][1]["choices"][0]["name"], "샷 +0")
        result = edit_submission(
            user=self.owner,
            room_id=self.room.pk,
            submission_id=first.pk,
            version=1,
            nickname="수정",
            items=self.items(2),
        )
        self.assertEqual(result.total_amount, 6000)


class CollectionConcurrencyTests(CollectionFixture, TransactionTestCase):
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
            return [job.result(timeout=10) for job in jobs]

    def test_two_participants_same_key_create_exactly_once(self):
        key = uuid.uuid4()

        def submit():
            return add_submission(
                room_id=self.room.pk, nickname="동시", items=self.items(), request_key=key
            ).pk

        result = self.race([submit, submit])
        self.assertEqual(result[0], result[1])
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(SubmissionRevision.objects.count(), 1)

    def test_two_distinct_participants_both_preserved(self):
        self.race([self.add, self.add])
        self.assertEqual(Submission.objects.count(), 2)
        self.assertEqual(sum(Submission.objects.values_list("total_amount", flat=True)), 6000)

    def test_submit_and_close_serialize_without_post_close_writes(self):
        result = self.race(
            [self.add, lambda: close_collection(user=self.owner, room_id=self.room.pk)]
        )
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "closed")
        self.assertEqual(Submission.objects.count(), int(result[0] is not None))
        self.assertIsNone(self.add())
        self.assertEqual(self.room.events.filter(action="closed").count(), 1)

    def test_competing_edits_allow_one_version_only(self):
        first = self.add()

        def edit():
            try:
                edit_submission(
                    user=self.owner,
                    room_id=self.room.pk,
                    submission_id=first.pk,
                    version=1,
                    nickname="변경",
                    items=self.items(2),
                )
                return True
            except EditConflict:
                return False

        self.assertEqual(sum(self.race([edit, edit])), 1)
        first.refresh_from_db()
        self.assertEqual(first.version, 2)
        self.assertEqual(first.revisions.count(), 2)
