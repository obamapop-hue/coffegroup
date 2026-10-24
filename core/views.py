from django.conf import settings
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from .runtime import deadline_worker_ready


@never_cache
@require_safe
def home(request):
    if request.user.is_authenticated:
        return redirect("accounts:organizer")
    return render(request, "core/home.html")


@never_cache
@require_safe
def liveness(request):
    return JsonResponse({"status": "ok"})


@never_cache
@require_safe
def readiness(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            if cursor.fetchone() != (1,):
                return JsonResponse({"status": "unavailable"}, status=503)
        if settings.REQUIRE_DEADLINE_WORKER and not deadline_worker_ready():
            return JsonResponse({"status": "unavailable"}, status=503)
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})
