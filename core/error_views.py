from django.http import HttpResponse
from django.template.loader import render_to_string


def error_response(status):
    # Error pages must not run request context processors: even the DB or Host may be invalid.
    response = HttpResponse(render_to_string(f"{status}.html"), status=status)
    response["Cache-Control"] = "no-store"
    return response


def bad_request(request, exception):
    return error_response(400)


def permission_denied(request, exception):
    return error_response(403)


def not_found(request, exception):
    return error_response(404)


def server_error(request):
    return error_response(500)
