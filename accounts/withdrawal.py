"""Delete site membership atomically and durably request Kakao unlinking.

Order snapshots remain untouched. No remote unlink request takes place before local deletion
commits. A temporary queue blocks re-registration until unlinking is confirmed.
"""

import hashlib
import secrets
import time
from datetime import timedelta

import requests
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from cafes.models import Cafe, CafeAccessEvent
from orders.models import Room
from orders.services import ACTIVE

from .models import KakaoUnlinkJob, User


def verified_for_withdrawal(request):
    proof = request.session.get("withdrawal_verified")
    return (
        isinstance(proof, dict)
        and proof.get("user") == request.user.pk
        and isinstance(proof.get("at"), (int, float))
        and 0 <= time.time() - proof["at"] <= 300
    )


def accept_withdrawal_verification(request, sociallogin):
    from django.utils.crypto import constant_time_compare

    data = sociallogin.state.get("data") or {}
    pending = request.session.pop("withdrawal_nonce", "")
    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or sociallogin.account.provider != "kakao"
        or sociallogin.state.get("process") != "redirect"
        or not sociallogin.is_existing
        or sociallogin.user.pk != request.user.pk
        or data.get("user") != request.user.pk
        or not isinstance(data.get("nonce"), str)
        or not pending
        or not constant_time_compare(pending, data["nonce"])
    ):
        raise PermissionDenied
    request.session["withdrawal_verified"] = {"user": request.user.pk, "at": time.time()}


def begin_withdrawal_verification(request):
    from allauth.socialaccount.adapter import get_adapter
    from allauth.socialaccount.providers.base import AuthProcess

    nonce = secrets.token_urlsafe(32)
    request.session.pop("withdrawal_verified", None)
    request.session["withdrawal_nonce"] = nonce
    return (
        get_adapter()
        .get_provider(request, "kakao")
        .redirect(
            request,
            AuthProcess.REDIRECT,
            data={"action": "verify-withdrawal", "user": request.user.pk, "nonce": nonce},
            auth_params={"prompt": "login"},
        )
    )


def withdrawal_problem(user):
    if user.is_operator:
        return "운영자 계정은 바로 탈퇴할 수 없어요. 운영자 권한을 먼저 정리해 주세요."
    if Room.objects.filter(organizer=user, status__in=ACTIVE).exists():
        return "진행 중인 주문이 있어요. 주문을 완료하거나 미전송 주문서를 삭제한 뒤 탈퇴해 주세요."
    if Room.objects.filter(cafe__seller=user, status__in=["pending", "accepted"]).exists():
        return "카페에서 처리 중인 주문이 있어요. 해당 주문을 처리한 뒤 탈퇴해 주세요."
    if user.socialaccount_set.filter(provider="kakao").count() != 1:
        return "카카오 계정 연결을 확인하지 못했어요. 문의하기로 알려 주세요."
    return ""


@sensitive_variables()
def credential_fingerprint():
    return hashlib.sha256(
        (settings.KAKAO_RESET_API_KEY + ":" + settings.KAKAO_ADMIN_KEY).encode()
    ).hexdigest()


@sensitive_variables()
def verify_admin_member(uid):
    """A key from another Kakao app must never permit local account deletion."""
    if not settings.KAKAO_ADMIN_KEY:
        return False
    try:
        with requests.Session() as session:
            response = session.get(
                "https://kapi.kakao.com/v2/user/me",
                headers={"Authorization": f"KakaoAK {settings.KAKAO_ADMIN_KEY}"},
                params={"target_id_type": "user_id", "target_id": uid, "property_keys": "[]"},
                timeout=8,
                allow_redirects=False,
            )
        data = response.json()
        return (
            response.status_code == 200
            and isinstance(data, dict)
            and str(data.get("id", "")) == uid
        )
    except (requests.RequestException, ValueError):
        return False


@transaction.atomic
def withdraw_member(*, user):
    if (
        not settings.KAKAO_WITHDRAWAL_ENABLED
        or not settings.KAKAO_ADMIN_KEY
        or not user.is_authenticated
        or not user.is_active
    ):
        raise PermissionDenied
    # Same member-before-cafe lock order as seller assignment and room creation.
    member = User.objects.select_for_update(no_key=True).filter(pk=user.pk).first()
    if member is None:
        return None
    if not member.is_active:
        raise PermissionDenied
    list(Room.objects.select_for_update().filter(organizer=member).order_by("pk"))
    cafes = list(Cafe.objects.select_for_update().filter(seller=member).order_by("pk"))
    problem = withdrawal_problem(member)
    if problem:
        raise ValidationError(problem)
    account = SocialAccount.objects.get(user=member, provider="kakao")
    job, _ = KakaoUnlinkJob.objects.get_or_create(
        kakao_uid=account.uid, defaults={"credential_fingerprint": credential_fingerprint()}
    )
    for cafe in cafes:
        if cafe.is_open:
            CafeAccessEvent.objects.create(cafe=cafe, actor=member, action="closed")
        cafe.is_open = False
        cafe.seller = None
        cafe.save(update_fields=["is_open", "seller"])
        CafeAccessEvent.objects.create(cafe=cafe, actor=member, action="unlinked")
    # Invalidate this member's other devices without touching other users' sessions.
    keys = [
        session.session_key
        for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator()
        if str(session.get_decoded().get("_auth_user_id", "")) == str(member.pk)
    ]
    if keys:
        Session.objects.filter(session_key__in=keys).delete()
    member.delete()
    return job.pk


@sensitive_variables()
def unlink_from_kakao(uid):
    if not settings.KAKAO_ADMIN_KEY:
        return False
    try:
        with requests.Session() as session:
            response = session.post(
                "https://kapi.kakao.com/v1/user/unlink",
                headers={"Authorization": f"KakaoAK {settings.KAKAO_ADMIN_KEY}"},
                data={"target_id_type": "user_id", "target_id": uid},
                timeout=8,
                allow_redirects=False,
            )
        data = response.json()
        if not isinstance(data, dict):
            return False
        # Kakao's -101 means that this user is already unlinked from this app.
        if response.status_code == 400 and data.get("code") == -101:
            return True
        return response.status_code == 200 and str(data.get("id", "")) == uid
    except (requests.RequestException, ValueError):
        # Never log response bodies, account IDs, credentials or exception payloads.
        return False


def process_unlink_jobs(*, job_id=None, now=None):
    if not settings.KAKAO_WITHDRAWAL_ENABLED or not settings.KAKAO_ADMIN_KEY:
        return 0
    now = now or timezone.now()
    # One bounded remote request per cycle cannot starve the order deadline worker.
    with transaction.atomic():
        jobs = KakaoUnlinkJob.objects.select_for_update(skip_locked=True)
        if job_id is not None:
            jobs = jobs.filter(pk=job_id)
        job = jobs.filter(next_attempt_at__lte=now).order_by("next_attempt_at", "pk").first()
        if job is None:
            return 0
        if job.credential_fingerprint == credential_fingerprint() and unlink_from_kakao(
            job.kakao_uid
        ):
            job.delete()
            return 1
        job.attempts += 1
        job.next_attempt_at = now + timedelta(seconds=min(3600, 60 * 2 ** min(job.attempts - 1, 6)))
        job.save(update_fields=["attempts", "next_attempt_at"])
        return 0
