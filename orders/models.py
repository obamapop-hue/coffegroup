import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def room_token():
    return secrets.token_urlsafe(24)


class Room(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "주문중"
        CLOSED = "closed", "마감"
        EXPIRED = "expired", "주문서 폐기"
        DELETED = "deleted", "주문서 삭제"
        PENDING = "pending", "주문 수락 대기"
        REJECTED = "rejected", "주문 거절"
        CANCELLED = "cancelled", "주문 취소"
        ACCEPTED = "accepted", "주문 수락"

    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="rooms"
    )
    cafe = models.ForeignKey("cafes.Cafe", on_delete=models.PROTECT, related_name="rooms")
    token = models.CharField(max_length=40, unique=True, default=room_token, editable=False)
    cafe_snapshot = models.JSONField(editable=False)
    menu_snapshot = models.JSONField(editable=False)
    status = models.CharField(max_length=16, choices=Status, default=Status.OPEN)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    expires_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True)
    ready_at = models.DateTimeField(null=True)
    rejection_reason = models.CharField(max_length=30, blank=True, default="", db_default="")

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["organizer", "-created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="room_deadline_after_creation",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=["accepted", "rejected", "cancelled"], resolved_at__isnull=False
                    )
                    | models.Q(
                        status__in=["open", "closed", "expired", "deleted", "pending"],
                        resolved_at__isnull=True,
                    )
                ),
                name="room_resolution_matches_status",
            ),
            models.CheckConstraint(
                condition=(
                    (models.Q(status="rejected") & ~models.Q(rejection_reason=""))
                    | (~models.Q(status="rejected") & models.Q(rejection_reason=""))
                ),
                name="rejected_room_has_reason",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="accepted", ready_at__isnull=False)
                    | (~models.Q(status="accepted") & models.Q(ready_at__isnull=True))
                ),
                name="accepted_room_has_ready_time",
            ),
            models.CheckConstraint(
                condition=models.Q(ready_at__isnull=True)
                | models.Q(ready_at__gt=models.F("resolved_at")),
                name="ready_time_after_acceptance",
            ),
        ]


class RoomEvent(models.Model):
    class Action(models.TextChoices):
        CREATED = "created", "주문방 생성"
        EXPIRED = "expired", "주문서 폐기"
        DELETED = "deleted", "주문서 삭제"
        CLOSED = "closed", "참여 마감"
        SENT = "sent", "최종 주문 전송"
        CANCELLED = "cancelled", "총무 주문 취소"
        TIMED_OUT = "timed_out", "주문 거절 · 5분 미응답"
        ACCEPTED = "accepted", "판매자 주문 수락"
        SELLER_REJECTED = "seller_rejected", "판매자 주문 거절"

    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=20, choices=Action)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["room", "action"], name="one_room_event_per_action")
        ]


class Submission(models.Model):
    class Source(models.TextChoices):
        PARTICIPANT = "participant", "참여자"
        ORGANIZER = "organizer", "총무"

    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="submissions")
    nickname = models.CharField(max_length=30)
    source = models.CharField(max_length=12, choices=Source)
    request_key = models.UUIDField(editable=False)
    items = models.JSONField()
    total_quantity = models.PositiveIntegerField()
    total_amount = models.PositiveBigIntegerField()
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    deleted_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["room", "request_key"], name="one_submission_per_request"
            ),
            models.CheckConstraint(
                condition=models.Q(total_quantity__gt=0), name="order_has_quantity"
            ),
        ]


class SubmissionRevision(models.Model):
    submission = models.ForeignKey(Submission, on_delete=models.PROTECT, related_name="revisions")
    version = models.PositiveIntegerField()
    action = models.CharField(
        max_length=10, choices=[("created", "제출"), ("edited", "수정"), ("deleted", "삭제")]
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    snapshot = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "version"], name="one_submission_revision"
            )
        ]


class FinalOrder(models.Model):
    """Immutable cafe-facing aggregate; no participant names or submission IDs."""

    room = models.OneToOneField(Room, on_delete=models.PROTECT, related_name="final_order")
    organizer_nickname = models.CharField(max_length=150)
    items = models.JSONField()
    total_quantity = models.PositiveIntegerField()
    total_amount = models.PositiveBigIntegerField()
    sent_at = models.DateTimeField(default=timezone.now)
    respond_by = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(total_quantity__gt=0), name="final_order_has_items"
            ),
            models.CheckConstraint(
                condition=models.Q(respond_by=models.F("sent_at") + timedelta(minutes=5)),
                name="final_order_five_minute_deadline",
            ),
        ]
