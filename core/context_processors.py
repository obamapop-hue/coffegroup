from django.conf import settings
from django.templatetags.static import static
from django.urls import reverse

from cafes.models import Cafe


def site(request):
    return {
        "site_url": request.build_absolute_uri(reverse("home")),
        "share_image_url": request.build_absolute_uri(static("images/yeongcha-share.png")),
        "kakao_ready": settings.KAKAO_LOGIN_ENABLED,
        "can_access_seller": (
            request.user.is_authenticated
            and request.user.is_active
            and Cafe.objects.filter(seller=request.user).exists()
        ),
    }
