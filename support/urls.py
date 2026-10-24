from django.urls import path

from . import views

app_name = "support"
urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("manage/", views.operator_inbox, name="operator-inbox"),
    path("manage/<int:ticket_id>/", views.operator_detail, name="operator-detail"),
    path("<int:ticket_id>/", views.detail, name="detail"),
]
