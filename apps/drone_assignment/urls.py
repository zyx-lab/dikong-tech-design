from django.urls import path

from apps.drone_assignment.views import DroneAssignmentViewSet

drone_assignment_list = DroneAssignmentViewSet.as_view({"get": "list", "post": "create"})
drone_assignment_detail = DroneAssignmentViewSet.as_view({"get": "retrieve"})
drone_assignment_cancel = DroneAssignmentViewSet.as_view({"post": "cancel"})

urlpatterns = [
    path("drone-assignments", drone_assignment_list, name="drone-assignment-list"),
    path("drone-assignments/<int:pk>", drone_assignment_detail, name="drone-assignment-detail"),
    path("drone-assignments/<int:pk>/cancel", drone_assignment_cancel, name="drone-assignment-cancel"),
]
