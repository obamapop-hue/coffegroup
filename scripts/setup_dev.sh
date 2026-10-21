#!/usr/bin/env bash
# Local-only setup. Reuses existing .env and database volume.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.cache/uv}"
export UV_PYTHON_DOWNLOADS=never

python3 scripts/init_local_env.py
uv sync --frozen --system-certs
docker compose up -d --wait db
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py check
.venv/bin/python manage.py check_database

printf '\n개발 환경 준비 완료. 실행: .venv/bin/python manage.py runserver 127.0.0.1:8000\n'
