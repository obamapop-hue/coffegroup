from allauth.socialaccount.models import SocialAccount
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .membership import ensure_existing_members
from .models import KakaoUnlinkJob, MembershipRecord
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
    )
    status = request.GET.get("status", "all")
    if status == "current":
        records = records.filter(user__isnull=False)
    elif status == "withdrawn":
        records = records.filter(user__isnull=True)
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
        },
    )
