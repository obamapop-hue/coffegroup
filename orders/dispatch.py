"""Final confirmation, immutable cafe-facing order creation and cancellation."""

import hashlib
import json
from datetime import timedelta

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.ordering import ordering_member
from cafes.models import Cafe

from .collection import editable_items, normalize_order, require_owner
from .models import FinalOrder, Room, RoomEvent
from .services import expire_locked

CONFIRM_SALT = "orders.final.confirmation"


class ConfirmationChanged(Exception):
    pass


def aggregate_order(room):
    submissions = list(room.submissions.filter(deleted_at__isnull=True).order_by("pk"))
    grouped = {}
    for submission in submissions:
        _, items = normalize_order(
            room.menu_snapshot, submission.nickname, editable_items(submission)
        )
        for item in items:
            key = json.dumps(
                {"menu_id": item["menu_id"], "options": item["options"]}, sort_keys=True
            )
            if key in grouped:
                grouped[key]["quantity"] += item["quantity"]
                grouped[key]["line_total"] += item["line_total"]
            else:
                grouped[key] = item
    items = list(grouped.values())
    if not items:
        raise ValidationError("전송할 주문이 없어요. 주문을 먼저 추가해 주세요.")
    payload = {
        "organizer_nickname": room.organizer.display_name,
        "items": items,
        "total_quantity": sum(item["quantity"] for item in items),
        "total_amount": sum(item["line_total"] for item in items),
    }
    digest = hashlib.sha256(
        json.dumps(
            {
                "room": room.pk,
                "payload": payload,
                "versions": [(item.pk, item.version) for item in submissions],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return payload, digest


@transaction.atomic
def preview_order(*, user, room_id):
    # This read-only token preview can run while the detail view holds a room lock.
    ordering_member(user, lock=False)
    room = Room.objects.select_for_update().get(pk=room_id)
    require_owner(room, user)
    if expire_locked(room, timezone.now()):
        return None
    if room.status != Room.Status.CLOSED:
        raise ValidationError("참여를 마감한 뒤 주문서를 전송해 주세요.")
    payload, digest = aggregate_order(room)
    token = signing.dumps({"room": room.pk, "digest": digest}, salt=CONFIRM_SALT)
    return room, payload, token


@transaction.atomic
def send_order(*, user, room_id, confirmation_token):
    ordering_member(user)
    room = Room.objects.select_for_update().get(pk=room_id)
    require_owner(room, user)
    if expire_locked(room, timezone.now()):
        return None
    try:
        confirmation = signing.loads(confirmation_token, salt=CONFIRM_SALT, max_age=600)
        if confirmation["room"] != room.pk or not isinstance(confirmation["digest"], str):
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError, KeyError):
        raise ValidationError("주문서를 다시 열어 최신 내용을 확인한 뒤 전송해 주세요.") from None
    existing = FinalOrder.objects.filter(room=room).first()
    if existing:
        return existing
    if room.status != Room.Status.CLOSED:
        raise ValidationError("참여를 마감한 뒤 주문서를 전송해 주세요.")
    payload, digest = aggregate_order(room)
    if confirmation["digest"] != digest:
        raise ConfirmationChanged
    # Room first, then cafe: serialize with explicit seller logout/unlink.
    cafe = Cafe.objects.select_for_update().get(pk=room.cafe_id)
    now = timezone.now()
    if expire_locked(room, now):
        return None
    if not cafe.is_open or not cafe.seller_id:
        raise ValidationError("카페가 영업종료 상태예요. 영업중일 때 전송할 수 있어요.")
    final_order = FinalOrder.objects.create(
        room=room,
        **payload,
        sent_at=now,
        respond_by=now + timedelta(minutes=5),
    )
    room.status = Room.Status.PENDING
    room.save(update_fields=["status"])
    RoomEvent.objects.create(room=room, action=RoomEvent.Action.SENT, created_at=now)
    return final_order


@transaction.atomic
def cancel_order(*, user, room_id):
    room = Room.objects.select_for_update().get(pk=room_id)
    require_owner(room, user)
    now = timezone.now()
    if expire_locked(room, now) or room.status != Room.Status.PENDING:
        return False
    room.status = Room.Status.CANCELLED
    room.resolved_at = now
    room.save(update_fields=["status", "resolved_at"])
    RoomEvent.objects.create(room=room, action=RoomEvent.Action.CANCELLED, created_at=now)
    return True
