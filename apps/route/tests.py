from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import Resolver404, resolve
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.management.commands.seed_role_permissions import ROLE_PERMISSION_MATRIX
from apps.access.models import RolePermissionGrant
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.dji_bff.models import TenantRouteIndex
from apps.dji_mock.state import mock_dji_state
from apps.dji_mock.test_support import MockDjiUpstreamTestMixin
from apps.route.models import Route, RouteType
from apps.waypoint.models import Waypoint

User = get_user_model()


class RouteApiTests(MockDjiUpstreamTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = User.objects.create_user(username="route_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="航线管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="route_test_tenant",
            role_code="route_test_role",
            role_name="航线测试角色",
        )
        grant_role_permissions(
            self.role,
            {
                "route.view_route": ScopeType.ALL,
                "route.manage_route": ScopeType.ALL,
            },
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def test_create_should_accept_nested_waypoints_and_mark_route_unpublished(self):
        payload = {
            "name": "城市巡检航线",
            "route_type": RouteType.PENDING_EXTENSION,
            "waypoints": [
                {"sequence": 1, "latitude": 31.5, "longitude": 121.5, "altitude": 100},
                {"sequence": 2, "latitude": 31.6, "longitude": 121.6, "altitude": 120},
            ],
        }

        response = self.client.post("/api/v1/routes", payload, format="json")

        self.assertEqual(response.status_code, 201)
        data = response.data["data"]
        route = Route.objects.get(id=data["id"])
        self.assertEqual(route.waypoint_count, 2)
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertFalse(route_index.is_published)

    def test_create_should_allow_multiple_unpublished_draft_routes_per_tenant(self):
        first_response = self.client.post(
            "/api/v1/routes",
            {
                "name": "草稿航线A",
                "route_type": RouteType.PENDING_EXTENSION,
                "waypoints": [
                    {"sequence": 1, "latitude": 31.50000000, "longitude": 121.50000000, "altitude": 100},
                ],
            },
            format="json",
        )
        second_response = self.client.post(
            "/api/v1/routes",
            {
                "name": "草稿航线B",
                "route_type": RouteType.PENDING_EXTENSION,
                "waypoints": [
                    {"sequence": 1, "latitude": 31.60000000, "longitude": 121.60000000, "altitude": 120},
                ],
            },
            format="json",
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 201)
        self.assertEqual(Route.objects.filter(tenant=self.tenant).count(), 2)
        self.assertEqual(
            TenantRouteIndex.objects.filter(
                tenant=self.tenant,
                dji_wayline_id="",
                is_published=False,
            ).count(),
            2,
        )

    def test_detail_should_return_nested_waypoints_in_sequence_order(self):
        route = Route.objects.create(
            tenant=self.tenant,
            name="细节航线",
            route_type=RouteType.PENDING_EXTENSION,
            creator_name="管理员",
        )
        route_index = TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="",
        )
        route_index.is_published = False
        Waypoint.objects.create(
            route=route,
            sequence=2,
            latitude=Decimal("31.5"),
            longitude=Decimal("121.5"),
            altitude=Decimal("80"),
        )
        Waypoint.objects.create(
            route=route,
            sequence=1,
            latitude=Decimal("31.6"),
            longitude=Decimal("121.6"),
            altitude=Decimal("90"),
        )

        response = self.client.get(f"/api/v1/routes/{route.id}")
        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertIn("waypoints", data)
        self.assertEqual([point["sequence"] for point in data["waypoints"]], [1, 2])

    def test_publish_should_upload_current_route_and_mark_published(self):
        route = Route.objects.create(
            tenant=self.tenant,
            name="待发布航线",
            route_type=RouteType.PENDING_EXTENSION,
            creator_name="管理员",
            waypoint_count=1,
        )
        Waypoint.objects.create(
            route=route,
            sequence=1,
            latitude=Decimal("31.7"),
            longitude=Decimal("121.7"),
            altitude=Decimal("100"),
        )
        route_index = TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="",
        )
        route_index.is_published = False

        response = self.client.post(f"/api/v1/routes/{route.id}/publish")

        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertTrue(data["is_published"])
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertTrue(route_index.is_published)
        self.assertTrue(route_index.dji_wayline_id.startswith("mock-wayline-"))

    def test_update_with_waypoints_should_replace_full_waypoint_set(self):
        route = Route.objects.create(
            tenant=self.tenant,
            name="待替换航线",
            route_type=RouteType.PENDING_EXTENSION,
            creator_name="管理员",
            waypoint_count=2,
        )
        Waypoint.objects.create(
            route=route,
            sequence=1,
            latitude=Decimal("31.10000000"),
            longitude=Decimal("121.10000000"),
            altitude=Decimal("80.00"),
        )
        Waypoint.objects.create(
            route=route,
            sequence=2,
            latitude=Decimal("31.20000000"),
            longitude=Decimal("121.20000000"),
            altitude=Decimal("90.00"),
        )
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="mock-wayline-existing",
            is_published=True,
        )

        response = self.client.put(
            f"/api/v1/routes/{route.id}",
            {
                "name": "已替换航线",
                "route_type": RouteType.PENDING_EXTENSION,
                "waypoints": [
                    {"sequence": 1, "latitude": "32.10000000", "longitude": "122.10000000", "altitude": "100.00"},
                    {"sequence": 2, "latitude": "32.20000000", "longitude": "122.20000000", "altitude": "110.00"},
                    {"sequence": 3, "latitude": "32.30000000", "longitude": "122.30000000", "altitude": "120.00"},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        route.refresh_from_db()
        self.assertEqual(route.name, "已替换航线")
        self.assertEqual(route.waypoint_count, 3)
        self.assertEqual(
            list(route.waypoint_rows.order_by("sequence").values_list("sequence", "latitude", "longitude", "altitude")),
            [
                (1, Decimal("32.10000000"), Decimal("122.10000000"), Decimal("100.00")),
                (2, Decimal("32.20000000"), Decimal("122.20000000"), Decimal("110.00")),
                (3, Decimal("32.30000000"), Decimal("122.30000000"), Decimal("120.00")),
            ],
        )
        self.assertFalse(TenantRouteIndex.objects.get(route=route).is_published)

    def test_patch_without_waypoints_should_keep_existing_waypoint_rows(self):
        route = Route.objects.create(
            tenant=self.tenant,
            name="局部更新航线",
            route_type=RouteType.PENDING_EXTENSION,
            creator_name="管理员",
            waypoint_count=2,
        )
        Waypoint.objects.create(
            route=route,
            sequence=1,
            latitude=Decimal("31.30000000"),
            longitude=Decimal("121.30000000"),
            altitude=Decimal("85.00"),
        )
        Waypoint.objects.create(
            route=route,
            sequence=2,
            latitude=Decimal("31.40000000"),
            longitude=Decimal("121.40000000"),
            altitude=Decimal("95.00"),
        )
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="mock-wayline-existing",
            is_published=True,
        )

        response = self.client.patch(
            f"/api/v1/routes/{route.id}",
            {"name": "仅更新名称"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        route.refresh_from_db()
        self.assertEqual(route.name, "仅更新名称")
        self.assertEqual(route.waypoint_count, 2)
        self.assertEqual(
            list(route.waypoint_rows.order_by("sequence").values_list("sequence", "latitude", "longitude", "altitude")),
            [
                (1, Decimal("31.30000000"), Decimal("121.30000000"), Decimal("85.00")),
                (2, Decimal("31.40000000"), Decimal("121.40000000"), Decimal("95.00")),
            ],
        )
        self.assertFalse(TenantRouteIndex.objects.get(route=route).is_published)

    def test_download_should_reject_unpublished_route(self):
        route = Route.objects.create(
            tenant=self.tenant,
            name="未发布航线",
            route_type=RouteType.PENDING_EXTENSION,
            creator_name="管理员",
        )
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="",
            is_published=False,
        )

        response = self.client.get(f"/api/v1/routes/{route.id}/download")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "C0201")

    def test_publish_should_replace_old_upstream_wayline_after_success(self):
        route = Route.objects.create(
            tenant=self.tenant,
            name="重复发布航线",
            route_type=RouteType.PENDING_EXTENSION,
            creator_name="管理员",
            waypoint_count=1,
        )
        Waypoint.objects.create(
            route=route,
            sequence=1,
            latitude=Decimal("31.80000000"),
            longitude=Decimal("121.80000000"),
            altitude=Decimal("100.00"),
        )
        old_upstream = mock_dji_state.create_wayline(name="legacy-upstream")["wayline_id"]
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=old_upstream,
            is_published=True,
        )

        response = self.client.post(f"/api/v1/routes/{route.id}/publish")

        self.assertEqual(response.status_code, 200)
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertNotEqual(route_index.dji_wayline_id, old_upstream)
        self.assertTrue(route_index.is_published)
        self.assertNotIn(old_upstream, mock_dji_state.waylines)
        self.assertEqual(len(mock_dji_state.waylines), 1)
        uploaded = next(iter(mock_dji_state.waylines.values()))
        self.assertTrue(uploaded["name"].startswith(f"route-{route.id}-"))

    def test_old_waypoint_endpoints_should_be_removed(self):
        response = self.client.get("/api/v1/waypoints")

        self.assertEqual(response.status_code, 404)

    def test_waypoint_permissions_should_no_longer_be_seeded(self):
        self.assertFalse(
            any(
                permission_code.startswith("waypoint.")
                for mapping in ROLE_PERMISSION_MATRIX.values()
                for permission_code in mapping
            )
        )

        call_command("seed_role_permissions", mode="replace", stdout=StringIO())

        self.assertFalse(RolePermissionGrant.objects.filter(permission__code__startswith="waypoint.").exists())

    def test_api_v1_should_not_mount_public_waypoint_urls(self):
        with self.assertRaises(Resolver404):
            resolve("/api/v1/waypoints")

    def test_route_model_should_not_expose_status_field(self):
        self.assertNotIn("status", [field.name for field in Route._meta.fields])

    def test_route_index_should_use_boolean_publish_flag(self):
        route = Route.objects.create(tenant=self.tenant, name="索引航线", creator_name="管理员")
        route_index = TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="",
            is_published=False,
        )
        self.assertFalse(route_index.is_published)
