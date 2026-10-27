from functools import wraps

from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.providers.base import AuthProcess
from allauth.socialaccount.providers.kakao.views import oauth2_callback
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from cafes.forms import CafeForm, SellerLinkForm
from cafes.models import Cafe
from cafes.services import link_seller, unlink_seller
from cafes.sheets import cafe_state
from orders.models import Room
from orders.services import ACTIVE, expire_due_rooms

from .services import ROLES, enter_role


def role_required(role):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"accounts:entry-{role}")
            allowed = request.user.is_active and (
                role == "organizer"
                or (role == "operator" and request.user.is_operator)
                or (role == "seller" and Cafe.objects.filter(seller=request.user).exists())
            )
            if not allowed:
                return render(
                    request, "accounts/denied.html", {"role_name": ROLES[role]}, status=403
                )
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


@never_cache
@require_GET
def entry(request, role):
    if role not in ROLES:
        raise Http404
    return render(request, "accounts/entry.html", {"role": role, "role_name": ROLES[role]})


@never_cache
@require_POST
def start(request, role):
    if role not in ROLES:
        raise Http404
    if request.user.is_authenticated:
        try:
            return redirect(enter_role(request, request.user, role))
        except PermissionDenied:
            return render(request, "accounts/denied.html", {"role_name": ROLES[role]}, status=403)
    if not settings.KAKAO_LOGIN_ENABLED:
        return render(request, "accounts/unavailable.html", status=503)
    # Role and destination are bound to allauth's one-use, expiring OAuth state.
    # Caller-supplied next, process, scope and role-elevation parameters are ignored.
    provider = get_adapter().get_provider(request, "kakao")
    return provider.redirect(request, AuthProcess.LOGIN, data={"role": role})


@never_cache
@require_GET
def denied(request, role):
    if role not in ROLES:
        raise Http404
    return render(request, "accounts/denied.html", {"role_name": ROLES[role]}, status=403)


@never_cache
@require_GET
def login_error(request):
    return render(request, "accounts/login_error.html")


@never_cache
@require_GET
def callback(request):
    if not settings.KAKAO_LOGIN_ENABLED:
        return render(request, "accounts/unavailable.html", status=503)
    return oauth2_callback(request)


@never_cache
@require_POST
def logout(request):
    auth_logout(request)
    return redirect("home")


@never_cache
@require_GET
@role_required("organizer")
def profile(request):
    expire_due_rooms()
    history = Paginator(Room.objects.filter(organizer=request.user), 20).get_page(
        request.GET.get("page")
    )
    return render(request, "accounts/profile.html", {"history": history})


@never_cache
@require_GET
@role_required("operator")
def privacy_preview(request):
    from .models import KakaoUnlinkJob

    return render(
        request,
        "accounts/privacy_preview.html",
        {"unlink_pending_count": KakaoUnlinkJob.objects.count()},
    )


@never_cache
@require_GET
@role_required("organizer")
def organizer(request):
    expire_due_rooms()
    return render(
        request,
        "accounts/organizer.html",
        {
            "cafes": Cafe.objects.all(),
            "rooms": Room.objects.filter(organizer=request.user, status__in=ACTIVE),
        },
    )


@never_cache
@require_GET
@role_required("seller")
def seller(request):
    from orders.seller_views import render_dashboard

    return render_dashboard(request)


@never_cache
@require_GET
@role_required("operator")
def operator(request):
    from support.models import Ticket

    expire_due_rooms()
    tab = "orders" if request.GET.get("tab") == "orders" else "cafes"
    return render(
        request,
        "accounts/operator.html",
        {
            "operator_tab": tab,
            "cafes": Cafe.objects.select_related("seller"),
            "form": CafeForm(),
            "sheet_data": cafe_state() if tab == "cafes" else None,
            "recent_rooms": Room.objects.all()[:20] if tab == "orders" else [],
            "support_waiting_count": Ticket.objects.filter(status=Ticket.Status.WAITING).count(),
        },
    )


@never_cache
@require_POST
@role_required("operator")
def cafe_create(request):
    form = CafeForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, "카페를 등록했어요.")
        return redirect("accounts:operator")
    return render(
        request,
        "accounts/operator.html",
        {"operator_tab": "cafes", "sheet_data": cafe_state(), "form": form},
        status=400,
    )


@never_cache
@require_POST
@role_required("operator")
def seller_link(request, cafe_id):
    cafe = get_object_or_404(Cafe, pk=cafe_id)
    form = SellerLinkForm(request.POST)
    if form.is_valid():
        try:
            link_seller(actor=request.user, cafe_id=cafe.pk, **form.cleaned_data)
        except ValidationError as error:
            form.add_error("account_code", error)
        else:
            messages.success(request, "판매자를 연결했어요. 판매자가 로그인하면 영업이 시작돼요.")
            return redirect("accounts:operator")
    return render(request, "accounts/seller_link.html", {"form": form, "cafe": cafe}, status=400)


@never_cache
@require_POST
@role_required("operator")
def seller_unlink(request, cafe_id):
    cafe = get_object_or_404(Cafe, pk=cafe_id)
    unlink_seller(actor=request.user, cafe_id=cafe.pk)
    messages.success(request, "판매자 연결을 해제했어요. 카페는 영업종료로 바뀌었어요.")
    return redirect("accounts:operator")
