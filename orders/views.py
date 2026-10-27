import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import User
from accounts.ordering import OrderingSuspended, ordering_required
from accounts.views import role_required
from cafes.models import Cafe

from .collection import (
    EditConflict,
    add_submission,
    close_collection,
    edit_submission,
    editable_items,
)
from .dispatch import ConfirmationChanged, cancel_order, preview_order, send_order
from .forms import OrderForm, submission_token
from .models import FinalOrder, Room, Submission
from .services import UNSENT, create_room, delete_room, refresh_room


@never_cache
@require_POST
@role_required("organizer")
@ordering_required
def create(request, cafe_id):
    get_object_or_404(Cafe, pk=cafe_id)
    try:
        room = create_room(user=request.user, cafe_id=cafe_id)
    except ValidationError as error:
        messages.error(request, error.messages[0])
        return redirect("accounts:organizer")
    messages.success(
        request,
        format_html(
            '주문방을 만들었어요.<br><span class="order-expiry-time">{}</span>까지 '
            "전송하지 않으면 주문서가 자동 폐기돼요.",
            timezone.localtime(room.expires_at).strftime("%H:%M"),
        ),
    )
    return redirect("orders:detail", room_id=room.pk)


@never_cache
@require_GET
@role_required("organizer")
def detail(request, room_id):
    scope = {} if request.user.is_operator else {"organizer": request.user}
    get_object_or_404(Room, pk=room_id, **scope)
    # Bind the send token to the very orders displayed, including concurrent edits.
    with transaction.atomic():
        room = refresh_room(room_id)
        submissions = list(room.submissions.filter(deleted_at__isnull=True))
        final_order = FinalOrder.objects.filter(room=room).first()
        can_send = (
            room.organizer_id == request.user.pk
            and room.status == Room.Status.CLOSED
            and bool(submissions)
            and not request.user.ordering_suspended
        )
        confirmation_token = ""
        if can_send:
            try:
                preview = preview_order(user=request.user, room_id=room.pk)
            except OrderingSuspended:
                request.user.ordering_suspended = True
                preview = None
            if preview:
                room, _, confirmation_token = preview
            else:
                room = refresh_room(room_id)
                can_send = False
    return render(
        request,
        "orders/detail.html",
        {
            "room": room,
            "can_delete": room.organizer_id == request.user.pk and room.status in UNSENT,
            "can_edit": room.organizer_id == request.user.pk
            and room.status in UNSENT
            and not request.user.ordering_suspended,
            "submissions": submissions,
            "total_quantity": final_order.total_quantity
            if final_order
            else sum(s.total_quantity for s in submissions),
            "total_amount": final_order.total_amount
            if final_order
            else sum(s.total_amount for s in submissions),
            "final_order": final_order,
            "can_send": can_send,
            "confirmation_token": confirmation_token,
            "can_cancel": room.organizer_id == request.user.pk
            and room.status == Room.Status.PENDING,
            "share_url": request.build_absolute_uri(f"/join/{room.token}/"),
        },
    )


@never_cache
@require_POST
@role_required("organizer")
def delete(request, room_id):
    get_object_or_404(Room, pk=room_id, organizer=request.user)
    if delete_room(user=request.user, room_id=room_id):
        messages.success(
            request, "주문서를 삭제했어요. 삭제 이력은 개인정보의 내 주문 이력에 남아요."
        )
    else:
        messages.info(request, "이미 종료된 주문서예요.")
    return redirect("accounts:profile")


