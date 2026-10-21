from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User


class Command(BaseCommand):
    help = "가입한 계정의 연결 코드를 확인해 최초 운영자 권한을 부여합니다."

    def add_arguments(self, parser):
        parser.add_argument("account_code", help="본인이 로그인한 화면의 계정 연결 코드(UUID)")
        parser.add_argument("--revoke", action="store_true", help="운영자 권한을 해제합니다.")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            user = User.objects.select_for_update().get(account_code=options["account_code"])
        except (User.DoesNotExist, ValueError, ValidationError) as error:
            raise CommandError("가입한 계정의 연결 코드를 확인해 주세요.") from error
        if not user.is_active or not user.socialaccount_set.filter(provider="kakao").exists():
            raise CommandError("활성 카카오 가입 계정에만 권한을 부여할 수 있습니다.")
        user.is_operator = not options["revoke"]
        user.save(update_fields=["is_operator"])
        action = "해제" if options["revoke"] else "부여"
        self.stdout.write(self.style.SUCCESS(f"지정한 계정에 운영자 권한을 {action}했습니다."))
