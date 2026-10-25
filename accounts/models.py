import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """One Kakao member can organize orders and hold additional permissions."""

    account_code = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    nickname = models.CharField("닉네임", max_length=40, blank=True)
    is_operator = models.BooleanField("운영자 권한", default=False)

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
