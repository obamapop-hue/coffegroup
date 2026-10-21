"""Cafe-scoped decisions and an explicit cafe-facing aggregate projection."""

import hashlib
from datetime import datetime, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone

from cafes.models import Cafe

from .models import FinalOrder, Room, RoomEvent
from .services import expire_due_rooms, expire_locked


def ready_time_choices(*, now=None):
    now = timezone.localtime(now or timezone.now())
    first = now.replace(second=0, microsecond=0) + timedelta(minutes=5 - now.minute % 5)
    return [
        (
            slot.isoformat(),
            slot.strftime("%H:%M") if slot.date() == now.date() else slot.strftime("내일 %H:%M"),
        )
        for slot in (first + timedelta(minutes=5 * index) for index in range(288))
    ]


def validate_ready_time(value, *, now):
    if not isinstance(value, datetime) or not timezone.is_aware(value):
        raise ValidationError("완료 예정시간을 선택해 주세요.")
    local = timezone.localtime(value)
    if local.minute % 5 or local.second or local.microsecond:
        raise ValidationError("완료 예정시간은 5분 단위로 선택해 주세요.")
    if value <= now:
        raise ValidationError("현재 시각 이후의 완료 예정시간을 선택해 주세요.")


@transaction.atomic
def decide_order(*, user, room_id, action, ready_at=None, reason=""):
    # Match dispatch/cancel/deadline lock order; recheck assignment under the cafe lock.
    room = Room.objects.select_for_update().get(pk=room_id)
    cafe = Cafe.objects.select_for_update().get(pk=room.cafe_id)
    if not user.is_authenticated or not user.is_active or cafe.seller_id != user.pk:
        raise PermissionDenied
    if action not in ("accept", "reject"):
        raise ValidationError("주문 처리 방법을 확인해 주세요.")
    now = timezone.now()
    if expire_locked(room, now) or room.status != Room.Status.PENDING:
        return False
    if not cafe.is_open:
        raise ValidationError("판매자로 로그인해 영업을 시작한 뒤 처리해 주세요.")
    if action == "accept":
        validate_ready_time(ready_at, now=now)
        room.status = Room.Status.ACCEPTED
        room.ready_at = ready_at
        event = RoomEvent.Action.ACCEPTED
    else:
        if not isinstance(reason, str):
            raise ValidationError("거절 사유를 1~30자로 입력해 주세요.")
        reason = reason.strip()
        if not 1 <= len(reason) <= 30 or any(ord(char) < 32 for char in reason):
            raise ValidationError("거절 사유를 1~30자로 입력해 주세요.")
        room.status = Room.Status.REJECTED
        room.rejection_reason = reason
        event = RoomEvent.Action.SELLER_REJECTED
    room.resolved_at = now
    room.save(update_fields=["status", "ready_at", "resolved_at", "rejection_reason"])
    RoomEvent.objects.create(room=room, action=event, created_at=now)
    return True


def seller_version(cafe):
    stats = RoomEvent.objects.filter(
        room__cafe_id=cafe.pk, room__final_order__isnull=False
    ).aggregate(count=Count("pk"), latest=Max("pk"))
    return hashlib.sha256(
        f"{cafe.pk}:{cafe.is_open}:{stats['count']}:{stats['latest']}".encode()
    ).hexdigest()


def seller_order(final):
    """Never pass a Room, organizer account, or participant submission to a seller template."""
    room = final.room
    return {
        "id": room.pk,
        "organizer_nickname": final.organizer_nickname,
        "sent_at": final.sent_at,
        "respond_by": final.respond_by,
        "total_quantity": final.total_quantity,
        "total_amount": final.total_amount,
        "status": room.status,
        "status_label": room.get_status_display(),
        "resolved_at": room.resolved_at,
        "ready_at": room.ready_at,
        "rejection_reason": room.rejection_reason,
        "items": [
            {
                "name": item["menu_name"],
                "quantity": item["quantity"],
                "amount": item["line_total"],
                "options": [
                    {
                        "group": group["group_name"],
                        "name": choice["name"],
                        "quantity": choice.get("quantity", 1),
                    }
                    for group in item["options"]
                    for choice in group["choices"]
                ],
            }
            for item in final.items
        ],
    }


def seller_context(*, user, page=1, accepted_page=1):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied
    cafe = Cafe.objects.filter(seller=user).first()
    if cafe is None:
        raise PermissionDenied
    expire_due_rooms()
    # Read before the list: a concurrent new event must cause the next poll to refresh.
    version = seller_version(cafe)
    orders = FinalOrder.objects.filter(room__cafe=cafe).select_related("room")
    history = Paginator(
        orders.filter(room__status__in=[Room.Status.REJECTED, Room.Status.CANCELLED]).order_by(
            "-room__resolved_at", "-pk"
        ),
        20,
    ).get_page(page)
    history.object_list = [seller_order(order) for order in history.object_list]
    accepted = Paginator(
        orders.filter(room__status=Room.Status.ACCEPTED).order_by("-room__resolved_at", "-pk"),
        20,
    ).get_page(accepted_page)
    accepted.object_list = [seller_order(order) for order in accepted.object_list]
    return {
        "cafe": cafe,
        "menus": cafe.menus.filter(is_active=True),
        "pending_orders": [
            seller_order(order)
            for order in orders.filter(room__status=Room.Status.PENDING).order_by("sent_at", "pk")
        ],
        "accepted_orders": accepted,
        "order_history": history,
        "seller_version": version,
        "ready_choices": ready_time_choices(),
    }
