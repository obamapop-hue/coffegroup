import time

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, close_old_connections, connection

from core.runtime import record_deadline_success
from orders.services import expire_due_rooms


class Command(BaseCommand):
    help = "미전송 주문방의 30분 폐기와 전송 주문의 5분 미수락 거절을 처리합니다."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=float, default=5)

    def handle(self, *args, **options):
        if options["interval"] <= 0:
            raise CommandError("처리 주기는 0보다 커야 합니다.")
        if not options["once"]:
            self.stdout.write("주문서 기한 처리 시작")
        failures = 0
        while True:
            if not connection.in_atomic_block:
                close_old_connections()
            try:
                count = expire_due_rooms()
                record_deadline_success()
                failures = 0
                if count or options["once"]:
                    self.stdout.write(f"기한 경과 주문서 {count}건 처리")
            except DatabaseError:
                failures += 1
                if options["once"] or failures >= 3:
                    raise CommandError("기한 처리 DB 연결을 확인해 주세요.") from None
                self.stderr.write("기한 처리 DB 연결 재시도")
            if options["once"]:
                return
            time.sleep(options["interval"])
