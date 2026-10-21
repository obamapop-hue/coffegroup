import json

from django.core.exceptions import RequestDataTooBig, ValidationError
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from accounts.models import User
from accounts.views import role_required

from .models import Cafe
from .sheets import SheetError, cafe_state, catalog_state, save_cafes, save_catalog


def receive(request, save):
    try:
        if request.content_type != "application/json":
            raise ValueError
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError, RequestDataTooBig):
        return JsonResponse(
            {"errors": [{"message": "저장할 표 데이터를 확인해 주세요."}]}, status=400
        )
    try:
        return JsonResponse(save(data))
    except SheetError as error:
        return JsonResponse({"errors": error.errors}, status=error.status)
    except IntegrityError:
        return JsonResponse(
            {
                "errors": [
                    {
                        "message": "다른 곳에서 연결 정보가 변경됐어요. "
                        "입력 내용을 확인한 뒤 최신 표를 불러와 주세요."
                    }
                ]
            },
            status=409,
        )


@never_cache
@require_http_methods(["GET", "POST"])
@role_required("operator")
def cafes(request):
    if request.method == "GET":
        return JsonResponse(cafe_state())
    return receive(request, lambda data: save_cafes(actor=request.user, data=data))


@never_cache
@require_http_methods(["GET", "POST"])
@role_required("operator")
def catalog(request, cafe_id):
    cafe = get_object_or_404(Cafe, pk=cafe_id)
    if request.method == "GET":
        return JsonResponse(catalog_state(cafe))
    return receive(
        request, lambda data: save_catalog(actor=request.user, cafe_id=cafe.pk, data=data)
    )


@never_cache
@require_GET
@role_required("operator")
def seller(request):
    code = request.GET.get("code", "")
    try:
        user = User.objects.filter(account_code=code, is_active=True).first()
    except (ValidationError, ValueError):
        user = None
    if user is None:
        return JsonResponse({"message": "가입한 계정의 연결코드를 확인해 주세요."}, status=404)
    cafe = Cafe.objects.filter(seller=user).first()
    return JsonResponse(
        {
            "nickname": user.display_name,
            "cafe": cafe.name if cafe else "",
            "cafe_id": cafe.pk if cafe else None,
        }
    )
