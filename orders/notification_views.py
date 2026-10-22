from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from accounts.views import role_required

from .models import OrderNotification


@never_cache
@require_GET
@role_required("organizer")
def feed(request):
    notices = OrderNotification.objects.filter(recipient=request.user, read_at__isnull=True)
    return JsonResponse(
        {
            "notifications": [
                {
                    "id": notice.pk,
                    "message": (
                        f"{notice.room.cafe_snapshot['name']}의 메뉴가 모두 준비됐어요. "
                        "픽업해 주세요."
                    ),
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
