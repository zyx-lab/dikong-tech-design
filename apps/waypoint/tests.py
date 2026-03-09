from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AuditLog, GroupPermissionScope, ScopeStatus, ScopeType, StaffProfile, StaffType, StaffTypeGroup
from apps.route.models import Route, RouteStatus
from apps.waypoint.models import Waypoint

User = get_user_model()


class WaypointApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff_type = StaffType.objects.create(code="waypoint_admin_test", name="航点管理员", status=1)
        self.user = User.objects.create_user(username="waypoint_admin", password="pass1234", status=1)
        self.staff = StaffProfile.objects.create(
            user=self.user,
            staff_no="W-001",
            name="航点管理员A",
            employment_status=1,
            staff_type=self.staff_type,
        )
        self.route_active = Route.objects.create(name="航点测试航线A", status=RouteStatus.ACTIVE, creator_name=self.staff.name)
        self.route_disabled = Route.objects.create(name="航点测试航线B", status=RouteStatus.DISABLED, creator_name=self.staff.name)

    def _grant_permission(self, permission_code: str):
        app_label, codename = permission_code.split(".", 1)
        perm = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        group = Group.objects.create(name=f"{permission_code}-group")
        group.permissions.add(perm)
        StaffTypeGroup.objects.create(staff_type=self.staff_type, group=group, status=ScopeStatus.ACTIVE)
        GroupPermissionScope.objects.create(
            group=group,
            permission=perm,
            scope_type=ScopeType.ALL,
            status=ScopeStatus.ACTIVE,
        )

    def _create_waypoint(
        self,
        *,
        route: Route,
        sequence: int,
        latitude: str = "22.28612345",
        longitude: str = "113.56781234",
        altitude: str = "120.50",
    ) -> Waypoint:
        return Waypoint.objects.create(
            route=route,
            sequence=sequence,
            latitude=latitude,
            longitude=longitude,
            altitude=altitude,
        )

    def test_create_waypoint_should_return_success(self):
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 1,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["route"], self.route_active.id)
        self.assertEqual(response.data["sequence"], 1)

        waypoint = Waypoint.objects.get(id=response.data["id"])
        self.assertEqual(waypoint.route_id, self.route_active.id)
        self.assertEqual(waypoint.sequence, 1)
        self.route_active.refresh_from_db()
        self.assertEqual(self.route_active.waypoint_count, 1)
        self.assertTrue(
            AuditLog.objects.filter(
                action="WAYPOINT_CREATE",
                target_type="waypoint",
                target_id=str(waypoint.id),
            ).exists()
        )

    def test_create_waypoint_invalid_params_should_return_invalid_params(self):
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 1,
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("latitude", response.data)

    def test_create_waypoint_for_disabled_route_should_return_invalid_params(self):
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_disabled.id,
                "sequence": 1,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("route", response.data)

    def test_create_waypoint_with_duplicate_sequence_should_return_idempotent_duplicate(self):
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)
        Waypoint.objects.create(
            route=self.route_active,
            sequence=1,
            latitude="22.28612345",
            longitude="113.56781234",
            altitude="120.50",
        )
        self.route_active.waypoint_count = 1
        self.route_active.save(update_fields=["waypoint_count", "updated_at"])

        response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 1,
                "latitude": "22.28612346",
                "longitude": "113.56781235",
                "altitude": "121.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "DUPLICATE_REQUEST")
        self.assertIn("non_field_errors", response.data)

    def test_create_waypoint_without_auth_should_return_permission_denied(self):
        response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 1,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_create_waypoint_without_permission_should_return_permission_denied(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 1,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_list_waypoints_should_return_success(self):
        self._grant_permission("waypoint.view_waypoint")
        self.client.force_authenticate(self.user)
        self._create_waypoint(route=self.route_active, sequence=1)
        self._create_waypoint(route=self.route_active, sequence=2, latitude="22.28612346")

        response = self.client.get("/api/v1/waypoints")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertIn("results", response.data)
        self.assertEqual(len(response.data["results"]), 2)

    def test_list_waypoints_with_filter_should_return_filtered_results(self):
        self._grant_permission("waypoint.view_waypoint")
        self.client.force_authenticate(self.user)
        self._create_waypoint(route=self.route_active, sequence=1)
        self._create_waypoint(route=self.route_active, sequence=2)

        response = self.client.get(
            "/api/v1/waypoints",
            {"route_id": self.route_active.id, "sequence": 2},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["sequence"], 2)

    def test_list_waypoints_without_auth_should_return_permission_denied(self):
        response = self.client.get("/api/v1/waypoints")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_list_waypoints_without_permission_should_return_permission_denied(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/v1/waypoints")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})
