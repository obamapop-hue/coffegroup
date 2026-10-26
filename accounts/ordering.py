"""Fresh ordering permission checks, independent of login and seller fulfillment."""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from .models import User


class OrderingSuspended(PermissionDenied):
    pass


def ordering_member(user, *, lock=True):
    if not user or not user.is_authenticated or not user.is_active:
        raise PermissionDenied
    query = User.objects.all()
    if lock:
        query = query.select_for_update(no_key=True)
    member = query.filter(pk=user.pk, is_active=True).first()
    if member is None:
        raise PermissionDenied
    if member.ordering_suspended:
        raise OrderingSuspended
    return member


def ordering_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            if request.user.is_authenticated:
                ordering_member(request.user, lock=False)
            return view(request, *args, **kwargs)
        except OrderingSuspended:
            request.user.ordering_suspended = True
            return render(request, "accounts/ordering_suspended.html", status=403)

    return wrapped
