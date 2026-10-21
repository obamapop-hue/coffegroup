from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import OperationalError, connection
from django.test import SimpleTestCase, TransactionTestCase
from django.urls import reverse


class HomeTests(SimpleTestCase):
    def test_home_is_korean_and_does_not_offer_working_login(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<html lang="ko">')
        self.assertContains(response, "커피 주문,")
        self.assertContains(response, "카카오 로그인 연결을 준비하고 있어요.")
        self.assertContains(response, 'disabled aria-describedby="service-status"', count=1)
        self.assertContains(response, reverse("accounts:entry-seller"))
        self.assertContains(response, reverse("accounts:entry-operator"))

    def test_liveness_does_not_require_database(self):
        response = self.client.get(reverse("health-live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIn("no-store", response.headers["Cache-Control"])

    @patch("core.views.connection")
    def test_readiness_fails_without_leaking_connection_details(self, database):
        database.cursor.side_effect = OperationalError("private-connection-detail")
        response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotContains(response, "private-connection-detail", status_code=503)


class DatabaseReadinessTests(TransactionTestCase):
    def test_readiness_checks_real_postgresql(self):
        self.assertEqual(connection.vendor, "postgresql")
        response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})

    def test_write_read_command_is_repeatable_and_leaves_no_probe(self):
        for _ in range(2):
            output = StringIO()
            call_command("check_database", stdout=output)
            self.assertIn("검증 데이터 제거 완료", output.getvalue())
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('pg_temp.coffegroup_database_probe')")
                self.assertEqual(cursor.fetchone(), (None,))
