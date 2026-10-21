import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event, local
from unittest.mock import patch

from django.db import close_old_connections, connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import User
from orders.services import create_room

from .catalog import add_basic_options
from .models import Cafe, ManagementChange, Menu
from .services import link_seller
from .sheets import cafe_state, catalog_state, save_catalog


class SpreadsheetTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user("operator", is_operator=True)
        self.seller = User.objects.create_user(
            "seller", nickname="판매자", email="private@example.com"
        )
        self.second = User.objects.create_user("second")
        self.cafe = Cafe.objects.create(name="첫 카페", seller=self.seller, is_open=True)
        self.other = Cafe.objects.create(name="둘째 카페", seller=self.second, is_open=True)
        self.menu = Menu.objects.create(cafe=self.cafe, name="아메리카노", price=3000)
        add_basic_options(self.menu)
        self.client.force_login(self.operator)
        self.catalog_url = reverse("cafes:sheet-catalog", args=[self.cafe.pk])
        self.cafes_url = reverse("cafes:sheet-cafes")

    def post(self, url, data):
        return self.client.post(url, json.dumps(data), content_type="application/json")

    def new_menu(self, key="new-menu"):
        return {
            "key": key,
            "id": None,
            "name": "라떼",
            "category": "커피",
            "price": "4,500원",
            "is_active": True,
        }

    def new_option(self, menu_key="new-menu", key="new-option"):
        return {
            "key": key,
            "id": None,
            "menu_key": menu_key,
            "group_key": "new-group",
            "group_name": "사이즈",
            "mode": "single",
            "required": True,
            "name": "기본",
            "extra_price": 0,
            "is_default": True,
        }

    def test_operator_bulk_adds_menus_and_options_and_receives_registered_rows(self):
        data = catalog_state(self.cafe)
        data["menus"].append(self.new_menu())
        data["options"].append(self.new_option())
        data["menus"][0]["price"] = 3500
        response = self.post(self.catalog_url, data)
        self.assertEqual(response.status_code, 200, response.content)
        registered = response.json()
        menu = Menu.objects.get(cafe=self.cafe, name="라떼")
        self.assertEqual(menu.price, 4500)
        self.assertTrue(menu.option_groups.get(name="사이즈").choices.get(name="기본").is_default)
        self.assertEqual(len(registered["menus"]), 2)
        self.assertTrue(all(row["id"] for row in registered["options"]))
        self.assertNotEqual(registered["version"], data["version"])
        self.assertEqual(registered["menu_keys"]["new-menu"], f"menu-{menu.pk}")
        self.assertEqual(len(registered["history"]), 1)
        self.assertEqual(self.post(self.catalog_url, data).status_code, 409)
        self.assertEqual(Menu.objects.filter(name="라떼").count(), 1)
        self.assertEqual(self.post(self.catalog_url, registered).status_code, 200)
        self.assertEqual(ManagementChange.objects.count(), 1)

    def test_invalid_row_rolls_back_entire_batch_and_reports_cell(self):
        data = catalog_state(self.cafe)
        data["menus"][0]["price"] = 8000
        bad = self.new_menu()
        bad["price"] = -1
        data["menus"].append(bad)
        response = self.post(self.catalog_url, data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["errors"][0]["key"], "new-menu")
        self.assertEqual(response.json()["errors"][0]["field"], "price")
        self.menu.refresh_from_db()
        self.assertEqual(self.menu.price, 3000)
        self.assertEqual(Menu.objects.filter(cafe=self.cafe).count(), 1)
        self.assertFalse(ManagementChange.objects.exists())

    def test_single_default_and_duplicate_choice_validation_are_atomic(self):
        data = catalog_state(self.cafe)
        data["options"][1]["is_default"] = True
        data["menus"][0]["name"] = "저장되면 안 됨"
        response = self.post(self.catalog_url, data)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(any(error["field"] == "is_default" for error in response.json()["errors"]))
        self.menu.refresh_from_db()
        self.assertEqual(self.menu.name, "아메리카노")
        data = catalog_state(self.cafe)
        data["options"][1]["name"] = data["options"][0]["name"]
        self.assertEqual(self.post(self.catalog_url, data).status_code, 400)

    def test_multiple_selection_can_have_multiple_defaults(self):
        data = catalog_state(self.cafe)
        for row in data["options"][:2]:
            row["mode"] = "multiple"
            row["is_default"] = True
        self.assertEqual(self.post(self.catalog_url, data).status_code, 200)
        self.assertEqual(self.menu.option_groups.first().mode, "multiple")

    def test_stale_edit_conflicts_but_stock_change_does_not_overwrite_stock(self):
        data = catalog_state(self.cafe)
        self.menu.price = 3500
        self.menu.save()
        data["menus"][0]["price"] = 1000
        self.assertEqual(self.post(self.catalog_url, data).status_code, 409)
        data = catalog_state(self.cafe)
        self.menu.is_available = False
        self.menu.save()
        data["menus"][0]["price"] = 4000
        data["menus"][0]["is_available"] = True
        self.assertEqual(self.post(self.catalog_url, data).status_code, 200)
        self.menu.refresh_from_db()
        self.assertFalse(self.menu.is_available)
        self.assertEqual(self.menu.price, 4000)

    def test_snapshot_is_preserved_when_prices_options_and_visibility_change(self):
        room = create_room(user=self.operator, cafe_id=self.cafe.pk)
        before = copy.deepcopy(room.menu_snapshot)
        data = catalog_state(self.cafe)
        data["menus"][0]["price"] = 9000
        for row in data["options"]:
            row["_delete"] = True
        self.assertEqual(self.post(self.catalog_url, data).status_code, 200)
        self.assertFalse(self.menu.option_groups.exists())
        room.refresh_from_db()
        self.assertEqual(room.menu_snapshot, before)
        data = catalog_state(self.cafe)
        data["menus"][0]["_delete"] = True
        self.assertEqual(self.post(self.catalog_url, data).status_code, 200)
        self.menu.refresh_from_db()
        self.assertFalse(self.menu.is_active)
        self.assertTrue(Menu.objects.filter(pk=self.menu.pk).exists())
        response = Client().get(reverse("join", args=[room.token]))
        self.assertContains(response, "3000원")
        self.assertEqual(response.context["menu"], before)

    def test_hiding_menu_preserves_source_options_and_old_room(self):
        data = catalog_state(self.cafe)
        count = len(data["options"])
        data["menus"][0]["_delete"] = True
        data["menus"][0]["price"] = ""
        self.assertEqual(self.post(self.catalog_url, data).status_code, 200)
        self.assertEqual(len(catalog_state(self.cafe)["options"]), count)

    def test_foreign_ids_and_omitted_rows_cannot_change_other_catalogs(self):
        foreign = Menu.objects.create(cafe=self.other, name="다른 메뉴", price=1234)
        data = catalog_state(self.cafe)
        data["menus"][0]["id"] = foreign.pk
        self.assertEqual(self.post(self.catalog_url, data).status_code, 400)
        data = catalog_state(self.cafe)
        data["options"].pop()
        self.assertEqual(self.post(self.catalog_url, data).status_code, 400)
        data = catalog_state(self.cafe)
        data["options"][0]["group_key"] = "group-999999"
        self.assertEqual(self.post(self.catalog_url, data).status_code, 400)
        foreign.refresh_from_db()
        self.assertEqual(foreign.price, 1234)

    def test_cafe_bulk_add_edit_and_seller_swap_are_atomic_and_close_changed_cafes(self):
        data = cafe_state()
        data["rows"][0]["seller_code"] = str(self.second.account_code)
        data["rows"][1]["seller_code"] = str(self.seller.account_code)
        data["rows"][0]["phone"] = "02-111-2222"
        data["rows"].append(
            {
                "key": "new-cafe",
                "id": None,
                "name": "새 카페",
                "address": "서울",
                "phone": "",
                "seller_code": "",
                "is_open": True,
            }
        )
        response = self.post(self.cafes_url, data)
        self.assertEqual(response.status_code, 200, response.content)
        self.cafe.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.cafe.seller_id, self.second.pk)
        self.assertEqual(self.other.seller_id, self.seller.pk)
        self.assertFalse(self.cafe.is_open or self.other.is_open)
        self.assertEqual(self.cafe.access_events.count(), 2)
        self.assertFalse(Cafe.objects.get(name="새 카페").is_open)
        self.assertEqual(len(response.json()["rows"]), 3)

    def test_duplicate_unknown_seller_and_invalid_contact_row_save_nothing(self):
        for code in (str(self.seller.account_code), str(uuid.uuid4()), "bad-code"):
            data = cafe_state()
            data["rows"][0]["name"] = "변경 금지"
            data["rows"][1]["seller_code"] = code
            response = self.post(self.cafes_url, data)
            self.assertEqual(response.status_code, 400)
            self.assertTrue(
                any(error["field"] == "seller_code" for error in response.json()["errors"])
            )
            self.cafe.refresh_from_db()
            self.assertEqual(self.cafe.name, "첫 카페")
        self.assertFalse(ManagementChange.objects.exists())

    def test_unlink_keeps_cafe_and_history_and_unchanged_cafe_remains_open(self):
        data = cafe_state()
        data["rows"][0]["seller_code"] = ""
        self.assertEqual(self.post(self.cafes_url, data).status_code, 200)
        self.cafe.refresh_from_db()
        self.other.refresh_from_db()
        self.assertIsNone(self.cafe.seller_id)
        self.assertFalse(self.cafe.is_open)
        self.assertTrue(self.other.is_open)
        self.assertEqual(self.cafe.access_events.get().action, "unlinked")
        data = cafe_state()
        data["rows"][0]["_delete"] = True
        self.assertEqual(self.post(self.cafes_url, data).status_code, 400)
        self.assertEqual(Cafe.objects.count(), 2)

    def test_seller_lookup_returns_only_linking_information(self):
        response = self.client.get(
            reverse("cafes:sheet-seller"), {"code": str(self.seller.account_code)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"nickname", "cafe", "cafe_id"})
        self.assertNotContains(response, self.seller.email)
        self.assertEqual(
            self.client.get(reverse("cafes:sheet-seller"), {"code": "bad"}).status_code, 404
        )

    def test_role_csrf_method_and_payload_guards(self):
        for url in (self.cafes_url, self.catalog_url, reverse("cafes:sheet-seller")):
            self.assertEqual(Client().get(url).status_code, 302)
            member = Client()
            member.force_login(self.seller)
            self.assertEqual(member.get(url).status_code, 403)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.operator)
        self.assertEqual(
            protected.post(self.catalog_url, "{}", content_type="application/json").status_code, 403
        )
        self.assertEqual(
            self.client.post(self.catalog_url, "{", content_type="application/json").status_code,
            400,
        )
        self.assertEqual(self.post(self.catalog_url, []).status_code, 400)
        self.assertEqual(self.client.put(self.catalog_url).status_code, 405)
        response = self.client.get(self.catalog_url)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_table_initial_data_escapes_html_and_cafes_have_saved_rows(self):
        self.cafe.name = "</script><script>alert(1)</script>"
        self.cafe.save()
        response = self.client.get(reverse("accounts:operator"))
        self.assertContains(response, "data-sheet-app")
        self.assertNotContains(response, "</script><script>alert(1)</script>")
        self.assertContains(response, "\\u003C/script\\u003E")


