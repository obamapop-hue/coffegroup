from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from accounts.views import role_required

from .models import OrderNotification
from .notifications import notification_message
from .services import expire_due_rooms


@never_cache
@require_GET
@role_required("organizer")
def feed(request):
    expire_due_rooms()
    notices = OrderNotification.objects.filter(recipient=request.user, read_at__isnull=True)
    return JsonResponse(
        {
            "notifications": [
                {
                    "id": notice.pk,
                    "message": notification_message(notice),
                    "order_url": reverse("orders:detail", args=[notice.room_id]),
                    "read_url": reverse("orders:notification-read", args=[notice.pk]),
                    "created_at": notice.created_at.isoformat(),
                }
                for notice in notices.select_related("room")[:20]
            ]
        }
    )


@never_cache
@require_POST
@role_required("organizer")
def read(request, notice_id):
    notice = get_object_or_404(OrderNotification, pk=notice_id, recipient=request.user)
    OrderNotification.objects.filter(pk=notice.pk, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    return JsonResponse({"ok": True})
