import uuid
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User

from .models import Message, Ticket


class SupportTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("help-owner")
        self.other = User.objects.create_user("help-other")
        self.operator = User.objects.create_user("help-operator", is_operator=True)
        self.member = Client()
        self.member.force_login(self.owner)
        self.stranger = Client()
        self.stranger.force_login(self.other)
        self.admin = Client()
        self.admin.force_login(self.operator)
        self.guest = Client()

    def post_ticket(self, client, **changes):
        response = client.get(reverse("support:inbox"))
        data = {
            "subject": "카페 주문 문의",
            "body": "비공개 문의 내용",
            "request_key": str(response.context["form"]["request_key"].value()),
        }
        data.update(changes)
        return client.post(reverse("support:inbox"), data), data

    def post_message(self, client, ticket, text, operator=False):
        route = "support:operator-detail" if operator else "support:detail"
        url = reverse(route, args=[ticket.pk])
        response = client.get(url)
        data = {"body": text, "request_key": str(response.context["form"]["request_key"].value())}
        return client.post(url, data), data

    def test_guest_can_submit_without_registration_and_only_same_browser_can_read(self):
        response, _ = self.post_ticket(self.guest)
        self.assertEqual(response.status_code, 302)
        ticket = Ticket.objects.get()
        self.assertIsNone(ticket.owner)
        self.assertEqual(len(ticket.guest_hash), 64)
        self.assertNotEqual(ticket.guest_hash, self.guest.session["support_access"])
        url = reverse("support:detail", args=[ticket.pk])
        self.assertContains(self.guest.get(url), "비공개 문의 내용")
        self.assertEqual(Client().get(url).status_code, 404)
        self.assertEqual(self.stranger.get(url).status_code, 404)
        self.assertNotContains(self.stranger.get(reverse("support:inbox")), "비공개 문의 내용")

    def test_member_history_returns_after_relogin_and_is_not_visible_to_other_accounts(self):
        self.post_ticket(self.member)
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.owner, self.owner)
        url = reverse("support:detail", args=[ticket.pk])
        self.member.logout()
        self.member.force_login(self.owner)
        self.assertContains(self.member.get(url), "비공개 문의 내용")
        self.assertEqual(self.stranger.get(url).status_code, 404)
        self.assertEqual(self.admin.get(url).status_code, 404)
        self.assertContains(
            self.admin.get(reverse("support:operator-detail", args=[ticket.pk])), "비공개 문의 내용"
        )

    def test_reply_and_follow_up_change_status_without_rewriting_history(self):
        self.post_ticket(self.guest)
        ticket = Ticket.objects.get()
        self.post_message(self.admin, ticket, "답변입니다", operator=True)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "answered")
        response = self.guest.get(reverse("support:detail", args=[ticket.pk]))
        self.assertContains(response, "영차 답변")
        self.assertContains(response, "답변입니다")
        self.post_message(self.guest, ticket, "추가로 문의합니다")
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "waiting")
        self.assertEqual(
            list(ticket.messages.values_list("sender", flat=True)),
            ["requester", "operator", "requester"],
        )

    def test_repeated_submission_and_reply_are_saved_once_and_do_not_reset_newer_state(self):
        _, data = self.post_ticket(self.member)
        self.member.post(reverse("support:inbox"), data)
        self.assertEqual(Ticket.objects.count(), 1)
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.messages.count(), 1)
        _, reply = self.post_message(self.admin, ticket, "답변", operator=True)
        self.post_message(self.member, ticket, "추가 문의")
        self.admin.post(reverse("support:operator-detail", args=[ticket.pk]), reply)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "waiting")
        self.assertEqual(ticket.messages.count(), 3)

    def test_guessed_duplicate_request_key_cannot_reveal_another_ticket(self):
        _, data = self.post_ticket(self.member)
        self.stranger.get(reverse("support:inbox"))
        response = self.stranger.post(reverse("support:inbox"), data)
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "비공개 문의 내용", status_code=403)
        self.assertEqual(Ticket.objects.count(), 1)

    def test_only_operator_can_read_and_answer_management_routes(self):
        self.post_ticket(self.member)
        ticket = Ticket.objects.get()
        url = reverse("support:operator-detail", args=[ticket.pk])
        self.assertEqual(Client().get(url).status_code, 302)
        self.assertEqual(self.member.get(url).status_code, 403)
        self.assertEqual(
            self.member.post(
                url, {"body": "관리자 사칭", "request_key": str(uuid.uuid4()), "operator": True}
            ).status_code,
            403,
        )
        self.assertEqual(ticket.messages.count(), 1)

    def test_post_requires_csrf_and_invalid_or_empty_body_is_not_saved(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get(reverse("support:inbox"))
        data = {
            "subject": "문의",
            "body": "내용",
            "request_key": str(response.context["form"]["request_key"].value()),
        }
        self.assertEqual(client.post(reverse("support:inbox"), data).status_code, 403)
        data["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
        self.assertEqual(client.post(reverse("support:inbox"), data).status_code, 302)
        self.assertEqual(Ticket.objects.count(), 1)
        response, _ = self.post_ticket(self.member, body=" ")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Ticket.objects.count(), 1)

    def test_user_text_is_escaped_in_both_customer_and_operator_pages(self):
        response, _ = self.post_ticket(
            self.member, subject="<script>bad()</script>", body="<img src=x onerror=bad()>"
        )
        ticket = Ticket.objects.get()
        for client, route in [
            (self.member, "support:detail"),
            (self.admin, "support:operator-detail"),
        ]:
            page = client.get(reverse(route, args=[ticket.pk]))
            self.assertNotContains(page, "<script>bad()</script>")
            self.assertContains(page, "&lt;img src=x onerror=bad()&gt;")

    def test_message_failure_rolls_back_new_ticket(self):
        self.member.get(reverse("support:inbox"))
        with (
            patch(
                "support.services.Message.objects.create", side_effect=RuntimeError("interrupted")
            ),
            self.assertRaises(RuntimeError),
        ):
            self.post_ticket(self.member)
        self.assertFalse(Ticket.objects.exists())
        self.assertFalse(Message.objects.exists())

    def test_pagination_keeps_older_tickets_accessible_and_lists_are_not_cached(self):
        for _ in range(21):
            self.post_ticket(self.member)
        first = self.member.get(reverse("support:inbox"))
        second = self.member.get(reverse("support:inbox") + "?page=2")
        self.assertEqual(len(first.context["tickets"]), 20)
        self.assertEqual(len(second.context["tickets"]), 1)
        self.assertIn("no-store", first["Cache-Control"])
