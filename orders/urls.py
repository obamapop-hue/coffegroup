from django.urls import path

from . import notification_views, seller_views, views

app_name = "orders"
urlpatterns = [
    path("notifications/", notification_views.feed, name="notifications"),
    path("notifications/<int:notice_id>/read/", notification_views.read, name="notification-read"),
    path("seller/feed/", seller_views.feed, name="seller-feed"),
    path("seller/<int:room_id>/accept/", seller_views.accept, name="seller-accept"),
    path("seller/<int:room_id>/reject/", seller_views.reject, name="seller-reject"),
    path("seller/<int:room_id>/complete/", seller_views.complete, name="seller-complete"),
    path("create/<int:cafe_id>/", views.create, name="create"),
    path("<int:room_id>/", views.detail, name="detail"),
    path("<int:room_id>/delete/", views.delete, name="delete"),
    path("<int:room_id>/close/", views.close, name="close"),
    path("<int:room_id>/confirm/", views.confirm, name="confirm"),
    path("<int:room_id>/send/", views.send, name="send"),
    path("<int:room_id>/cancel/", views.cancel, name="cancel"),
    path("<int:room_id>/state/", views.state, name="state"),
    path("<int:room_id>/items/add/", views.order_add, name="order-add"),
    path("<int:room_id>/items/<int:submission_id>/edit/", views.order_edit, name="order-edit"),
    path(
        "<int:room_id>/items/<int:submission_id>/delete/", views.order_delete, name="order-delete"
    ),
]
