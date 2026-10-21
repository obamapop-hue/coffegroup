from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.urls import reverse
from django.utils.crypto import constant_time_compare

from cafes.services import open_for_seller

from .models import User

ROLES = {"organizer": "총무", "seller": "카페 판매자", "operator": "운영자"}


@transaction.atomic
def select_initial_operator(user):
    target = settings.INITIAL_OPERATOR_ACCOUNT_CODE
    if not target or not constant_time_compare(str(user.account_code), target):
        return
    member = User.objects.select_for_update().get(pk=user.pk)
    if (
        member.is_active
        and not User.objects.filter(is_operator=True).exists()
        and member.socialaccount_set.filter(provider="kakao").exists()
    ):
        member.is_operator = True
        member.save(update_fields=["is_operator"])
        user.is_operator = True


def enter_role(request, user, role):
    if role not in ROLES or not user.is_active:
        raise PermissionDenied
    if role == "seller":
        cafe = open_for_seller(user=user)
        request.session["seller_cafe_id"] = cafe.pk
    elif role == "operator" and not user.is_operator:
        raise PermissionDenied
    request.session["active_role"] = role
    return reverse(f"accounts:{role}")
