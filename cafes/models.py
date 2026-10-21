from django.conf import settings
from django.db import models


class Cafe(models.Model):
    name = models.CharField("카페 이름", max_length=80)
    phone = models.CharField("카페 전화번호", max_length=30, blank=True)
    address = models.CharField("카페 주소", max_length=250, blank=True)
    seller = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="seller_cafe",
    )
    is_open = models.BooleanField("영업 중", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(is_open=False) | models.Q(seller__isnull=False),
                name="open_cafe_requires_seller",
            )
        ]

    def __str__(self):
        return self.name


class Menu(models.Model):
    cafe = models.ForeignKey(Cafe, on_delete=models.PROTECT, related_name="menus")
    name = models.CharField("메뉴 이름", max_length=80)
    category = models.CharField("분류", max_length=40, blank=True)
    price = models.PositiveIntegerField("가격(원)")
    is_active = models.BooleanField("메뉴판에 표시", default=True)
    is_available = models.BooleanField("판매 가능", default=True)

    class Meta:
        ordering = ["category", "pk"]


class OptionGroup(models.Model):
    class Mode(models.TextChoices):
        SINGLE = "single", "하나 선택"
        MULTIPLE = "multiple", "여러 개 선택"

    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, related_name="option_groups")
    name = models.CharField("옵션 이름", max_length=60)
    mode = models.CharField("선택 방식", max_length=10, choices=Mode, default=Mode.SINGLE)
    required = models.BooleanField("필수 선택", default=True)

    class Meta:
        ordering = ["pk"]


class OptionChoice(models.Model):
    group = models.ForeignKey(OptionGroup, on_delete=models.CASCADE, related_name="choices")
    name = models.CharField("선택 항목", max_length=60)
    extra_price = models.PositiveIntegerField("추가 금액(원)", default=0)
    is_default = models.BooleanField("기본 선택", default=False)

    class Meta:
        ordering = ["pk"]


class CafeAccessEvent(models.Model):
    class Action(models.TextChoices):
        LINKED = "linked", "판매자 연결"
        UNLINKED = "unlinked", "판매자 연결 해제"
        OPENED = "opened", "판매자 로그인 · 영업 시작"
        CLOSED = "closed", "판매자 로그아웃 · 영업 종료"

    cafe = models.ForeignKey(Cafe, on_delete=models.PROTECT, related_name="access_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=16, choices=Action.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


class ManagementChange(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    cafe = models.ForeignKey(Cafe, null=True, on_delete=models.PROTECT)
    summary = models.CharField(max_length=250)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
