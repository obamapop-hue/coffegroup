import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Ticket(models.Model):
    class Status(models.TextChoices):
        WAITING = "waiting", "답변 대기"
        ANSWERED = "answered", "답변 완료"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    guest_hash = models.CharField(max_length=64, blank=True)
    request_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    subject = models.CharField(max_length=100)
    status = models.CharField(max_length=12, choices=Status, default=Status.WAITING)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-updated_at", "-pk"]
        indexes = [
            models.Index(fields=["owner", "-updated_at"]),
            models.Index(fields=["guest_hash", "-updated_at"]),
        ]


class Message(models.Model):
    class Sender(models.TextChoices):
        REQUESTER = "requester", "내 문의"
        OPERATOR = "operator", "영차 답변"

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    sender = models.CharField(max_length=12, choices=Sender)
    body = models.TextField(max_length=3000)
    request_key = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["ticket", "request_key"], name="one_support_message_per_request"
            )
        ]
