from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from accounts.views import role_required
from cafes.models import Cafe

from .forms import SellerAcceptForm, SellerRejectForm
from .models import Room
from .selling import decide_order, ready_time_choices, seller_context, seller_version
from .services import expire_due_rooms


def render_dashboard(request, *, error_room=None, accept_form=None, reject_form=None, status=200):
    context = seller_context(
        user=request.user,
        page=request.GET.get("page", 1),
        accepted_page=request.GET.get("accepted_page", 1),
    )
    for order in context["pending_orders"]:
        order["accept_form"] = (
            accept_form
            if order["id"] == error_room and accept_form is not None
            else SellerAcceptForm(
                choices=context["ready_choices"], auto_id=f"order-{order['id']}-accept-%s"
            )
        )
        order["reject_form"] = (
            reject_form
            if order["id"] == error_room and reject_form is not None
            else SellerRejectForm(auto_id=f"order-{order['id']}-reject-%s")
        )
    if error_room is not None and not any(
        order["id"] == error_room for order in context["pending_orders"]
    ):
        context["decision_feedback"] = "이미 처리되었거나 수락 기한이 지난 주문이에요."
    return render(request, "accounts/seller.html", context, status=status)


@never_cache
@require_GET
@role_required("seller")
def feed(request):
    cafe = get_object_or_404(Cafe, seller=request.user)
    expire_due_rooms()
    # Read version first, like the initial dashboard; never skip a concurrent new event.
    version = seller_version(cafe)
    pending = list(
        Room.objects.filter(cafe=cafe, status=Room.Status.PENDING).values_list("pk", flat=True)
    )
    return JsonResponse({"version": version, "pending": pending})


def decision(request, room_id, action):
    get_object_or_404(Room, pk=room_id, cafe__seller=request.user, final_order__isnull=False)
    if action == "accept":
        form = SellerAcceptForm(
            request.POST, choices=ready_time_choices(), auto_id=f"order-{room_id}-accept-%s"
        )
        error_form = {"accept_form": form}
    else:
        form = SellerRejectForm(request.POST, auto_id=f"order-{room_id}-reject-%s")
        error_form = {"reject_form": form}
    if form.is_valid():
        try:
            changed = decide_order(
                user=request.user, room_id=room_id, action=action, **form.cleaned_data
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            if changed:
                messages.success(
                    request, "주문을 접수했어요." if action == "accept" else "주문을 거절했어요."
                )
            else:
                messages.info(request, "이미 처리되었거나 수락 기한이 지난 주문이에요.")
            return redirect("accounts:seller")
    return render_dashboard(request, error_room=room_id, status=400, **error_form)


@never_cache
@require_POST
@role_required("seller")
def accept(request, room_id):
    return decision(request, room_id, "accept")


@never_cache
@require_POST
@role_required("seller")
def reject(request, room_id):
    return decision(request, room_id, "reject")
