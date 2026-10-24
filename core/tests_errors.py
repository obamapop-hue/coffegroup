from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase, override_settings

from core import error_views


@override_settings(
    DEBUG=False,
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class ErrorPageTests(SimpleTestCase):
    def test_error_pages_are_korean_hide_details_and_render_without_database_access(self):
        request = RequestFactory().get("/missing/")
        request.user = AnonymousUser()
        private = RuntimeError("private-error-detail")
        for response, status in [
            (error_views.bad_request(request, private), 400),
            (error_views.permission_denied(request, private), 403),
            (error_views.not_found(request, private), 404),
            (error_views.server_error(request), 500),
        ]:
            with self.subTest(status=status):
                self.assertEqual(response.status_code, status)
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertIn('lang="ko"', response.content.decode())
                self.assertNotIn("private-error-detail", response.content.decode())
                self.assertIn("영차", response.content.decode())
