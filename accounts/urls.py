from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("entry/organizer/", views.entry, {"role": "organizer"}, name="entry-organizer"),
    path("entry/seller/", views.entry, {"role": "seller"}, name="entry-seller"),
    path("entry/operator/", views.entry, {"role": "operator"}, name="entry-operator"),
    path("start/<str:role>/", views.start, name="start"),
    path("denied/<str:role>/", views.denied, name="denied"),
    path("login-error/", views.login_error, name="login-error"),
    path("logout/", views.logout, name="logout"),
    path("profile/", views.profile, name="profile"),
    path("organizer/", views.organizer, name="organizer"),
    path("seller/", views.seller, name="seller"),
    path("operator/", views.operator, name="operator"),
    path("operator/cafes/create/", views.cafe_create, name="cafe-create"),
    path("operator/cafes/<int:cafe_id>/seller/", views.seller_link, name="seller-link"),
    path("operator/cafes/<int:cafe_id>/unlink/", views.seller_unlink, name="seller-unlink"),
]
