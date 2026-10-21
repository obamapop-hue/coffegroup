from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction


class Command(BaseCommand):
    help = "PostgreSQL 쓰기·읽기를 확인하고 검증용 데이터는 자동 제거합니다."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("PostgreSQL 연결이 필요합니다.")
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TEMP TABLE coffegroup_database_probe "
                    "(id integer PRIMARY KEY, value text NOT NULL) ON COMMIT DROP"
                )
                cursor.execute(
                    "INSERT INTO coffegroup_database_probe (id, value) VALUES (%s, %s)",
                    [1, "coffegroup-read-write-check"],
                )
                cursor.execute("SELECT value FROM coffegroup_database_probe WHERE id = %s", [1])
                if cursor.fetchone() != ("coffegroup-read-write-check",):
                    raise CommandError("PostgreSQL 읽기 결과가 쓰기 값과 다릅니다.")
        except DatabaseError as exc:
            raise CommandError("PostgreSQL 쓰기·읽기 검증에 실패했습니다.") from exc
        self.stdout.write(
            self.style.SUCCESS("PostgreSQL 쓰기·읽기 확인 완료. 검증 데이터 제거 완료.")
        )
