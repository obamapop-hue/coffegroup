# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./

# Railway's Metal builder supports cache mounts only. Public PyPI downloads
# use the platform's normal verified TLS trust without embedding local CAs.
RUN python -m pip install --no-cache-dir uv==0.12.23 && \
    uv sync --frozen --no-dev

RUN useradd --uid 10001 --create-home app
COPY --chown=app:app . .
# Installed dependencies only need read/execute access. Source files are already
# owned by app; allow static-file output without recursively rewriting the venv.
RUN chown app:app /app
USER app

EXPOSE 8000
CMD ["bash", "scripts/start_web.sh"]
