from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from accounts.views import role_required

from .catalog import add_basic_options
from .forms import CafeForm, ChoicesFormSet, MenuForm, OptionGroupForm
from .models import Cafe, Menu, OptionGroup
from .sheets import catalog_state


@never_cache
@require_http_methods(["GET", "POST"])
@role_required("operator")
@transaction.atomic
def manage(request, cafe_id):
    cafe = get_object_or_404(Cafe.objects.select_for_update(), pk=cafe_id)
    form = CafeForm(request.POST or None, instance=cafe)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "카페 정보를 저장했어요.")
        return redirect("cafes:manage", cafe_id=cafe.pk)
    return render(
        request,
        "cafes/manage.html",
        {
            "cafe": cafe,
            "form": form,
            "menus": cafe.menus.all().prefetch_related("option_groups"),
            "menu_form": MenuForm(),
            "sheet_data": catalog_state(cafe),
        },
        status=400 if request.method == "POST" else 200,
    )


@never_cache
@require_http_methods(["GET", "POST"])
@role_required("operator")
@transaction.atomic
def menu_edit(request, cafe_id, menu_id=None):
    cafe = get_object_or_404(Cafe.objects.select_for_update(), pk=cafe_id)
    menu = get_object_or_404(Menu, pk=menu_id, cafe=cafe) if menu_id else Menu(cafe=cafe)
    form = MenuForm(request.POST or None, instance=menu)
    if request.method == "POST" and form.is_valid():
        is_new = menu.pk is None
        menu = form.save()
        if is_new:
            add_basic_options(menu)
        messages.success(request, "메뉴를 저장했어요. 기존 주문방의 메뉴판은 그대로 유지돼요.")
        return redirect("cafes:manage", cafe_id=cafe.pk)
    return render(
        request,
        "cafes/menu_form.html",
        {"cafe": cafe, "form": form},
        status=400 if request.method == "POST" else 200,
    )


@never_cache
@require_http_methods(["GET", "POST"])
@role_required("operator")
@transaction.atomic
def option_edit(request, cafe_id, menu_id, group_id=None):
    cafe = get_object_or_404(Cafe.objects.select_for_update(), pk=cafe_id)
    menu = get_object_or_404(Menu, pk=menu_id, cafe=cafe)
    group = (
        get_object_or_404(OptionGroup, pk=group_id, menu=menu)
        if group_id
        else OptionGroup(menu=menu)
    )
    form = OptionGroupForm(request.POST or None, instance=group)
    valid = form.is_valid() if request.method == "POST" else False
    choices = ChoicesFormSet(request.POST or None, instance=group)
    if request.method == "POST" and valid and choices.is_valid():
        form.save()
        choices.save()
        messages.success(request, "옵션과 기본값을 저장했어요.")
        return redirect("cafes:manage", cafe_id=cafe.pk)
    return render(
        request,
        "cafes/option_form.html",
        {"cafe": cafe, "menu": menu, "form": form, "choices": choices},
        status=400 if request.method == "POST" else 200,
    )


@never_cache
@require_POST
@role_required("operator")
@transaction.atomic
def option_delete(request, cafe_id, menu_id, group_id):
    cafe = get_object_or_404(Cafe.objects.select_for_update(), pk=cafe_id)
    menu = get_object_or_404(Menu, pk=menu_id, cafe=cafe)
    group = get_object_or_404(OptionGroup, pk=group_id, menu=menu)
    group.delete()
    messages.success(request, "옵션을 삭제했어요. 기존 주문방에는 영향이 없어요.")
    return redirect("cafes:manage", cafe_id=cafe.pk)


@never_cache
@require_POST
@role_required("seller")
@transaction.atomic
def stock(request, menu_id):
    cafe = get_object_or_404(Cafe.objects.select_for_update(), seller=request.user)
    menu = get_object_or_404(Menu, pk=menu_id, cafe=cafe, is_active=True)
    if request.POST.get("available") not in {"yes", "no"}:
        return render(request, "cafes/stock_error.html", status=400)
    menu.is_available = request.POST["available"] == "yes"
    menu.save(update_fields=["is_available"])
    messages.success(request, "품절 상태를 변경했어요. 기존 주문방의 메뉴판은 유지돼요.")
    return redirect("accounts:seller")
