"""Create a private local configuration once; never overwrite an existing file."""

import os
import secrets
from pathlib import Path


def main():
    target = Path(__file__).resolve().parent.parent / ".env"
    password = secrets.token_urlsafe(32)
    content = (
        f"DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}\n"
        "DJANGO_DEBUG=True\n"
        "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1\n"
        "POSTGRES_DB=coffegroup\n"
        "POSTGRES_USER=coffegroup\n"
        f"POSTGRES_PASSWORD={password}\n"
        f"DATABASE_URL=postgresql://coffegroup:{password}@127.0.0.1:5432/coffegroup\n"
    )
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("기존 .env를 유지합니다. 값을 덮어쓰지 않았습니다.")
        return
    with os.fdopen(fd, "w") as file:
        file.write(content)
    print("로컬 .env를 생성했습니다. 비밀값은 출력하지 않습니다.")


if __name__ == "__main__":
    main()
