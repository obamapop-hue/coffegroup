from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from accounts.models import User

from .models import Cafe, CafeAccessEvent


@transaction.atomic
def link_seller(*, actor, cafe_id, account_code):
    if not actor.is_active or not actor.is_operator:
        raise PermissionDenied
    # Lock the member first so two simultaneous cafe assignments serialize.
    # NO KEY UPDATE still serializes assignments while allowing audit FK reads.
    seller = (
        User.objects.select_for_update(no_key=True)
        .filter(account_code=account_code, is_active=True)
        .first()
    )
    if seller is None:
        raise ValidationError("가입한 판매자의 계정 연결 코드를 확인해 주세요.")
    if Cafe.objects.filter(seller=seller).exclude(pk=cafe_id).exists():
        raise ValidationError("이미 다른 카페에 연결된 계정입니다.")
    cafe = Cafe.objects.select_for_update().get(pk=cafe_id)
    if cafe.seller_id == seller.pk:
        return cafe
    if cafe.seller_id:
        CafeAccessEvent.objects.create(cafe=cafe, actor=actor, action="unlinked")
    cafe.seller = seller
    cafe.is_open = False
    cafe.save(update_fields=["seller", "is_open"])
    CafeAccessEvent.objects.create(cafe=cafe, actor=actor, action="linked")
    return cafe


@transaction.atomic
def unlink_seller(*, actor, cafe_id):
    if not actor.is_active or not actor.is_operator:
        raise PermissionDenied
    cafe = Cafe.objects.select_for_update().get(pk=cafe_id)
    if cafe.seller_id:
        cafe.seller = None
        cafe.is_open = False
        cafe.save(update_fields=["seller", "is_open"])
        CafeAccessEvent.objects.create(cafe=cafe, actor=actor, action="unlinked")


@transaction.atomic
def open_for_seller(*, user):
    if not user.is_active:
        raise PermissionDenied
    cafe = Cafe.objects.select_for_update().filter(seller=user).first()
    if cafe is None:
        raise PermissionDenied
    if not cafe.is_open:
        cafe.is_open = True
        cafe.save(update_fields=["is_open"])
        CafeAccessEvent.objects.create(cafe=cafe, actor=user, action="opened")
    return cafe


@transaction.atomic
def close_for_seller(*, user, cafe_id):
    cafe = Cafe.objects.select_for_update().filter(pk=cafe_id, seller=user).first()
    if cafe and cafe.is_open:
        cafe.is_open = False
        cafe.save(update_fields=["is_open"])
        CafeAccessEvent.objects.create(cafe=cafe, actor=user, action="closed")
