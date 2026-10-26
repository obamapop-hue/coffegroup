import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """One Kakao member can organize orders and hold additional permissions."""

    account_code = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    nickname = models.CharField("닉네임", max_length=40, blank=True)
    is_operator = models.BooleanField("운영자 권한", default=False)
    ordering_suspended = models.BooleanField("주문 이용 정지", default=False)

    @property
    def display_name(self):
        return self.nickname or "영차 회원"


class KakaoUnlinkJob(models.Model):
    """Temporary retry data, removed as soon as Kakao confirms unlinking."""

    kakao_uid = models.CharField(max_length=100, unique=True)
    credential_fingerprint = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    membership_record = models.OneToOneField(
        "MembershipRecord", null=True, on_delete=models.SET_NULL, related_name="unlink_job"
    )


class MembershipRecord(models.Model):
    """Operator membership history, separate from deleted login credentials."""

    user = models.OneToOneField(
        User, null=True, on_delete=models.SET_NULL, related_name="membership_record"
    )
    nickname = models.CharField(max_length=40)
    joined_at = models.DateTimeField(default=timezone.now)
    withdrawn_at = models.DateTimeField(null=True)
    unlink_confirmed_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ["-joined_at", "-pk"]


class OrderRestrictionEvent(models.Model):
    class Action(models.TextChoices):
        SUSPENDED = "suspended", "주문 정지"
        RESTORED = "restored", "정지 해제"

    membership_record = models.ForeignKey(
        MembershipRecord, on_delete=models.CASCADE, related_name="order_restrictions"
    )
    actor = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    actor_nickname = models.CharField(max_length=40)
    action = models.CharField(max_length=12, choices=Action)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-pk"]
