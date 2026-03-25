"""Waypoint live HTTP test events.

- 航点通过真实 HTTP 完成创建/查询/更新/删除闭环，并同步 route.waypoint_count
- 创建/更新按合同拒绝 disabled route、重复 sequence、空 PATCH、未知字段和无效坐标
- 跨租户 route 绑定按合同拒绝，跨租户航点在当前租户下不可见不可改不可删
- business waypoint API 强制要求有效 Bearer + X-TENANT-CODE
- platform_admin 即使权限矩阵放开也禁止访问租户业务接口
"""

from apps.access.models import AuditLog, DirectoryStatus, EmploymentStatus, Role, ScopeType
from apps.access.test_live_base import LiveIamApiTestCase, User
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding, grant_role_permissions
from apps.route.models import Route, RouteStatus
from apps.waypoint.models import Waypoint


class LiveWaypointApiTestCase(LiveIamApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="waypoint_live_admin", password="pass1234", status=1)
        self.staff = ensure_staff_profile(
            self.user,
            staff_no="WPL-001",
            name="实时航点管理员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="waypoint_live_tenant",
            role_code="waypoint_live_role",
            role_name="实时航点角色",
        )
        grant_role_permissions(
            self.role,
            {
                "waypoint.view_waypoint": ScopeType.ALL,
                "waypoint.manage_waypoint": ScopeType.ALL,
            },
            group_name="waypoint-live-group",
        )

        self.other_user = User.objects.create_user(username="waypoint_live_other_admin", password="pass1234", status=1)
        self.other_staff = ensure_staff_profile(
            self.other_user,
            staff_no="WPL-OTHER-001",
            name="其他租户航点管理员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.other_tenant, self.other_member, self.other_role = ensure_tenant_role_binding(
            self.other_user,
            tenant_code="waypoint_live_other_tenant",
            role_code="waypoint_live_other_role",
            role_name="其他租户实时航点角色",
        )
        grant_role_permissions(
            self.other_role,
            {
                "waypoint.view_waypoint": ScopeType.ALL,
                "waypoint.manage_waypoint": ScopeType.ALL,
            },
            group_name="waypoint-live-other-group",
        )

        self.route_active = self._create_route(name="实时航点航线", status=RouteStatus.ACTIVE)
        self.route_disabled = self._create_route(name="禁用航点航线", status=RouteStatus.DISABLED)
        self.other_route = self._create_route(
            name="其他租户航点航线",
            status=RouteStatus.ACTIVE,
            tenant=self.other_tenant,
        )
        self.login(username="waypoint_live_admin", password="pass1234", tenant_code=self.tenant.code)

    def _create_route(self, *, name: str, status: int = RouteStatus.ACTIVE, tenant=None) -> Route:
        tenant = tenant or self.tenant
        creator_name = self.staff.name if tenant == self.tenant else self.other_staff.name
        return Route.objects.create(
            tenant=tenant,
            name=name,
            status=status,
            creator_name=creator_name,
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


class LiveWaypointApiTests(LiveWaypointApiTestCase):
    def test_waypoint_lifecycle_should_follow_live_http_contract(self):
        first_create_response = self.client.post(
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
        self.assertEqual(first_create_response.status_code, 201)
        self.assertEqual(first_create_response.json()["code"], "00000")
        first_waypoint_id = first_create_response.json()["data"]["id"]

        second_create_response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 2,
                "latitude": "22.28622345",
                "longitude": "113.56791234",
                "altitude": "121.50",
            },
            format="json",
        )
        self.assertEqual(second_create_response.status_code, 201)
        self.assertEqual(second_create_response.json()["code"], "00000")
        second_data = second_create_response.json()["data"]
        self.assertEqual(second_data["route"], self.route_active.id)
        self.assertEqual(second_data["sequence"], 2)
        waypoint_id = second_data["id"]

        self.route_active.refresh_from_db()
        self.assertEqual(self.route_active.waypoint_count, 2)

        list_response = self.client.get(
            "/api/v1/waypoints",
            {"route_id": self.route_active.id, "sequence": 2},
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 1)
        self.assertEqual(list_response.json()["data"]["list"][0]["id"], waypoint_id)

        retrieve_response = self.client.get(f"/api/v1/waypoints/{waypoint_id}")
        self.assertEqual(retrieve_response.status_code, 200)
        self.assertEqual(retrieve_response.json()["code"], "00000")
        self.assertEqual(retrieve_response.json()["data"]["route"], self.route_active.id)
        self.assertEqual(retrieve_response.json()["data"]["sequence"], 2)

        put_response = self.client.put(
            f"/api/v1/waypoints/{waypoint_id}",
            {
                "sequence": 20,
                "latitude": "22.28632345",
                "longitude": "113.56801234",
                "altitude": "130.00",
            },
            format="json",
        )
        self.assertEqual(put_response.status_code, 200)
        self.assertEqual(put_response.json()["code"], "00000")
        self.assertEqual(put_response.json()["data"]["sequence"], 20)
        self.assertEqual(put_response.json()["data"]["longitude"], "113.56801234")

        patch_response = self.client.patch(
            f"/api/v1/waypoints/{waypoint_id}",
            {"sequence": 21, "altitude": "135.50"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["code"], "00000")
        self.assertEqual(patch_response.json()["data"]["sequence"], 21)
        self.assertEqual(patch_response.json()["data"]["altitude"], "135.50")

        delete_response = self.client.delete(f"/api/v1/waypoints/{waypoint_id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()["code"], "00000")
        self.assertEqual(delete_response.json()["data"]["id"], waypoint_id)
        self.assertTrue(delete_response.json()["data"]["deleted"])

        deleted_retrieve_response = self.client.get(f"/api/v1/waypoints/{waypoint_id}")
        self.assertEqual(deleted_retrieve_response.status_code, 404)
        self.assertEqual(deleted_retrieve_response.json()["code"], "C0404")

        self.route_active.refresh_from_db()
        self.assertEqual(self.route_active.waypoint_count, 1)
        self.assertTrue(Waypoint.objects.filter(id=first_waypoint_id).exists())
        self.assertFalse(Waypoint.objects.filter(id=waypoint_id).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="WAYPOINT_CREATE",
                target_type="waypoint",
                target_id=str(waypoint_id),
            ).exists()
        )
        self.assertEqual(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="WAYPOINT_UPDATE",
                target_type="waypoint",
                target_id=str(waypoint_id),
            ).count(),
            2,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="WAYPOINT_DELETE",
                target_type="waypoint",
                target_id=str(waypoint_id),
            ).exists()
        )

    def test_boundary_values_should_be_accepted_over_live_http(self):
        min_boundary_response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 0,
                "latitude": "-90.00000000",
                "longitude": "0.00000000",
                "altitude": "0.00",
            },
            format="json",
        )
        self.assertEqual(min_boundary_response.status_code, 201)
        self.assertEqual(min_boundary_response.json()["code"], "00000")
        self.assertEqual(min_boundary_response.json()["data"]["sequence"], 0)
        self.assertEqual(min_boundary_response.json()["data"]["latitude"], "-90.00000000")
        self.assertEqual(min_boundary_response.json()["data"]["altitude"], "0.00")

        max_boundary_response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 99999,
                "latitude": "90.00000000",
                "longitude": "180.00000000",
                "altitude": "999999.99",
            },
            format="json",
        )
        self.assertEqual(max_boundary_response.status_code, 201)
        self.assertEqual(max_boundary_response.json()["code"], "00000")
        self.assertEqual(max_boundary_response.json()["data"]["sequence"], 99999)
        self.assertEqual(max_boundary_response.json()["data"]["longitude"], "180.00000000")
        self.assertEqual(max_boundary_response.json()["data"]["altitude"], "999999.99")

        list_response = self.client.get("/api/v1/waypoints", {"route_id": self.route_active.id})
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 2)

        self.route_active.refresh_from_db()
        self.assertEqual(self.route_active.waypoint_count, 2)

    def test_invalid_create_contract_should_be_rejected_over_live_http(self):
        self._create_waypoint(route=self.route_active, sequence=8)

        missing_field_response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 1,
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(missing_field_response.status_code, 400)
        self.assertEqual(missing_field_response.json()["code"], "B0001")
        self.assertIn("latitude", missing_field_response.json()["data"])

        invalid_coordinate_response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 2,
                "latitude": "invalid",
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(invalid_coordinate_response.status_code, 400)
        self.assertEqual(invalid_coordinate_response.json()["code"], "B0001")
        self.assertIn("latitude", invalid_coordinate_response.json()["data"])

        unknown_field_response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 3,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "120.50",
                "created_at": "2026-03-21T08:00:00+08:00",
            },
            format="json",
        )
        self.assertEqual(unknown_field_response.status_code, 400)
        self.assertEqual(unknown_field_response.json()["code"], "B0001")
        self.assertIn("created_at", unknown_field_response.json()["data"])

        disabled_route_response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_disabled.id,
                "sequence": 4,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(disabled_route_response.status_code, 400)
        self.assertEqual(disabled_route_response.json()["code"], "B0001")
        self.assertIn("route", disabled_route_response.json()["data"])

        nonexistent_route_response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": 999999,
                "sequence": 5,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(nonexistent_route_response.status_code, 400)
        self.assertEqual(nonexistent_route_response.json()["code"], "B0001")
        self.assertIn("route", nonexistent_route_response.json()["data"])

        duplicate_response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 8,
                "latitude": "22.28612346",
                "longitude": "113.56781235",
                "altitude": "121.00",
            },
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, 400)
        self.assertEqual(duplicate_response.json()["code"], "C0101")
        self.assertTrue(
            "sequence" in duplicate_response.json()["data"]
            or "non_field_errors" in duplicate_response.json()["data"]
        )

    def test_invalid_patch_contract_should_be_rejected_over_live_http(self):
        first_waypoint = self._create_waypoint(route=self.route_active, sequence=10)
        second_waypoint = self._create_waypoint(route=self.route_active, sequence=11)

        empty_patch_response = self.client.patch(
            f"/api/v1/waypoints/{first_waypoint.id}",
            {},
            format="json",
        )
        self.assertEqual(empty_patch_response.status_code, 400)
        self.assertEqual(empty_patch_response.json()["code"], "B0001")
        self.assertIn("non_field_errors", empty_patch_response.json()["data"])

        unknown_field_patch_response = self.client.patch(
            f"/api/v1/waypoints/{first_waypoint.id}",
            {"route": self.route_disabled.id},
            format="json",
        )
        self.assertEqual(unknown_field_patch_response.status_code, 400)
        self.assertEqual(unknown_field_patch_response.json()["code"], "B0001")
        self.assertIn("route", unknown_field_patch_response.json()["data"])

        duplicate_patch_response = self.client.patch(
            f"/api/v1/waypoints/{second_waypoint.id}",
            {"sequence": 10},
            format="json",
        )
        self.assertEqual(duplicate_patch_response.status_code, 400)
        self.assertEqual(duplicate_patch_response.json()["code"], "C0101")
        self.assertIn("sequence", duplicate_patch_response.json()["data"])

        self.route_active.status = RouteStatus.DISABLED
        self.route_active.save(update_fields=["status", "updated_at"])

        disabled_route_patch_response = self.client.patch(
            f"/api/v1/waypoints/{first_waypoint.id}",
            {"altitude": "130.00"},
            format="json",
        )
        self.assertEqual(disabled_route_patch_response.status_code, 400)
        self.assertEqual(disabled_route_patch_response.json()["code"], "B0001")
        self.assertIn("non_field_errors", disabled_route_patch_response.json()["data"])

    def test_cross_tenant_waypoints_should_be_invisible_and_unmodifiable_over_live_http(self):
        foreign_waypoint = self._create_waypoint(route=self.other_route, sequence=1)

        cross_tenant_create_response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.other_route.id,
                "sequence": 2,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(cross_tenant_create_response.status_code, 400)
        self.assertEqual(cross_tenant_create_response.json()["code"], "B0001")
        self.assertIn("route", cross_tenant_create_response.json()["data"])

        list_response = self.client.get("/api/v1/waypoints", {"route_id": self.other_route.id})
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 0)

        retrieve_response = self.client.get(f"/api/v1/waypoints/{foreign_waypoint.id}")
        self.assertEqual(retrieve_response.status_code, 404)
        self.assertEqual(retrieve_response.json()["code"], "C0404")

        patch_response = self.client.patch(
            f"/api/v1/waypoints/{foreign_waypoint.id}",
            {"altitude": "222.22"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 404)
        self.assertEqual(patch_response.json()["code"], "C0404")

        delete_response = self.client.delete(f"/api/v1/waypoints/{foreign_waypoint.id}")
        self.assertEqual(delete_response.status_code, 404)
        self.assertEqual(delete_response.json()["code"], "C0404")
        self.assertTrue(Waypoint.objects.filter(id=foreign_waypoint.id).exists())

    def test_business_waypoint_api_should_require_tenant_context(self):
        waypoint = self._create_waypoint(route=self.route_active, sequence=30)
        tenantless_client = self.new_client()
        self.authenticate_client(
            tenantless_client,
            username="waypoint_live_admin",
            password="pass1234",
        )

        list_response = tenantless_client.get("/api/v1/waypoints")
        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(list_response.json()["code"], "A0403")

        detail_response = tenantless_client.get(f"/api/v1/waypoints/{waypoint.id}")
        self.assertEqual(detail_response.status_code, 403)
        self.assertEqual(detail_response.json()["code"], "A0403")

        create_response = tenantless_client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 31,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(create_response.json()["code"], "A0403")

    def test_platform_admin_should_be_blocked_from_business_waypoint_api_even_with_permission(self):
        platform_role, _ = Role.objects.update_or_create(
            code="platform_admin",
            defaults={"name": "平台管理员", "status": DirectoryStatus.ACTIVE},
        )
        grant_role_permissions(
            platform_role,
            {
                "waypoint.view_waypoint": ScopeType.ALL,
                "waypoint.manage_waypoint": ScopeType.ALL,
            },
            group_name="waypoint-live-platform-group",
        )
        platform_user = User.objects.create_user(
            username="waypoint_live_platform_admin",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        ensure_staff_profile(platform_user, name="平台航点管理员")
        platform_client = self.new_client()
        self.authenticate_client(
            platform_client,
            username="waypoint_live_platform_admin",
            password="pass1234",
            tenant_code=self.tenant.code,
        )

        response = platform_client.get("/api/v1/waypoints")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "A0403")
        self.assertEqual(response.json()["msg"], "平台管理员不可访问租户业务接口")
