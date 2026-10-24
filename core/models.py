from django.db import models


class DeadlineWorkerState(models.Model):
    """Operational health only; never controls cafe login or business state."""

    revision = models.CharField(max_length=128, primary_key=True)
    last_success_at = models.DateTimeField()
