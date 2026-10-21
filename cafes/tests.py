from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from .catalog import add_basic_options
from .forms import ChoicesFormSet
from .models import Cafe, Menu, OptionGroup


class CatalogTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", is_operator=True)
        self.seller = User.objects.create_user("seller")
        self.cafe = Cafe.objects.create(name="카페", seller=self.seller)
        self.menu = Menu.objects.create(cafe=self.cafe, name="아메리카노", price=3000)
        add_basic_options(self.menu)

    def test_operator_manages_contacts_menu_and_default_options(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("cafes:manage", args=[self.cafe.pk]),
            {"name": "새 카페", "phone": "02-111-2222", "address": "서울 주소"},
        )
        self.assertEqual(response.status_code, 302)
        self.cafe.refresh_from_db()
        self.assertEqual(self.cafe.phone, "02-111-2222")
        response = self.client.post(
            reverse("cafes:menu-create", args=[self.cafe.pk]),
            {"name": "라떼", "category": "커피", "price": 4500, "is_active": "on"},
        )
        self.assertEqual(response.status_code, 302)
        menu = Menu.objects.get(name="라떼")
        self.assertEqual(menu.option_groups.count(), 2)
        self.assertTrue(menu.option_groups.get(name="샷 추가").choices.get(name="샷 +0").is_default)
        response = self.client.post(
            reverse("cafes:menu-edit", args=[self.cafe.pk, menu.pk]),
            {"name": "라떼", "price": -100, "is_active": "on"},
        )
        self.assertEqual(response.status_code, 400)
        menu.refresh_from_db()
        self.assertEqual(menu.price, 4500)

    def choice_data(self, defaults=(True, False)):
        return {
            "choices-TOTAL_FORMS": "2",
            "choices-INITIAL_FORMS": "0",
            "choices-MIN_NUM_FORMS": "0",
            "choices-MAX_NUM_FORMS": "100",
            "choices-0-name": "기본",
            "choices-0-extra_price": "0",
            "choices-0-is_default": "on" if defaults[0] else "",
            "choices-1-name": "추가",
            "choices-1-extra_price": "500",
            "choices-1-is_default": "on" if defaults[1] else "",
        }

    def test_single_default_validation_and_multiple_defaults(self):
        for mode, expected in (("single", False), ("multiple", True)):
            group = OptionGroup(menu=self.menu, name="테스트", mode=mode)
            formset = ChoicesFormSet(self.choice_data((True, True)), instance=group)
            self.assertEqual(formset.is_valid(), expected)
        group = OptionGroup(menu=self.menu, name="테스트")
        formset = ChoicesFormSet(self.choice_data(), instance=group)
        self.assertTrue(formset.is_valid())

    def test_option_post_scopes_children_and_saves_price_and_default(self):
        self.client.force_login(self.owner)
        data = dict(self.choice_data(), name="샷 추가 테스트", mode="single", required="on")
        response = self.client.post(
            reverse("cafes:option-create", args=[self.cafe.pk, self.menu.pk]), data
        )
        self.assertEqual(response.status_code, 302)
        group = self.menu.option_groups.get(name="샷 추가 테스트")
        self.assertEqual(group.choices.get(name="추가").extra_price, 500)
        other = Cafe.objects.create(name="다른 카페")
        response = self.client.post(
            reverse("cafes:option-delete", args=[other.pk, self.menu.pk, group.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(OptionGroup.objects.filter(pk=group.pk).exists())

    def test_seller_only_changes_own_stock_and_cannot_change_price_or_catalog(self):
        self.client.force_login(self.seller)
        stock_url = reverse("cafes:stock", args=[self.menu.pk])
        self.assertEqual(
            self.client.post(stock_url, {"available": "no", "price": 0}).status_code, 302
        )
        self.menu.refresh_from_db()
        self.assertFalse(self.menu.is_available)
        self.assertEqual(self.menu.price, 3000)
        self.assertEqual(
            self.client.get(reverse("cafes:manage", args=[self.cafe.pk])).status_code, 403
        )
        other = Cafe.objects.create(name="다른 카페")
        menu = Menu.objects.create(cafe=other, name="다른 메뉴", price=1)
        self.assertEqual(
            self.client.post(
                reverse("cafes:stock", args=[menu.pk]), {"available": "no"}
            ).status_code,
            404,
        )
        self.assertEqual(self.client.post(stock_url, {"available": "invalid"}).status_code, 400)
