from django.urls import path

from apps.system_v2 import views


urlpatterns = [
    path("menus/current", views.CurrentMenuTreeView.as_view(), name="v2-system-menus-current"),
    path("menus/tree", views.MenuTreeView.as_view(), name="v2-system-menus-tree"),
    path("menus", views.MenuListCreateView.as_view(), name="v2-system-menus"),
    path("menus/<int:id>", views.MenuDetailView.as_view(), name="v2-system-menu-detail"),
    path("operation-logs", views.OperationLogListView.as_view(), name="v2-system-operation-logs"),
    path("login-logs", views.LoginLogListView.as_view(), name="v2-system-login-logs"),
    path("file-logs", views.FileLogListView.as_view(), name="v2-system-file-logs"),
]
