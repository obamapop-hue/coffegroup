import hashlib
import secrets

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from accounts.views import role_required

from .forms import MessageForm, TicketForm
from .models import Ticket
from .services import add_message, create_ticket, own_tickets


def guest_identity(request, *, create=False):
    token = request.session.get("support_access")
    if token is None and create and not request.user.is_authenticated:
        token = secrets.token_urlsafe(32)
        request.session["support_access"] = token
    return hashlib.sha256(token.encode()).hexdigest() if isinstance(token, str) else ""


@never_cache
@require_http_methods(["GET", "POST"])
def inbox(request):
    guest_hash = guest_identity(request, create=True)
    form = TicketForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            ticket = create_ticket(user=request.user, guest_hash=guest_hash, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            return redirect("support:detail", ticket_id=ticket.pk)
    tickets = Paginator(own_tickets(request.user, guest_hash), 20).get_page(request.GET.get("page"))
    return render(
        request,
        "support/inbox.html",
        {"form": form, "tickets": tickets},
        status=400 if request.method == "POST" else 200,
    )


def conversation(request, ticket_id, *, operator=False):
    guest_hash = guest_identity(request)
    queryset = Ticket.objects.all() if operator else own_tickets(request.user, guest_hash)
    ticket = get_object_or_404(queryset, pk=ticket_id)
    form = MessageForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            add_message(
                user=request.user,
                guest_hash=guest_hash,
                ticket_id=ticket.pk,
                as_operator=operator,
                **form.cleaned_data,
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            return redirect(
                "support:operator-detail" if operator else "support:detail", ticket_id=ticket.pk
            )
    return render(
        request,
        "support/conversation.html",
        {
            "ticket": ticket,
            "thread": ticket.messages.all(),
            "form": form,
            "operator_view": operator,
        },
        status=400 if request.method == "POST" else 200,
    )


@never_cache
@require_http_methods(["GET", "POST"])
def detail(request, ticket_id):
    return conversation(request, ticket_id)


@never_cache
@require_GET
@role_required("operator")
def operator_inbox(request):
    tickets = Ticket.objects.select_related("owner").all()
    status = request.GET.get("status", "waiting")
    if status in Ticket.Status.values:
        tickets = tickets.filter(status=status)
    page = Paginator(tickets, 20).get_page(request.GET.get("page"))
    return render(request, "support/operator.html", {"tickets": page, "status": status})


@never_cache
@require_http_methods(["GET", "POST"])
@role_required("operator")
def operator_detail(request, ticket_id):
    return conversation(request, ticket_id, operator=True)
