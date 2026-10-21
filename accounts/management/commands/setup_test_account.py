from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from cafes.models import Cafe
from cafes.services import link_seller


class Command(BaseCommand):
    help = "본인이 확인한 카카오 계정에 운영자·테스트 카페 판매자 권한을 연결합니다."

    def add_arguments(self, parser):
        parser.add_argument("account_code")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            user = User.objects.select_for_update().get(
                account_code=options["account_code"], is_active=True
            )
        except (User.DoesNotExist, ValueError, ValidationError) as error:
            raise CommandError("확인한 본인의 계정 연결 코드를 사용해 주세요.") from error
        if not user.socialaccount_set.filter(provider="kakao").exists():
            raise CommandError("카카오로 가입한 계정이어야 합니다.")
        if User.objects.filter(is_operator=True).exclude(pk=user.pk).exists():
            raise CommandError("기존 운영자가 있습니다. 운영자 화면에서 연결해 주세요.")
        user.is_operator = True
        user.save(update_fields=["is_operator"])
        if not Cafe.objects.filter(seller=user).exists():
            cafe = Cafe.objects.create(name="영차 테스트 카페")
            link_seller(actor=user, cafe_id=cafe.pk, account_code=user.account_code)
        self.stdout.write(
            "본인 테스트 계정에 운영자·판매자 권한을 연결했습니다. "
            "영업은 판매자 페이지 진입 후 시작됩니다."
        )
