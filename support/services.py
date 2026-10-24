from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Message, Ticket


def own_tickets(user, guest_hash):
    scope = Q(pk__in=[])
    if user.is_authenticated and user.is_active:
        scope |= Q(owner_id=user.pk)
    if guest_hash:
        scope |= Q(guest_hash=guest_hash)
    return Ticket.objects.filter(scope)


def validate_body(body):
    if not isinstance(body, str) or not body.strip() or len(body.strip()) > 3000 or "\x00" in body:
        raise ValidationError("문의 내용은 1~3,000자로 적어 주세요.")
    return body.strip()


@transaction.atomic
def create_ticket(*, user, guest_hash, subject, body, request_key):
    body = validate_body(body)
    if not isinstance(subject, str) or not subject.strip() or len(subject.strip()) > 100:
        raise ValidationError("제목은 1~100자로 적어 주세요.")
    owner = user if user.is_authenticated and user.is_active else None
    if owner is None and not guest_hash:
        raise PermissionDenied
    ticket, created = Ticket.objects.get_or_create(
        request_key=request_key,
        defaults={
            "owner": owner,
            "guest_hash": guest_hash if owner is None else "",
            "subject": subject.strip(),
        },
    )
    if not created:
        if not own_tickets(user, guest_hash).filter(pk=ticket.pk).exists():
            raise PermissionDenied
        if ticket.subject != subject.strip() or ticket.messages.first().body != body:
            raise ValidationError(
                "이미 접수한 문의예요. 새 문의를 작성하려면 화면을 다시 열어 주세요."
            )
        return ticket
    Message.objects.create(
        ticket=ticket, sender=Message.Sender.REQUESTER, body=body, request_key=request_key
    )
    return ticket


@transaction.atomic
def add_message(*, user, guest_hash, ticket_id, body, request_key, as_operator=False):
    ticket = Ticket.objects.select_for_update().get(pk=ticket_id)
    if as_operator:
        if not user.is_authenticated or not user.is_active or not user.is_operator:
            raise PermissionDenied
    elif not own_tickets(user, guest_hash).filter(pk=ticket.pk).exists():
        raise PermissionDenied
    body = validate_body(body)
    sender = Message.Sender.OPERATOR if as_operator else Message.Sender.REQUESTER
    message, created = Message.objects.get_or_create(
        ticket=ticket, request_key=request_key, defaults={"sender": sender, "body": body}
    )
    if not created:
        if message.sender != sender or message.body != body:
            raise ValidationError("이미 등록한 내용이에요. 화면을 새로 열어 주세요.")
        return ticket
    ticket.status = Ticket.Status.ANSWERED if as_operator else Ticket.Status.WAITING
    ticket.updated_at = timezone.now()
    ticket.save(update_fields=["status", "updated_at"])
    return ticket