class SheetLockTests(TransactionTestCase):
    def test_catalog_save_and_seller_link_can_finish_concurrently(self):
        actor = User.objects.create_user("parallel-operator", is_operator=True)
        cafe = Cafe.objects.create(name="동시 저장 카페", seller=actor)
        menu = Menu.objects.create(cafe=cafe, name="커피", price=3000)
        data = catalog_state(cafe)
        data["menus"][0]["price"] = 3500
        audit_ready, seller_locked = Event(), Event()
        thread_state = local()
        real_create = ManagementChange.objects.create
        real_lock = Cafe.objects.select_for_update

        def before_audit(**values):
            audit_ready.set()
            self.assertTrue(seller_locked.wait(timeout=5))
            return real_create(**values)

        def cafe_lock(*args, **kwargs):
            if getattr(thread_state, "seller", False):
                seller_locked.set()
            return real_lock(*args, **kwargs)

        def edit_catalog():
            close_old_connections()
            try:
                save_catalog(actor=actor, cafe_id=cafe.pk, data=data)
            finally:
                connections.close_all()

        def connect_seller():
            close_old_connections()
            try:
                self.assertTrue(audit_ready.wait(timeout=5))
                thread_state.seller = True
                link_seller(actor=actor, cafe_id=cafe.pk, account_code=actor.account_code)
            finally:
                connections.close_all()

        with (
            patch.object(ManagementChange.objects, "create", side_effect=before_audit),
            patch.object(Cafe.objects, "select_for_update", side_effect=cafe_lock),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            jobs = [pool.submit(edit_catalog), pool.submit(connect_seller)]
            for job in jobs:
                job.result(timeout=10)
        menu.refresh_from_db()
        cafe.refresh_from_db()
        self.assertEqual(menu.price, 3500)
        self.assertEqual(cafe.seller_id, actor.pk)
        self.assertEqual(ManagementChange.objects.count(), 1)
