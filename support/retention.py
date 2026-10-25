"""Remove answered inquiries one calendar year after the latest reply."""

from django.db import transaction
from django.utils import timezone

from .models import Ticket


def one_year_before(now):
    try:
        return now.replace(year=now.year - 1)
    except ValueError:
        return now.replace(year=now.year - 1, day=28)


def delete_expired_inquiries(*, now=None):
    cutoff = one_year_before(now or timezone.now())
    count = 0
    while True:
        with transaction.atomic():
            tickets = list(
                Ticket.objects.select_for_update(skip_locked=True)
                .filter(status=Ticket.Status.ANSWERED, updated_at__lte=cutoff)
                .order_by("pk")[:100]
            )
            if not tickets:
                return count
            for ticket in tickets:
                ticket.delete()
                count += 1
