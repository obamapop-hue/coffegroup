from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connections, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from cafes.catalog import add_basic_options
from cafes.models import Cafe, Menu
from cafes.services import close_for_seller

from .models import Room, RoomEvent
from .services import create_room, delete_room, expire_due_rooms


class RoomTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", nickname="비공개총무닉네임")
        self.other = User.objects.create_user("other")
        self.seller = User.objects.create_user("seller")
        self.cafe = Cafe.objects.create(
            name="파일럿 카페",
            seller=self.seller,
            is_open=True,
            phone="02-123-4567",
            address="서울 테스트 주소",
        )
        self.menu = Menu.objects.create(cafe=self.cafe, name="아메리카노", price=3000)
        add_basic_options(self.menu)
        self.client.force_login(self.owner)

    def make_room(self, at=None):
        with patch("orders.services.timezone.now", return_value=at or timezone.now()):
            return create_room(user=self.owner, cafe_id=self.cafe.pk)

    def test_create_returns_shared_frozen_menu_and_exact_expiry_notice(self):
        response = self.client.post(reverse("orders:create", args=[self.cafe.pk]), follow=True)
        self.assertEqual(response.status_code, 200)
        room = Room.objects.get()
        self.assertEqual(room.expires_at - room.created_at, timedelta(minutes=30))
        self.assertContains(response, timezone.localtime(room.expires_at).strftime("%H:%M"))
        self.assertContains(response, f"/join/{room.token}/")
        self.assertEqual(room.menu_snapshot[0]["groups"][1]["choices"][0]["name"], "샷 +0")
        self.assertTrue(room.menu_snapshot[0]["groups"][1]["choices"][0]["is_default"])

    def test_source_changes_only_affect_new_rooms_not_reload_or_anonymous_visitors(self):
        room = self.make_room()
        self.menu.price = 5500
        self.menu.save()
        self.menu.option_groups.first().delete()
        new_room = self.make_room()
        self.assertEqual(new_room.menu_snapshot[0]["price"], 5500)
        visitor = Client()
        for _ in range(2):
            response = visitor.get(reverse("join", args=[room.token]))
            self.assertContains(response, "3000원")
            self.assertEqual(response.context["menu"][0]["groups"][0]["name"], "아이스 / 핫")
            self.assertNotContains(response, "5500원")
            self.assertNotContains(response, self.owner.nickname)
            self.assertNotContains(response, str(self.owner.account_code))
            self.assertIn("no-store", response.headers["Cache-Control"])

    def test_closed_cafe_and_sold_out_menus_block_new_rooms_but_existing_room_survives(self):
        room = self.make_room()
        self.menu.is_available = False
        self.menu.save()
        with self.assertRaises(ValidationError):
            self.make_room()
        self.assertEqual(Client().get(reverse("join", args=[room.token])).status_code, 200)
        close_for_seller(user=self.seller, cafe_id=self.cafe.pk)
        response = self.client.post(reverse("orders:create", args=[self.cafe.pk]), follow=True)
        self.assertContains(response, "영업종료")
        self.assertEqual(Room.objects.count(), 1)
        self.assertEqual(Client().get(reverse("join", args=[room.token])).status_code, 200)

    def test_database_blocks_snapshot_or_deadline_mutation(self):
        room = self.make_room()
        for changes in (
            {"menu_snapshot": []},
            {"cafe_snapshot": {}},
            {"expires_at": room.expires_at + timedelta(minutes=1)},
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Room.objects.filter(pk=room.pk).update(**changes)

    def test_expiry_boundary_is_persisted_once_and_guest_link_has_exact_message(self):
        room = self.make_room()
        self.assertEqual(expire_due_rooms(now=room.expires_at - timedelta(microseconds=1)), 0)
        self.assertEqual(expire_due_rooms(now=room.expires_at), 1)
        self.assertEqual(expire_due_rooms(now=room.expires_at + timedelta(minutes=1)), 0)
        room.refresh_from_db()
        self.assertEqual(room.status, Room.Status.EXPIRED)
        self.assertEqual(room.events.filter(action=RoomEvent.Action.EXPIRED).count(), 1)
        self.assertContains(
            Client().get(reverse("join", args=[room.token])),
            "참여할 수 없는 주문서입니다.",
            status_code=410,
        )
        self.assertContains(self.client.get(reverse("accounts:profile")), "주문서 폐기")

    def test_closed_unsent_room_also_expires_and_request_enforces_deadline(self):
        room = self.make_room(at=timezone.now() - timedelta(minutes=31))
        Room.objects.filter(pk=room.pk).update(status=Room.Status.CLOSED)
        response = Client().get(reverse("join", args=[room.token]))
        self.assertEqual(response.status_code, 410)
        room.refresh_from_db()
        self.assertEqual(room.status, Room.Status.EXPIRED)

    def test_manual_delete_is_soft_preserves_snapshots_and_history_and_is_idempotent(self):
        room = self.make_room()
        original = room.menu_snapshot
        response = self.client.post(reverse("orders:delete", args=[room.pk]), follow=True)
        self.assertRedirects(response, reverse("accounts:organizer"))
        self.assertContains(response, "주문서를 삭제했어요.")
        self.assertContains(self.client.get(reverse("accounts:profile")), "주문서 삭제")
        delete_room(user=self.owner, room_id=room.pk)
        room.refresh_from_db()
        self.assertEqual(room.status, Room.Status.DELETED)
        self.assertEqual(room.menu_snapshot, original)
        self.assertEqual(room.events.filter(action=RoomEvent.Action.DELETED).count(), 1)
        self.assertEqual(Client().get(reverse("join", args=[room.token])).status_code, 410)
        self.assertEqual(expire_due_rooms(now=room.expires_at), 0)

    def test_expired_room_cannot_be_reclassified_as_manual_delete(self):
        room = self.make_room(at=timezone.now() - timedelta(minutes=31))
        self.assertFalse(delete_room(user=self.owner, room_id=room.pk))
        room.refresh_from_db()
        self.assertEqual(room.status, Room.Status.EXPIRED)

    def test_history_is_private_and_only_operator_can_read_others_rooms(self):
        room = self.make_room()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("orders:detail", args=[room.pk])).status_code, 404)
        self.assertEqual(
            self.client.post(reverse("orders:delete", args=[room.pk])).status_code, 404
        )
        self.assertNotContains(self.client.get(reverse("accounts:profile")), self.cafe.name)
        self.other.is_operator = True
        self.other.save()
        self.assertEqual(self.client.get(reverse("orders:detail", args=[room.pk])).status_code, 200)
        self.assertEqual(
            self.client.post(reverse("orders:delete", args=[room.pk])).status_code, 404
        )
        self.assertNotContains(self.client.get(reverse("accounts:profile")), self.cafe.name)
        self.assertEqual(Client().get(reverse("join", args=["unknown"])).status_code, 404)

    def test_room_creation_and_delete_require_post_and_csrf(self):
        room = self.make_room()
        for name, arg in (("orders:create", self.cafe.pk), ("orders:delete", room.pk)):
            self.assertEqual(self.client.get(reverse(name, args=[arg])).status_code, 405)
            client = Client(enforce_csrf_checks=True)
            client.force_login(self.owner)
            self.assertEqual(client.post(reverse(name, args=[arg])).status_code, 403)

    def test_deadline_command_updates_database_without_any_page_request(self):
        room = self.make_room(at=timezone.now() - timedelta(minutes=31))
        output = StringIO()
        call_command("run_deadlines", once=True, stdout=output)
        room.refresh_from_db()
        self.assertEqual(room.status, Room.Status.EXPIRED)
        self.assertIn("1건", output.getvalue())


class DeadlineRaceTests(TransactionTestCase):
    def test_two_workers_expire_one_room_without_duplicate_events(self):
        user = User.objects.create_user("race-user")
        cafe = Cafe.objects.create(name="카페", seller=user, is_open=True)
        Menu.objects.create(cafe=cafe, name="커피", price=3000)
        with patch(
            "orders.services.timezone.now", return_value=timezone.now() - timedelta(minutes=31)
        ):
            room = create_room(user=user, cafe_id=cafe.pk)
        barrier = Barrier(2)

        def process():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return expire_due_rooms()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(process) for _ in range(2)]
            self.assertEqual(sum(job.result(timeout=10) for job in jobs), 1)
        room.refresh_from_db()
        self.assertEqual(room.status, Room.Status.EXPIRED)
        self.assertEqual(room.events.filter(action="expired").count(), 1)
