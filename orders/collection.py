"""Collect orders against the room's frozen catalog. All writes lock the room first."""

import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from accounts.ordering import OrderingSuspended, ordering_member

from .models import Room, RoomEvent, Submission, SubmissionRevision
from .services import UNSENT, expire_locked


class EditConflict(Exception):
    pass


def normalize_order(menu_snapshot, nickname, items):
    if not isinstance(nickname, str):
        raise ValidationError("이름(닉네임)을 입력해 주세요.")
    nickname = nickname.strip()
    if not nickname or len(nickname) > 30 or any(ord(c) < 32 or ord(c) == 127 for c in nickname):
        raise ValidationError("이름(닉네임)은 1~30자로 입력해 주세요.")
    if not isinstance(items, list) or not 1 <= len(items) <= 50:
        raise ValidationError("메뉴는 한 번에 1~50개 항목을 담아 주세요.")
    menus = {item["id"]: item for item in menu_snapshot}
    normalized = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValidationError(f"{index}번째 메뉴를 다시 선택해 주세요.")
        menu_id, quantity = item.get("menu_id"), item.get("quantity")
        if type(menu_id) is not int or menu_id not in menus:
            raise ValidationError(f"{index}번째 메뉴는 이 주문방의 메뉴판에 없어요.")
        if type(quantity) is not int or not 1 <= quantity <= 99:
            raise ValidationError(f"{index}번째 메뉴 수량은 1~99잔으로 입력해 주세요.")
        menu = menus[menu_id]
        selections = item.get("selections", {})
        quantities = item.get("option_quantities", {})
        groups = {str(group["id"]): group for group in menu["groups"]}
        if not isinstance(selections, dict) or set(selections) - groups.keys():
            raise ValidationError(f"{index}번째 메뉴의 옵션을 다시 선택해 주세요.")
        if not isinstance(quantities, dict) or set(quantities) - groups.keys():
            raise ValidationError(f"{index}번째 메뉴의 옵션 수량을 확인해 주세요.")
        options, extra = [], 0
        for key, group in groups.items():
            ids = selections.get(key, [c["id"] for c in group["choices"] if c["is_default"]])
            choices = {choice["id"]: choice for choice in group["choices"]}
            if (
                not isinstance(ids, list)
                or any(type(choice_id) is not int or choice_id not in choices for choice_id in ids)
                or len(ids) != len(set(ids))
                or (group["mode"] == "single" and len(ids) > 1)
                or (group["required"] and not ids)
            ):
                raise ValidationError(f"{index}번째 메뉴의 ‘{group['name']}’ 옵션을 확인해 주세요.")
            selected = [c for c in group["choices"] if c["id"] in ids]
            counts = quantities.get(key, {})
            if (
                not isinstance(counts, dict)
                or set(counts) - {str(choice_id) for choice_id in ids}
                or (group["mode"] != "multiple" and counts)
                or any(type(count) is not int or not 1 <= count <= 99 for count in counts.values())
            ):
                raise ValidationError(
                    f"{index}번째 메뉴의 ‘{group['name']}’ 수량은 1~99개로 입력해 주세요."
                )
            extra += sum(c["extra_price"] * counts.get(str(c["id"]), 1) for c in selected)
            options.append(
                {
                    "group_id": group["id"],
                    "group_name": group["name"],
                    "choices": [
                        {
                            "id": c["id"],
                            "name": c["name"],
                            "extra_price": c["extra_price"],
                            **(
                                {"quantity": counts[str(c["id"])]}
                                if counts.get(str(c["id"]), 1) != 1
                                else {}
                            ),
                        }
                        for c in selected
                    ],
                }
            )
        unit_price = menu["price"] + extra
        normalized.append(
            {
                "menu_id": menu_id,
                "menu_name": menu["name"],
                "category": menu["category"],
                "base_price": menu["price"],
                "options": options,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": unit_price * quantity,
            }
        )
    return nickname, normalized


def record_revision(submission, action, actor):
    SubmissionRevision.objects.create(
        submission=submission,
        version=submission.version,
        action=action,
        actor=actor,
        snapshot={
            "nickname": submission.nickname,
            "items": submission.items,
            "total_quantity": submission.total_quantity,
            "total_amount": submission.total_amount,
        },
    )


def require_owner(room, user):
    if not user or not user.is_authenticated or not user.is_active or room.organizer_id != user.pk:
        raise PermissionDenied


