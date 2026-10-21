from django.urls import path

from . import sheet_views, views

app_name = "cafes"
urlpatterns = [
    path("sheets/cafes/", sheet_views.cafes, name="sheet-cafes"),
    path("sheets/seller/", sheet_views.seller, name="sheet-seller"),
    path("<int:cafe_id>/sheet/", sheet_views.catalog, name="sheet-catalog"),
    path("<int:cafe_id>/manage/", views.manage, name="manage"),
    path("<int:cafe_id>/menus/new/", views.menu_edit, name="menu-create"),
    path("<int:cafe_id>/menus/<int:menu_id>/", views.menu_edit, name="menu-edit"),
    path("<int:cafe_id>/menus/<int:menu_id>/options/new/", views.option_edit, name="option-create"),
    path(
        "<int:cafe_id>/menus/<int:menu_id>/options/<int:group_id>/",
        views.option_edit,
        name="option-edit",
    ),
    path(
        "<int:cafe_id>/menus/<int:menu_id>/options/<int:group_id>/delete/",
        views.option_delete,
        name="option-delete",
    ),
    path("stock/<int:menu_id>/", views.stock, name="stock"),
]
