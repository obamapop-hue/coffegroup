import uuid
from datetime import datetime, time, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from cafes.models import Cafe, Menu
from orders.collection import add_submission, close_collection, edit_submission
from orders.dispatch import preview_order, send_order
from orders.models import Room
from orders.services import create_room, delete_room


class OrderHistoryTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user("history-operator", is_operator=True)
        self.first = User.objects.create_user("history-first", nickname="김총무")
        self.second = User.objects.create_user("history-second", nickname="이총무")
        self.cafe = Cafe.objects.create(name="첫 카페", seller=self.operator, is_open=True)
        self.other_cafe = Cafe.objects.create(name="둘째 카페", seller=self.second, is_open=True)
        self.menu = Menu.objects.create(cafe=self.cafe, name="커피", price=3000)
        self.other_menu = Menu.objects.create(cafe=self.other_cafe, name="라떼", price=4000)
        self.room = create_room(user=self.first, cafe_id=self.cafe.pk)
        self.other_room = create_room(user=self.second, cafe_id=self.other_cafe.pk)
        self.entry = self.add(self.room, self.menu, self.first, 2)
        self.add(self.other_room, self.other_menu, self.second, 3)
        self.client.force_login(self.operator)
        self.url = reverse("accounts:operator")

    def add(self, room, menu, user, quantity):
        return add_submission(
            user=user,
            room_id=room.pk,
            nickname="목록에 나오지 않을 참여자 별칭",
            items=[{"menu_id": menu.pk, "quantity": quantity}],
            request_key=uuid.uuid4(),
        )

    def get_history(self, **params):
        return self.client.get(self.url, {"tab": "orders", **params})

    def test_cafe_selection_and_member_search_combine_without_exposing_participant_names(self):
        response = self.get_history()
        self.assertContains(response, "김총무")
        self.assertContains(response, "이총무")
        self.assertContains(response, "총 2잔")
        self.assertContains(response, "6000원")
        self.assertContains(response, "총 3잔")
        self.assertContains(response, "12000원")
        self.assertNotContains(response, self.entry.nickname)
        response = self.get_history(cafe=self.cafe.pk, name="김")
        self.assertEqual([r.pk for r in response.context["history_page"]], [self.room.pk])
        self.assertEqual(response.context["selected_cafe"], self.cafe)
        response = self.get_history(cafe=self.other_cafe.pk, name="김")
        self.assertEqual(response.context["history_page"].paginator.count, 0)

    def test_live_totals_ignore_deleted_items_and_empty_rooms_show_zero(self):
        removed = self.add(self.room, self.menu, self.first, 9)
        edit_submission(
            user=self.first,
            room_id=self.room.pk,
            submission_id=removed.pk,
            version=removed.version,
            delete=True,
        )
        empty = create_room(user=self.first, cafe_id=self.cafe.pk)
        delete_room(user=self.first, room_id=self.room.pk)
        rows = {r.pk: r for r in self.get_history(cafe=self.cafe.pk).context["history_page"]}
        self.assertEqual(rows[self.room.pk].history_quantity, 2)
        self.assertEqual(rows[self.room.pk].history_amount, 6000)
        self.assertEqual(rows[empty.pk].history_quantity, 0)
        self.assertEqual(rows[empty.pk].history_amount, 0)

    def test_sent_snapshot_remains_searchable_after_account_and_cafe_changes(self):
        close_collection(user=self.first, room_id=self.room.pk)
        _, _, token = preview_order(user=self.first, room_id=self.room.pk)
        send_order(user=self.first, room_id=self.room.pk, confirmation_token=token)
        self.first.nickname = "바뀐 이름"
        self.first.save()
        self.cafe.name = "변경된 카페명"
        self.cafe.save()
        Room.objects.filter(pk=self.room.pk).update(organizer=None)
        response = self.get_history(cafe=self.cafe.pk, name="김총무")
        row = list(response.context["history_page"])[0]
        self.assertEqual(row.pk, self.room.pk)
        self.assertEqual(row.history_nickname, "김총무")
        self.assertEqual((row.history_quantity, row.history_amount), (2, 6000))
        self.assertEqual(row.cafe_snapshot["name"], "첫 카페")
        self.assertEqual(response.context["selected_cafe"].name, "변경된 카페명")
        self.assertEqual(
            self.get_history(name="바뀐 이름").context["history_page"].paginator.count, 0
        )

    def test_date_filter_uses_korean_midnight_boundaries(self):
        day = timezone.localdate() + timedelta(days=2)
        midnight = datetime.combine(day, time.min, tzinfo=timezone.get_default_timezone())
        times = [
            midnight - timedelta(microseconds=1),
            midnight,
            midnight + timedelta(days=1) - timedelta(microseconds=1),
            midnight + timedelta(days=1),
        ]
        rooms = []
        for at in times:
            with patch("orders.services.timezone.now", return_value=at):
                rooms.append(create_room(user=self.first, cafe_id=self.cafe.pk))
        response = self.get_history(cafe=self.cafe.pk, date=day.isoformat(), name="김")
        self.assertEqual(
            {r.pk for r in response.context["history_page"]}, {rooms[1].pk, rooms[2].pk}
        )

    def test_invalid_search_shows_errors_and_does_not_fall_back_to_all_orders(self):
        for params, field in [
            ({"cafe": self.cafe.pk, "date": "not-a-date"}, "date"),
            ({"cafe": "999999"}, "cafe"),
            ({"name": "x" * 151}, "name"),
        ]:
            response = self.get_history(**params)
            self.assertEqual(response.status_code, 200)
            self.assertIn(field, response.context["history_form"].errors)
            self.assertEqual(response.context["history_page"].paginator.count, 0)
        response = self.get_history(cafe=self.cafe.pk, date="invalid")
        self.assertEqual(response.context["selected_cafe"], self.cafe)

    def test_pagination_and_cafe_links_keep_filters_without_per_order_queries(self):
        for _ in range(22):
            create_room(user=self.first, cafe_id=self.cafe.pk)
        params = {"cafe": self.cafe.pk, "name": "김", "date": timezone.localdate().isoformat()}
        with CaptureQueriesContext(connection) as queries:
            response = self.get_history(**params)
        self.assertEqual(len(response.context["history_page"]), 20)
        self.assertLessEqual(
            sum(q["sql"].lstrip().upper().startswith("SELECT") for q in queries), 15
        )
        query = parse_qs(response.context["history_query"])
        self.assertEqual(query["cafe"], [str(self.cafe.pk)])
        self.assertEqual(query["name"], ["김"])
        self.assertEqual(query["date"], [params["date"]])
        self.assertEqual(len(self.get_history(**params, page=2).context["history_page"]), 3)
        other = next(c for c in response.context["history_cafes"] if c["name"] == "둘째 카페")
        next_query = parse_qs(urlsplit(other["url"]).query)
        self.assertEqual(next_query["name"], ["김"])
        self.assertEqual(next_query["date"], [params["date"]])

    def test_names_are_escaped_and_missing_old_member_information_is_not_invented(self):
        self.first.nickname = '<script>alert("x")</script>'
        self.first.save()
        response = self.get_history(name=self.first.nickname)
        self.assertNotContains(response, self.first.nickname)
        self.assertContains(response, "&lt;script&gt;")
        Room.objects.filter(pk=self.room.pk).update(organizer=None)
        response = self.get_history(cafe=self.cafe.pk)
        self.assertContains(response, "회원 정보 없음")