@never_cache
@require_http_methods(["GET", "POST"])
@ordering_required
def join(request, token):
    # Only the shared menu is exposed; never serialize the organizer or events.
    room_id = Room.objects.filter(token=token).values_list("pk", flat=True).first()
    if room_id is None:
        raise Http404
    room = refresh_room(room_id)
    if (
        room.status != Room.Status.OPEN
        or not User.objects.filter(
            pk=room.organizer_id, is_active=True, ordering_suspended=False
        ).exists()
    ):
        return render(request, "orders/unavailable.html", status=410)
    form = OrderForm(
        request.POST or None,
        room_id=room.pk,
        initial={"items": [], "request_token": submission_token(room.pk)},
    )
    if request.method == "POST" and form.is_valid():
        try:
            result = add_submission(
                participant_user=request.user,
                room_id=room.pk,
                nickname=form.cleaned_data["nickname"],
                items=form.cleaned_data["items"],
                request_key=form.cleaned_data["request_key"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            if result is None:
                return render(request, "orders/unavailable.html", status=410)
            return redirect("join-submitted", token=token)
    return order_editor(
        request, room, form, is_organizer=False, status=400 if request.method == "POST" else 200
    )


def order_editor(request, room, form, *, is_organizer, status=200, editing=False, submission=None):
    try:
        initial_items = json.loads(form["items"].value() or "[]", parse_constant=lambda _: None)
    except (ValueError, TypeError):
        initial_items = []
    categories = {}
    for item in room.menu_snapshot:
        categories.setdefault(item["category"].strip() or "기타", []).append(item)
    return render(
        request,
        "orders/order_form.html",
        {
            "room": room,
            "form": form,
            "menu": room.menu_snapshot,
            "menu_categories": [
                {"name": name, "items": items} for name, items in categories.items()
            ],
            "initial_items": initial_items,
            "is_organizer": is_organizer,
            "editing": editing,
            "submission": submission,
        },
        status=status,
    )


@never_cache
@require_GET
def submitted(request, token):
    # A generic receipt has no order IDs, names or other participants' information.
    get_object_or_404(Room, token=token)
    return render(request, "orders/submitted.html")


@never_cache
@require_http_methods(["GET", "POST"])
@role_required("organizer")
@ordering_required
def order_add(request, room_id):
    get_object_or_404(Room, pk=room_id, organizer=request.user)
    room = refresh_room(room_id)
    if room.status not in UNSENT:
        return render(request, "orders/unavailable.html", status=410)
    form = OrderForm(
        request.POST or None,
        room_id=room.pk,
        initial={
            "items": [],
            "nickname": request.user.display_name,
            "request_token": submission_token(room.pk),
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            result = add_submission(
                user=request.user,
                room_id=room.pk,
                nickname=form.cleaned_data["nickname"],
                items=form.cleaned_data["items"],
                request_key=form.cleaned_data["request_key"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            if result is None:
                return render(request, "orders/unavailable.html", status=410)
            messages.success(request, "주문을 추가했어요.")
            return redirect("orders:detail", room_id=room.pk)
    return order_editor(
        request, room, form, is_organizer=True, status=400 if request.method == "POST" else 200
    )


@never_cache
@require_http_methods(["GET", "POST"])
@role_required("organizer")
@ordering_required
def order_edit(request, room_id, submission_id):
    get_object_or_404(Room, pk=room_id, organizer=request.user)
    room = refresh_room(room_id)
    submission = get_object_or_404(Submission, pk=submission_id, room=room, deleted_at__isnull=True)
    if room.status not in UNSENT:
        return render(request, "orders/unavailable.html", status=410)
    form = OrderForm(
        request.POST or None,
        room_id=room.pk,
        editing=True,
        initial={
            "nickname": submission.nickname,
            "items": editable_items(submission),
            "version": submission.version,
        },
    )
    status = 400 if request.method == "POST" else 200
    if request.method == "POST" and form.is_valid():
        try:
            result = edit_submission(
                user=request.user,
                room_id=room.pk,
                submission_id=submission.pk,
                version=form.cleaned_data["version"],
                nickname=form.cleaned_data["nickname"],
                items=form.cleaned_data["items"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        except EditConflict:
            form.add_error(
                None,
                "다른 창에서 이 주문이 변경됐어요. 입력 내용은 유지했으니 "
                "최신 주문을 확인한 뒤 수정 화면을 다시 열어 주세요.",
            )
            status = 409
        else:
            if result is None:
                return render(request, "orders/unavailable.html", status=410)
            messages.success(request, "주문을 수정했어요.")
            return redirect("orders:detail", room_id=room.pk)
    return order_editor(
        request, room, form, is_organizer=True, editing=True, status=status, submission=submission
    )


@never_cache
@require_POST
@role_required("organizer")
@ordering_required
def order_delete(request, room_id, submission_id):
    get_object_or_404(Room, pk=room_id, organizer=request.user)
    get_object_or_404(Submission, pk=submission_id, room_id=room_id)
    try:
        version = int(request.POST.get("version", ""))
    except (ValueError, TypeError):
        return render(request, "orders/conflict.html", {"room_id": room_id}, status=400)
    try:
        result = edit_submission(
            user=request.user,
            room_id=room_id,
            submission_id=submission_id,
            version=version,
            delete=True,
        )
    except EditConflict:
        return render(request, "orders/conflict.html", {"room_id": room_id}, status=409)
    if result is None:
        return render(request, "orders/unavailable.html", status=410)
    messages.success(request, "주문 항목을 삭제했어요.")
    return redirect("orders:detail", room_id=room_id)


@never_cache
@require_POST
@role_required("organizer")
@ordering_required
def close(request, room_id):
    get_object_or_404(Room, pk=room_id, organizer=request.user)
    if close_collection(user=request.user, room_id=room_id):
        messages.success(
            request, "참여를 마감했어요. 최종 전송 전까지 총무는 주문을 수정할 수 있어요."
        )
    else:
        messages.info(request, "이미 마감되었거나 종료된 주문서예요.")
    return redirect("orders:detail", room_id=room_id)


@never_cache
@require_GET
@role_required("organizer")
def confirm(request, room_id):
    # Keep old bookmarks working without introducing another checkout screen.
    get_object_or_404(Room, pk=room_id, organizer=request.user)
    return redirect("orders:detail", room_id=room_id)


@never_cache
@require_POST
@role_required("organizer")
@ordering_required
def send(request, room_id):
    get_object_or_404(Room, pk=room_id, organizer=request.user)
    try:
        result = send_order(
            user=request.user,
            room_id=room_id,
            confirmation_token=request.POST.get("confirmation_token", ""),
        )
    except ConfirmationChanged:
        return render(request, "orders/confirmation_changed.html", {"room_id": room_id}, status=409)
    except ValidationError as error:
        return render(
            request,
            "orders/confirmation_changed.html",
            {"room_id": room_id, "error": error.messages[0]},
            status=400,
        )
    if result and Room.objects.filter(pk=room_id, status=Room.Status.PENDING).exists():
        messages.success(request, "주문서를 전송했어요. 카페의 수락을 기다려 주세요.")
    elif result:
        messages.info(request, "이미 전송한 주문서예요. 현재 처리 상태를 확인해 주세요.")
    return redirect("orders:detail", room_id=room_id)


@never_cache
@require_POST
@role_required("organizer")
def cancel(request, room_id):
    get_object_or_404(Room, pk=room_id, organizer=request.user)
    if cancel_order(user=request.user, room_id=room_id):
        messages.success(request, "주문을 취소했어요. 주문 내역과 취소 이력은 남아요.")
    else:
        messages.info(request, "이미 처리되었거나 수락된 주문은 취소할 수 없어요.")
    return redirect("orders:detail", room_id=room_id)


@never_cache
@require_GET
@role_required("organizer")
def state(request, room_id):
    scope = {} if request.user.is_operator else {"organizer": request.user}
    get_object_or_404(Room, pk=room_id, **scope)
    room = refresh_room(room_id)
    return JsonResponse(
        {
            "status": room.status,
            "label": room.get_status_display(),
            "rejection_reason": room.rejection_reason,
        }
    )
