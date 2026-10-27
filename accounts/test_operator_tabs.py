from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User


class OperatorTabsTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user("tab-operator", is_operator=True)
        self.member = User.objects.create_user("tab-member")
        self.client.force_login(self.operator)

    def test_cafe_and_history_tabs_do_not_render_each_others_workspaces(self):
        response = self.client.get(reverse("accounts:operator"))
        self.assertContains(response, "data-sheet-app")
        self.assertNotContains(response, 'id="operator-history-title"')
        with patch("accounts.views.cafe_state", side_effect=AssertionError("unused")):
            response = self.client.get(reverse("accounts:operator"), {"tab": "orders"})
        self.assertContains(response, 'id="operator-history-title"')
        self.assertNotContains(response, "data-sheet-app")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_shared_navigation_selects_the_current_management_category(self):
        for name, selected in [
            ("accounts:members", "회원 관리"),
            ("accounts:privacy-preview", "개인정보 처리방침"),
            ("support:operator-inbox", "사용자 문의"),
        ]:
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertContains(response, 'aria-label="운영자 관리 분류"')
                self.assertContains(response, f'aria-current="page">{selected}</a>')

    def test_history_tab_is_operator_only_and_unknown_tab_falls_back_to_cafes(self):
        url = reverse("accounts:operator") + "?tab=orders"
        self.assertEqual(Client().get(url).status_code, 302)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.operator)
        self.assertContains(
            self.client.get(reverse("accounts:operator"), {"tab": "unknown"}), "data-sheet-app"
        )
