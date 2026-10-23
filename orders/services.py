from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone

from cafes.catalog import snapshot_menu
from cafes.models import Cafe

from .models import OrderNotification, Room, RoomEvent
from .notifications import notify_organizer

UNSENT = (Room.Status.OPEN, Room.Status.CLOSED)
ACTIVE = (*UNSENT, Room.Status.PENDING, Room.Status.ACCEPTED)


@transaction.atomic
def create_room(*, user, cafe_id):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied
    cafe = Cafe.objects.select_for_update().get(pk=cafe_id)
    if not cafe.is_open or not cafe.seller_id:
        raise ValidationError("영업종료 카페는 링크를 만들 수 없어요.")
    menu = snapshot_menu(cafe)
    if not menu:
        raise ValidationError("현재 주문 가능한 메뉴가 없어요.")
    now = timezone.now()
    room = Room.objects.create(
        organizer=user,
        cafe=cafe,
        cafe_snapshot={"name": cafe.name, "phone": cafe.phone, "address": cafe.address},
        menu_snapshot=menu,
        created_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    RoomEvent.objects.create(room=room, action=RoomEvent.Action.CREATED, created_at=now)
    return room


def expire_locked(room, now):
    if room.status in UNSENT and room.expires_at <= now:
        room.status = Room.Status.EXPIRED
        room.save(update_fields=["status"])
        RoomEvent.objects.create(room=room, action=RoomEvent.Action.EXPIRED, created_at=now)
        notify_organizer(room, OrderNotification.Kind.EXPIRED, now)
        return True
    if room.status == Room.Status.PENDING and room.final_order.respond_by <= now:
        room.status = Room.Status.REJECTED
        room.rejection_reason = "5분 미응답"
        room.resolved_at = now
        room.save(update_fields=["status", "rejection_reason", "resolved_at"])
        RoomEvent.objects.create(room=room, action=RoomEvent.Action.TIMED_OUT, created_at=now)
        notify_organizer(room, OrderNotification.Kind.REJECTED, now)
        return True
    return False


@transaction.atomic
def refresh_room(room_id):
    room = Room.objects.select_for_update().get(pk=room_id)
    expire_locked(room, timezone.now())
    return room


@transaction.atomic
def delete_room(*, user, room_id):
    room = Room.objects.select_for_update().get(pk=room_id, organizer=user)
    now = timezone.now()
    if expire_locked(room, now) or room.status not in UNSENT:
        return False
    room.status = Room.Status.DELETED
    room.save(update_fields=["status"])
    RoomEvent.objects.create(room=room, action=RoomEvent.Action.DELETED, created_at=now)
    return True


def expire_due_rooms(*, now=None):
    now = now or timezone.now()
    total = 0
    while True:
        with transaction.atomic():
            rooms = list(
                Room.objects.select_for_update(skip_locked=True, of=("self",))
                .filter(
                    models.Q(status__in=UNSENT, expires_at__lte=now)
                    | models.Q(status=Room.Status.PENDING, final_order__respond_by__lte=now)
                )
                .order_by("pk")[:100]
            )
            if not rooms:
                return total
            for room in rooms:
                total += expire_locked(room, now)
