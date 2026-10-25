from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from orders.services import expire_due_rooms

from .views import role_required
from .withdrawal import (
    begin_withdrawal_verification,
    process_unlink_jobs,
    verified_for_withdrawal,
    verify_admin_member,
    withdraw_member,
    withdrawal_problem,
)


class WithdrawalForm(forms.Form):
    confirm = forms.BooleanField(
        label="탈퇴하면 계정과 주문 이력의 연결이 끊어지고 되돌릴 수 없음을 확인했어요."
    )


@never_cache
@require_http_methods(["GET", "POST"])
@role_required("organizer")
def withdraw(request):
    expire_due_rooms()
    enabled = settings.KAKAO_WITHDRAWAL_ENABLED and bool(settings.KAKAO_ADMIN_KEY)
    problem = withdrawal_problem(request.user)
    form = WithdrawalForm(request.POST if request.method == "POST" else None)
    if request.method == "POST":
        if not enabled:
            form.add_error(None, "회원 탈퇴 연결을 준비하고 있어요. 문의하기로 요청해 주세요.")
        elif not verified_for_withdrawal(request):
            form.add_error(None, "카카오로 본인 확인을 먼저 해 주세요. 확인은 5분 동안 유효해요.")
        elif form.is_valid() and not problem:
            uid = request.user.socialaccount_set.get(provider="kakao").uid
            if not verify_admin_member(uid):
                form.add_error(
                    None, "카카오 연결을 확인하지 못해 탈퇴하지 않았어요. 문의하기로 알려 주세요."
                )
                return render(
                    request,
                    "accounts/withdraw.html",
                    {
                        "form": form,
                        "problem": problem,
                        "withdrawal_enabled": enabled,
                        "verified": True,
                    },
                    status=503,
                )
            request.session.pop("withdrawal_verified", None)
            try:
                job_id = withdraw_member(user=request.user)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                request.session.pop("seller_cafe_id", None)
                logout(request)
                # Deletion has already committed. A remote failure remains in the retry queue.
                if job_id is not None:
                    process_unlink_jobs(job_id=job_id)
                return redirect("accounts:withdraw-done")
    return render(
        request,
        "accounts/withdraw.html",
        {
            "form": form,
            "problem": problem,
            "withdrawal_enabled": enabled,
            "verified": verified_for_withdrawal(request),
        },
        status=400 if request.method == "POST" else 200,
    )


@never_cache
@require_POST
@role_required("organizer")
def verify(request):
    if not settings.KAKAO_WITHDRAWAL_ENABLED or not settings.KAKAO_ADMIN_KEY:
        return redirect("accounts:withdraw")
    return begin_withdrawal_verification(request)


@never_cache
@require_GET
def done(request):
    return render(request, "accounts/withdraw_done.html")


@never_cache
@require_POST
@role_required("operator")
def check_connection(request):
    account = request.user.socialaccount_set.filter(provider="kakao").first()
    if (
        settings.KAKAO_WITHDRAWAL_ENABLED
        and account is not None
        and verify_admin_member(account.uid)
    ):
        messages.success(request, "카카오 연결 확인을 통과했어요. 회원 정보는 변경하지 않았어요.")
    else:
        messages.error(
            request, "카카오 연결을 확인하지 못했어요. 어드민 키와 연결 설정을 확인해 주세요."
        )
    return redirect("accounts:privacy-preview")
