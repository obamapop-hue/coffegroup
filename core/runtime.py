import os
from datetime import timedelta

from django.utils import timezone

from .models import DeadlineWorkerState


def revision_key():
    revision = os.environ.get("K_REVISION", "local")[:80]
    instance = os.environ.get("YEONGCHA_RUNTIME_INSTANCE", "standalone")[:40]
    return f"{revision}:{instance}"


def record_deadline_success():
    DeadlineWorkerState.objects.update_or_create(
        revision=revision_key(), defaults={"last_success_at": timezone.now()}
    )


def deadline_worker_ready():
    now = timezone.now()
    return DeadlineWorkerState.objects.filter(
        revision=revision_key(),
        last_success_at__gte=now - timedelta(seconds=60),
        last_success_at__lte=now + timedelta(seconds=5),
    ).exists()
