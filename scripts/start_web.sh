#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

web_port="${PORT:-8000}"
if [[ ! "$web_port" =~ ^[0-9]+$ ]] || (( web_port < 1 || web_port > 65535 )); then
  printf 'PORT는 1~65535 범위의 정수여야 합니다.\n' >&2
  exit 1
fi

.venv/bin/python manage.py collectstatic --noinput
exec .venv/bin/python scripts/run_services.py "$web_port"
