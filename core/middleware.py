from . import views


class HealthEndpointMiddleware:
    """Permit platform health probes independently of their internal Host header.

    These two public, read-only responses contain no account or connection details.
    Every other route still goes through Django's normal host/authentication checks.
    SecurityMiddleware wraps this middleware and adds the normal response headers.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        view = {
            "/health/live/": views.liveness,
            "/health/ready/": views.readiness,
        }.get(request.path_info)
        if view is None:
            return self.get_response(request)
        response = view(request)
        response["X-Frame-Options"] = "DENY"
        return response
