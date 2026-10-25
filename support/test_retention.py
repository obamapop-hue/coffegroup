from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from .models import Message, Ticket
from .retention import delete_expired_inquiries, one_year_before


class InquiryRetentionTests(TestCase):
    def test_regular_deadline_worker_also_removes_due_inquiries(self):
        now = datetime(2026, 10, 25, 8, 0, tzinfo=UTC)
        Ticket.objects.create(subject="old", status="answered", updated_at=one_year_before(now))
        with patch("support.retention.timezone.now", return_value=now):
            call_command("run_deadlines", once=True, stdout=StringIO())
        self.assertFalse(Ticket.objects.exists())

    def test_delete_only_answered_threads_one_calendar_year_after_latest_answer(self):
        now = datetime(2026, 10, 25, 8, 0, tzinfo=UTC)
        cutoff = one_year_before(now)
        old = Ticket.objects.create(subject="old", status="answered", updated_at=cutoff)
        Message.objects.create(ticket=old, sender="operator", body="reply")
        pending = Ticket.objects.create(subject="waiting", updated_at=cutoff - timedelta(days=30))
        fresh = Ticket.objects.create(
            subject="new reply", status="answered", updated_at=cutoff + timedelta(seconds=1)
        )
        self.assertEqual(delete_expired_inquiries(now=now), 1)
        self.assertEqual(set(Ticket.objects.values_list("pk", flat=True)), {pending.pk, fresh.pk})
        self.assertFalse(Message.objects.exists())
        self.assertEqual(delete_expired_inquiries(now=now), 0)

    def test_leap_year_anniversary_does_not_crash_or_use_365_days_for_all_years(self):
        leap = datetime(2024, 2, 29, 8, 0, tzinfo=UTC)
        self.assertEqual(one_year_before(leap), leap.replace(year=2023, day=28))
        later = datetime(2025, 3, 1, 8, 0, tzinfo=UTC)
        self.assertEqual(one_year_before(later), later.replace(year=2024))
