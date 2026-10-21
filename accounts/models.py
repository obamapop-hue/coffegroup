import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """One Kakao member can organize orders and hold additional permissions."""

    account_code = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    nickname = models.CharField("닉네임", max_length=40, blank=True)
    is_operator = models.BooleanField("운영자 권한", default=False)

    @property
    def display_name(self):
        return self.nickname or "영차 회원"
