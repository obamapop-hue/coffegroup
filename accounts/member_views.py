from urllib.parse import urlencode

from allauth.socialaccount.models import SocialAccount
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .membership import ensure_existing_members
from .models import KakaoUnlinkJob, MembershipRecord, OrderRestrictionEvent
from .order_restrictions import set_ordering_suspension
from .views import role_required


def display_row(record):
    member = record.user
    roles = []
    if member is not None:
        roles.append("총무")
        cafe = getattr(member, "seller_cafe", None)
        if cafe is not None:
            roles.append("판매자")
        if member.is_operator:
            roles.append("운영자")
        status = "가입" if member.is_active else "이용 중지"
        connection = "연결됨" if record.has_kakao else "연결 없음"
    else:
        cafe = None
        status = "탈퇴"
        if record.unlink_confirmed_at:
            connection = "해제 완료"
        elif record.has_unlink_job:
            connection = "해제 대기"
        else:
            connection = "확인 기록 없음"
    return {
        "number": record.pk,
        "nickname": member.display_name if member is not None else record.nickname,
        "status": status,
        "roles": " · ".join(roles) or "—",
        "cafe": cafe.name if cafe is not None else "",
        "joined_at": record.joined_at,
        "withdrawn_at": record.withdrawn_at,
        "connection": connection,
        "ordering_suspended": bool(member is not None and member.ordering_suspended),
        "can_restrict": bool(member is not None and member.is_active),
        "unlink_confirmed_at": record.unlink_confirmed_at,
    }


@never_cache
@require_GET
@role_required("operator")
def members(request):
    ensure_existing_members()
    records = MembershipRecord.objects.all()
    totals = records.aggregate(
        total=Count("pk"),
        current=Count("pk", filter=Q(user__isnull=False)),
        withdrawn=Count("pk", filter=Q(user__isnull=True)),
        suspended=Count("pk", filter=Q(user__ordering_suspended=True)),
    )
    status = request.GET.get("status", "all")
    if status == "current":
        records = records.filter(user__isnull=False)
    elif status == "withdrawn":
        records = records.filter(user__isnull=True)
    elif status == "suspended":
        records = records.filter(user__ordering_suspended=True)
    else:
        status = "all"
    query = request.GET.get("q", "").strip()[:100]
    if query:
        scope = Q(nickname__icontains=query) | Q(user__nickname__icontains=query)
        if query.isdecimal() and len(query) <= 18:
            scope |= Q(pk=int(query))
        records = records.filter(scope)
    records = records.select_related("user", "user__seller_cafe").annotate(
        has_kakao=Exists(
            SocialAccount.objects.filter(user_id=OuterRef("user_id"), provider="kakao")
        ),
        has_unlink_job=Exists(KakaoUnlinkJob.objects.filter(membership_record_id=OuterRef("pk"))),
    )
    page = Paginator(records, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "accounts/members.html",
        {
            "rows": [display_row(record) for record in page],
            "page": page,
            "totals": totals,
            "status": status,
            "query": query,
            "restriction_events": OrderRestrictionEvent.objects.select_related("membership_record")[
                :20
            ],
        },
    )


@never_cache
@require_POST
@role_required("operator")
def restrict_ordering(request, membership_id):
    action = request.POST.get("action")
    try:
        if action not in {"suspend", "restore"}:
            raise ValidationError("정지 또는 해제를 선택해 주세요.")
        changed = set_ordering_suspension(
            actor=request.user, membership_id=membership_id, suspended=action == "suspend"
        )
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        messages.success(
            request,
            ("주문 이용을 정지했어요." if action == "suspend" else "주문 이용 정지를 해제했어요.")
            if changed
            else "이미 같은 상태예요.",
        )
    status = request.POST.get("status", "all")
    if status not in {"all", "current", "withdrawn", "suspended"}:
        status = "all"
    page = request.POST.get("page", "1")
    if not page.isdecimal() or len(page) > 6:
        page = "1"
    params = urlencode({"status": status, "q": request.POST.get("q", "")[:100], "page": page})
    return redirect(reverse("accounts:members") + "?" + params)