@transaction.atomic
def add_submission(*, room_id, nickname, items, request_key, user=None, participant_user=None):
    owner = None
    if user is not None:
        ordering_member(user)
    else:
        owner_id = Room.objects.values_list("organizer_id", flat=True).get(pk=room_id)
        member_ids = {owner_id} if owner_id is not None else set()
        if participant_user is not None and participant_user.is_authenticated:
            member_ids.add(participant_user.pk)
        members = {
            member.pk: member
            for member in User.objects.select_for_update(no_key=True)
            .filter(pk__in=member_ids)
            .order_by("pk")
        }
        if participant_user is not None and participant_user.is_authenticated:
            participant = members.get(participant_user.pk)
            if participant is None or not participant.is_active:
                raise PermissionDenied
            if participant.ordering_suspended:
                raise OrderingSuspended
        owner = members.get(owner_id)
        if owner is None or not owner.is_active or owner.ordering_suspended:
            return None
    room = Room.objects.select_for_update().get(pk=room_id)
    if user is not None:
        require_owner(room, user)
    elif room.organizer_id != owner.pk:
        return None
    # Return a sentinel rather than raising inside atomic: expiry must commit.
    if expire_locked(room, timezone.now()) or room.status not in (
        UNSENT if user else (Room.Status.OPEN,)
    ):
        return None
    nickname, items = normalize_order(room.menu_snapshot, nickname, items)
    try:
        request_key = uuid.UUID(str(request_key))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValidationError("화면을 새로고침한 뒤 다시 제출해 주세요.") from error
    existing = Submission.objects.filter(room=room, request_key=request_key).first()
    if existing:
        if existing.nickname != nickname or existing.items != items or existing.deleted_at:
            raise ValidationError("이미 처리한 제출이에요. 화면을 새로 열어 주세요.")
        return existing
    now = timezone.now()
    submission = Submission.objects.create(
        room=room,
        nickname=nickname,
        items=items,
        request_key=request_key,
        source=Submission.Source.ORGANIZER if user else Submission.Source.PARTICIPANT,
        total_quantity=sum(item["quantity"] for item in items),
        total_amount=sum(item["line_total"] for item in items),
        created_at=now,
        updated_at=now,
    )
    record_revision(submission, "created", user)
    return submission


@transaction.atomic
def edit_submission(
    *, user, room_id, submission_id, version, nickname=None, items=None, delete=False
):
    ordering_member(user)
    room = Room.objects.select_for_update().get(pk=room_id)
    require_owner(room, user)
    if expire_locked(room, timezone.now()) or room.status not in UNSENT:
        return None
    submission = Submission.objects.get(pk=submission_id, room=room)
    if type(version) is not int or submission.version != version or submission.deleted_at:
        raise EditConflict
    if delete:
        submission.deleted_at = timezone.now()
    else:
        submission.nickname, submission.items = normalize_order(room.menu_snapshot, nickname, items)
        submission.total_quantity = sum(item["quantity"] for item in submission.items)
        submission.total_amount = sum(item["line_total"] for item in submission.items)
    submission.version += 1
    submission.updated_at = timezone.now()
    submission.save()
    record_revision(submission, "deleted" if delete else "edited", user)
    return submission


@transaction.atomic
def close_collection(*, user, room_id):
    ordering_member(user)
    room = Room.objects.select_for_update().get(pk=room_id)
    require_owner(room, user)
    now = timezone.now()
    if expire_locked(room, now) or room.status != Room.Status.OPEN:
        return False
    room.status = Room.Status.CLOSED
    room.save(update_fields=["status"])
    RoomEvent.objects.create(room=room, action=RoomEvent.Action.CLOSED, created_at=now)
    return True


def editable_items(submission):
    return [
        {
            "menu_id": item["menu_id"],
            "quantity": item["quantity"],
            "selections": {
                str(group["group_id"]): [c["id"] for c in group["choices"]]
                for group in item["options"]
            },
            "option_quantities": {
                str(group["group_id"]): {
                    str(c["id"]): c["quantity"]
                    for c in group["choices"]
                    if c.get("quantity", 1) != 1
                }
                for group in item["options"]
                if any(c.get("quantity", 1) != 1 for c in group["choices"])
            },
        }
        for item in submission.items
    ]
