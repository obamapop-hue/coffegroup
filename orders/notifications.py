from django.utils import timezone

from .models import OrderNotification


def notify_organizer(room, kind, now):
    """Call inside the same locked transaction as the state change."""
    if room.organizer_id:
        OrderNotification.objects.create(
            room=room, recipient_id=room.organizer_id, kind=kind, created_at=now
        )


def notification_message(notice):
    cafe = notice.room.cafe_snapshot["name"]
    if notice.kind == OrderNotification.Kind.ACCEPTED:
        ready = timezone.localtime(notice.room.ready_at).strftime("%m.%d %H:%M")
        return f"{cafe}가 주문을 수락했어요. {ready} 완료 예정이에요."
    if notice.kind == OrderNotification.Kind.REJECTED:
        return f"{cafe} 주문이 거절됐어요. 사유: {notice.room.rejection_reason}"
    if notice.kind == OrderNotification.Kind.EXPIRED:
        return f"{cafe} 주문서가 생성 후 30분 동안 전송되지 않아 폐기됐어요."
    return f"{cafe}의 메뉴가 모두 준비됐어요. 픽업해 주세요."
