"""Read-only operator history, using final snapshots after transmission."""

from urllib.parse import urlencode

from django import forms
from django.core.paginator import Paginator
from django.db.models import (
    BigIntegerField,
    Case,
    CharField,
    F,
    OuterRef,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce, NullIf
from django.urls import reverse

from cafes.models import Cafe
from orders.models import Room, Submission


class OrderHistoryFilter(forms.Form):
    cafe = forms.TypedChoiceField(
        required=False, coerce=int, empty_value=None, widget=forms.HiddenInput
    )
    name = forms.CharField(
        label="회원 이름(닉네임)",
        required=False,
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "주문한 총무의 닉네임"}),
    )
    date = forms.DateField(
        label="주문 날짜",
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )

    def __init__(self, data, cafes):
        super().__init__(data)
        self.fields["cafe"].choices = [("", "전체 카페"), *[(c.pk, c.name) for c in cafes]]


def history_rooms():
    collected = (
        Submission.objects.filter(room_id=OuterRef("pk"), deleted_at__isnull=True)
        .order_by()
        .values("room_id")
        .annotate(quantity=Sum("total_quantity"), amount=Sum("total_amount"))
    )
    return (
        Room.objects.select_related("cafe")
        .defer("menu_snapshot")
        .annotate(
            history_nickname=Coalesce(
                NullIf(F("final_order__organizer_nickname"), Value("")),
                Case(
                    When(
                        organizer__isnull=False,
                        then=Coalesce(
                            NullIf(F("organizer__nickname"), Value("")), Value("영차 회원")
                        ),
                    ),
                    default=Value("회원 정보 없음"),
                    output_field=CharField(),
                ),
            ),
            history_quantity=Coalesce(
                F("final_order__total_quantity"),
                Subquery(collected.values("quantity")[:1]),
                Value(0),
                output_field=BigIntegerField(),
            ),
            history_amount=Coalesce(
                F("final_order__total_amount"),
                Subquery(collected.values("amount")[:1]),
                Value(0),
                output_field=BigIntegerField(),
            ),
        )
        .order_by("-created_at", "-pk")
    )


def order_history_context(request):
    cafes = list(Cafe.objects.only("pk", "name").order_by("name", "pk"))
    form = OrderHistoryFilter(request.GET, cafes)
    valid = form.is_valid()
    selected_id = form.cleaned_data.get("cafe")
    rooms = history_rooms() if valid else Room.objects.none()
    if valid:
        if selected_id:
            rooms = rooms.filter(cafe_id=selected_id)
        if name := form.cleaned_data["name"]:
            rooms = rooms.filter(history_nickname__icontains=name)
        if date := form.cleaned_data["date"]:
            rooms = rooms.filter(created_at__date=date)
    base = {"tab": "orders"}
    # Whitelist query keys; cafe links and pagination preserve the entered search.
    for key in ("name", "date"):
        if value := request.GET.get(key, ""):
            base[key] = value
    url = reverse("accounts:operator")
    links = [{"name": "전체 카페", "url": f"{url}?{urlencode(base)}", "selected": not selected_id}]
    links.extend(
        {
            "name": cafe.name,
            "url": f"{url}?{urlencode({**base, 'cafe': cafe.pk})}",
            "selected": cafe.pk == selected_id,
        }
        for cafe in cafes
    )
    selection = {"cafe": selected_id} if selected_id else {}
    params = {**base, **selection}
    return {
        "history_form": form,
        "history_cafes": links,
        "selected_cafe": next((c for c in cafes if c.pk == selected_id), None),
        "history_page": Paginator(rooms, 20).get_page(request.GET.get("page")),
        "history_query": urlencode(params),
        "history_reset_url": f"{url}?{urlencode({'tab': 'orders', **selection})}",
    }
