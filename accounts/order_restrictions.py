"""Explicit, idempotent operator changes; serialize with member order writes."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .models import MembershipRecord, OrderRestrictionEvent, User


@transaction.atomic
def set_ordering_suspension(*, actor, membership_id, suspended):
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied
    if type(suspended) is not bool:
        raise ValidationError("정지 또는 해제를 선택해 주세요.")
    target_id = (
        MembershipRecord.objects.filter(pk=membership_id).values_list("user_id", flat=True).first()
    )
    if target_id is None:
        raise ValidationError("이미 탈퇴했거나 찾을 수 없는 회원이에요.")
    users = {
        user.pk: user
        for user in User.objects.select_for_update(no_key=True)
        .filter(pk__in={actor.pk, target_id})
        .order_by("pk")
    }
    operator = users.get(actor.pk)
    member = users.get(target_id)
    if operator is None or not operator.is_active or not operator.is_operator:
        raise PermissionDenied
    record = MembershipRecord.objects.select_for_update().get(pk=membership_id)
    if member is None or record.user_id != member.pk or not member.is_active:
        raise ValidationError("현재 이용 중인 회원만 주문 정지·해제를 할 수 있어요.")
    if member.ordering_suspended == suspended:
        return False
    member.ordering_suspended = suspended
    member.save(update_fields=["ordering_suspended"])
    OrderRestrictionEvent.objects.create(
        membership_record=record,
        actor=operator,
        actor_nickname=operator.display_name,
        action=OrderRestrictionEvent.Action.SUSPENDED
        if suspended
        else OrderRestrictionEvent.Action.RESTORED,
    )
    return True
