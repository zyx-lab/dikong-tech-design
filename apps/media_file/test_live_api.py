"""Media file live HTTP test events.

- 调度员通过真实 HTTP 完成媒体文件创建/查询/更新/逻辑删除闭环
- 媒体列表支持 flight_record/media_type/mission/drone/file_name 过滤，并自动排除已逻辑删除记录
- 跨租户 flight_record 绑定按合同拒绝，跨租户媒体记录在当前租户下不可见不可改不可删
- business media-file API 强制要求有效 Bearer + X-TENANT-CODE
- ASSIGNED 范围飞手只能查看和操作自己飞行记录下的媒体文件
- platform_admin 即使权限矩阵放开也禁止访问租户业务接口
"""

from datetime import timedelta

from django.utils import timezone

from apps.access.models import AuditLog, DirectoryStatus, EmploymentStatus, Role, ScopeType
from apps.access.test_live_base import LiveIamApiTestCase, User
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone, DroneStatus
from apps.flight_record.models import FlightRecord, FlightRecordStatus
from apps.media_file.models import MediaFile, MediaType
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route, RouteStatus


class LiveMediaFileApiTestCase(LiveIamApiTestCase):
    def setUp(self):
        super().setUp()
        self._flight_no_seq = 1

        self.admin_user = User.objects.create_user(username="media_file_live_admin", password="pass1234", status=1)
        ensure_staff_profile(
            self.admin_user,
            staff_no="MFL-001",
            name="实时媒体管理员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.tenant, self.admin_member, self.admin_role = ensure_tenant_role_binding(
            self.admin_user,
            tenant_code="media_file_live_tenant",
            role_code="media_file_live_admin_role",
            role_name="实时媒体管理角色",
        )
        grant_role_permissions(
            self.admin_role,
            {
                "media_file.view_media_file": ScopeType.ALL,
                "media_file.manage_media_file": ScopeType.ALL,
            },
            group_name="media-file-live-admin-group",
        )

        self.other_admin_user = User.objects.create_user(username="media_file_live_other_admin", password="pass1234", status=1)
        ensure_staff_profile(
            self.other_admin_user,
            staff_no="MFL-OTHER-001",
            name="其他租户媒体管理员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.other_tenant, self.other_admin_member, self.other_admin_role = ensure_tenant_role_binding(
            self.other_admin_user,
            tenant_code="media_file_live_other_tenant",
            role_code="media_file_live_other_admin_role",
            role_name="其他租户实时媒体管理角色",
        )
        grant_role_permissions(
            self.other_admin_role,
            {
                "media_file.view_media_file": ScopeType.ALL,
                "media_file.manage_media_file": ScopeType.ALL,
            },
            group_name="media-file-live-other-admin-group",
        )

        self.pilot_user = User.objects.create_user(username="media_file_live_pilot", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="MFL-P-001",
            name="实时媒体飞手A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")

        self.other_pilot_user = User.objects.create_user(username="media_file_live_other_pilot", password="pass1234", status=1)
        self.other_pilot_staff = ensure_staff_profile(
            self.other_pilot_user,
            staff_no="MFL-P-002",
            name="实时媒体飞手B",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.other_pilot_member, _other_pilot_role = ensure_tenant_role_binding(
            self.other_pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.other_pilot_member, code="pilot_operator", name="飞手")

        self.other_tenant_pilot_user = User.objects.create_user(
            username="media_file_live_other_tenant_pilot",
            password="pass1234",
            status=1,
        )
        self.other_tenant_pilot_staff = ensure_staff_profile(
            self.other_tenant_pilot_user,
            staff_no="MFL-OTHER-P-001",
            name="其他租户媒体飞手",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.other_tenant_pilot_member, _other_tenant_pilot_role = ensure_tenant_role_binding(
            self.other_tenant_pilot_user,
            tenant=self.other_tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.other_tenant_pilot_member, code="pilot_operator", name="飞手")

        self.route = Route.objects.create(tenant=self.tenant, name="实时媒体航线", status=RouteStatus.ACTIVE)
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="MFL-DRN-001",
            name="实时媒体无人机",
            model="M300",
            serial_no="MFL-SN-001",
            status=DroneStatus.ENABLED,
        )
        self.mission = Mission.objects.create(
            tenant=self.tenant,
            name="实时媒体任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name=self.pilot_staff.name,
            status=MissionStatus.RUNNING,
        )
        self.other_mission = Mission.objects.create(
            tenant=self.tenant,
            name="实时媒体他人任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.other_pilot_member,
            pilot_name=self.other_pilot_staff.name,
            status=MissionStatus.RUNNING,
        )

        self.other_route = Route.objects.create(tenant=self.other_tenant, name="其他租户媒体航线", status=RouteStatus.ACTIVE)
        self.other_drone = Drone.objects.create(
            tenant=self.other_tenant,
            code="MFL-OTHER-DRN-001",
            name="其他租户媒体无人机",
            model="M350",
            serial_no="MFL-OTHER-SN-001",
            status=DroneStatus.ENABLED,
        )
        self.other_tenant_mission = Mission.objects.create(
            tenant=self.other_tenant,
            name="其他租户媒体任务",
            route=self.other_route,
            route_name=self.other_route.name,
            drone=self.other_drone,
            drone_name=self.other_drone.name,
            pilot=self.other_tenant_pilot_member,
            pilot_name=self.other_tenant_pilot_staff.name,
            status=MissionStatus.RUNNING,
        )

        self.login(username="media_file_live_admin", password="pass1234", tenant_code=self.tenant.code)

    def _next_flight_no(self) -> str:
        flight_no = f"MFL20260321{self._flight_no_seq:04d}"
        self._flight_no_seq += 1
        return flight_no

    def _create_record(
        self,
        *,
        tenant=None,
        mission=None,
        drone=None,
        pilot=None,
        pilot_name: str | None = None,
        flight_no: str | None = None,
        status: int = FlightRecordStatus.COMPLETED,
    ) -> FlightRecord:
        tenant = tenant or self.tenant
        mission = mission or (self.mission if tenant == self.tenant else self.other_tenant_mission)
        drone = drone or (self.drone if tenant == self.tenant else self.other_drone)
        pilot = pilot or (self.pilot_member if tenant == self.tenant else self.other_tenant_pilot_member)
        if pilot_name is None:
            if pilot.id == self.pilot_member.id:
                pilot_name = self.pilot_staff.name
            elif pilot.id == self.other_pilot_member.id:
                pilot_name = self.other_pilot_staff.name
            elif pilot.id == self.other_tenant_pilot_member.id:
                pilot_name = self.other_tenant_pilot_staff.name
            else:
                pilot_name = pilot.display_name or pilot.user.username
        start_time = timezone.now() - timedelta(minutes=20)
        end_time = timezone.now()
        return FlightRecord.objects.create(
            tenant=tenant,
            flight_no=flight_no or self._next_flight_no(),
            mission=mission,
            mission_name=mission.name if mission else "",
            route_name=mission.route_name if mission else "",
            airport_name="珠海金湾机场",
            drone=drone,
            drone_name=drone.name if drone else "",
            pilot=pilot,
            pilot_name=pilot_name,
            start_time=start_time,
            end_time=end_time,
            flight_duration=int((end_time - start_time).total_seconds()),
            photo_count=12,
            video_count=3,
            status=status,
        )

    def _create_media_file(
        self,
        *,
        flight_record: FlightRecord,
        tenant=None,
        media_type: int = MediaType.PHOTO,
        file_name: str = "IMG_0001.JPG",
        is_deleted: bool = False,
    ) -> MediaFile:
        tenant = tenant or self.tenant
        return MediaFile.objects.create(
            tenant=tenant,
            flight_record=flight_record,
            media_type=media_type,
            file_name=file_name,
            file_url=f"https://example.com/{file_name}",
            thumbnail_url=f"https://example.com/thumb/{file_name}",
            file_size=1024,
            latitude="22.31930000",
            longitude="113.58800000",
            captured_at=timezone.now() - timedelta(minutes=3),
            is_deleted=is_deleted,
            deleted_at=timezone.now() if is_deleted else None,
        )


class LiveMediaFileApiTests(LiveMediaFileApiTestCase):
    def test_media_file_lifecycle_should_follow_live_http_contract(self):
        flight_record = self._create_record(flight_no="MFL202603210900")
        next_flight_record = self._create_record(flight_no="MFL202603210901")

        create_response = self.client.post(
            "/api/v1/media-files",
            {
                "flight_record": flight_record.id,
                "media_type": MediaType.PHOTO,
                "file_name": "IMG_CREATE_OK.JPG",
                "file_url": "https://example.com/IMG_CREATE_OK.JPG",
                "thumbnail_url": "https://example.com/thumb/IMG_CREATE_OK.JPG",
                "file_size": 4096,
                "latitude": "22.31930000",
                "longitude": "113.58800000",
                "captured_at": "2026-03-21T08:16:00+08:00",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.json()["code"], "00000")
        create_data = create_response.json()["data"]
        self.assertEqual(create_data["flight_record"], flight_record.id)
        self.assertEqual(create_data["media_type"], MediaType.PHOTO)
        self.assertEqual(create_data["file_name"], "IMG_CREATE_OK.JPG")
        self.assertFalse(create_data["is_deleted"])
        media_file_id = create_data["id"]

        list_response = self.client.get(
            "/api/v1/media-files",
            {
                "flight_record_id": flight_record.id,
                "media_type": MediaType.PHOTO,
                "mission_id": self.mission.id,
                "drone_id": self.drone.id,
                "file_name": "CREATE_OK",
            },
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 1)
        self.assertEqual(list_response.json()["data"]["list"][0]["id"], media_file_id)

        retrieve_response = self.client.get(f"/api/v1/media-files/{media_file_id}")
        self.assertEqual(retrieve_response.status_code, 200)
        self.assertEqual(retrieve_response.json()["data"]["file_name"], "IMG_CREATE_OK.JPG")

        put_response = self.client.put(
            f"/api/v1/media-files/{media_file_id}",
            {
                "flight_record": next_flight_record.id,
                "media_type": MediaType.VIDEO,
                "file_name": "IMG_PUT_NEW.MP4",
                "file_url": "https://example.com/IMG_PUT_NEW.MP4",
                "thumbnail_url": "https://example.com/thumb/IMG_PUT_NEW.MP4",
                "file_size": 8192,
                "latitude": "23.12910000",
                "longitude": "113.26440000",
                "captured_at": "2026-03-21T08:20:00+08:00",
            },
            format="json",
        )
        self.assertEqual(put_response.status_code, 200)
        self.assertEqual(put_response.json()["code"], "00000")
        self.assertEqual(put_response.json()["data"]["flight_record"], next_flight_record.id)
        self.assertEqual(put_response.json()["data"]["media_type"], MediaType.VIDEO)
        self.assertEqual(put_response.json()["data"]["file_name"], "IMG_PUT_NEW.MP4")

        patch_response = self.client.patch(
            f"/api/v1/media-files/{media_file_id}",
            {
                "file_name": "IMG_PATCH_NEW.MP4",
                "file_url": "https://example.com/IMG_PATCH_NEW.MP4",
                "file_size": 16384,
            },
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["code"], "00000")
        self.assertEqual(patch_response.json()["data"]["file_name"], "IMG_PATCH_NEW.MP4")
        self.assertEqual(patch_response.json()["data"]["file_size"], 16384)

        delete_response = self.client.delete(f"/api/v1/media-files/{media_file_id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()["code"], "00000")
        self.assertEqual(delete_response.json()["data"]["id"], media_file_id)
        self.assertTrue(delete_response.json()["data"]["is_deleted"])

        deleted_retrieve_response = self.client.get(f"/api/v1/media-files/{media_file_id}")
        self.assertEqual(deleted_retrieve_response.status_code, 404)
        self.assertEqual(deleted_retrieve_response.json()["code"], "C0404")

        media_file = MediaFile.objects.get(id=media_file_id)
        self.assertTrue(media_file.is_deleted)
        self.assertIsNotNone(media_file.deleted_at)
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="MEDIA_FILE_CREATE",
                target_type="media_file",
                target_id=str(media_file_id),
            ).exists()
        )
        self.assertGreaterEqual(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="MEDIA_FILE_UPDATE",
                target_type="media_file",
                target_id=str(media_file_id),
            ).count(),
            2,
        )

    def test_list_should_exclude_deleted_records_and_support_filters_over_live_http(self):
        flight_record = self._create_record(flight_no="MFL202603211000")
        active_photo = self._create_media_file(
            flight_record=flight_record,
            media_type=MediaType.PHOTO,
            file_name="IMG_FILTER_A.JPG",
            is_deleted=False,
        )
        self._create_media_file(
            flight_record=flight_record,
            media_type=MediaType.VIDEO,
            file_name="VID_FILTER_B.MP4",
            is_deleted=False,
        )
        self._create_media_file(
            flight_record=flight_record,
            media_type=MediaType.PHOTO,
            file_name="IMG_DELETED.JPG",
            is_deleted=True,
        )

        media_type_response = self.client.get("/api/v1/media-files", {"media_type": MediaType.PHOTO})
        self.assertEqual(media_type_response.status_code, 200)
        self.assertEqual(media_type_response.json()["data"]["total"], 1)
        self.assertEqual(media_type_response.json()["data"]["list"][0]["id"], active_photo.id)

        file_name_response = self.client.get("/api/v1/media-files", {"file_name": "FILTER_A"})
        self.assertEqual(file_name_response.status_code, 200)
        self.assertEqual(file_name_response.json()["data"]["total"], 1)
        self.assertEqual(file_name_response.json()["data"]["list"][0]["file_name"], "IMG_FILTER_A.JPG")

    def test_invalid_write_contract_should_be_rejected_over_live_http(self):
        own_record = self._create_record(flight_no="MFL202603211100")
        foreign_record = self._create_record(
            tenant=self.other_tenant,
            mission=self.other_tenant_mission,
            drone=self.other_drone,
            pilot=self.other_tenant_pilot_member,
            pilot_name=self.other_tenant_pilot_staff.name,
            flight_no="MFL202603211101",
        )
        media_file = self._create_media_file(flight_record=own_record, file_name="IMG_PATCH_OLD.JPG")

        missing_name_response = self.client.post(
            "/api/v1/media-files",
            {
                "flight_record": own_record.id,
                "media_type": MediaType.PHOTO,
                "file_url": "https://example.com/IMG_MISSING_NAME.JPG",
            },
            format="json",
        )
        self.assertEqual(missing_name_response.status_code, 400)
        self.assertEqual(missing_name_response.json()["code"], "B0001")
        self.assertIn("file_name", missing_name_response.json()["data"])

        unknown_field_response = self.client.post(
            "/api/v1/media-files",
            {
                "flight_record": own_record.id,
                "media_type": MediaType.PHOTO,
                "file_name": "IMG_UNKNOWN.JPG",
                "file_url": "https://example.com/IMG_UNKNOWN.JPG",
                "is_deleted": True,
            },
            format="json",
        )
        self.assertEqual(unknown_field_response.status_code, 400)
        self.assertEqual(unknown_field_response.json()["code"], "B0001")
        self.assertIn("is_deleted", unknown_field_response.json()["data"])

        cross_tenant_create_response = self.client.post(
            "/api/v1/media-files",
            {
                "flight_record": foreign_record.id,
                "media_type": MediaType.PHOTO,
                "file_name": "IMG_OTHER_TENANT.JPG",
                "file_url": "https://example.com/IMG_OTHER_TENANT.JPG",
            },
            format="json",
        )
        self.assertEqual(cross_tenant_create_response.status_code, 400)
        self.assertEqual(cross_tenant_create_response.json()["code"], "B0001")
        self.assertIn("flight_record", cross_tenant_create_response.json()["data"])

        empty_patch_response = self.client.patch(
            f"/api/v1/media-files/{media_file.id}",
            {},
            format="json",
        )
        self.assertEqual(empty_patch_response.status_code, 400)
        self.assertEqual(empty_patch_response.json()["code"], "B0001")
        self.assertIn("body", empty_patch_response.json()["data"])

        cross_tenant_patch_response = self.client.patch(
            f"/api/v1/media-files/{media_file.id}",
            {"flight_record": foreign_record.id},
            format="json",
        )
        self.assertEqual(cross_tenant_patch_response.status_code, 400)
        self.assertEqual(cross_tenant_patch_response.json()["code"], "B0001")
        self.assertIn("flight_record", cross_tenant_patch_response.json()["data"])

    def test_cross_tenant_media_should_be_invisible_and_unmodifiable_over_live_http(self):
        foreign_record = self._create_record(
            tenant=self.other_tenant,
            mission=self.other_tenant_mission,
            drone=self.other_drone,
            pilot=self.other_tenant_pilot_member,
            pilot_name=self.other_tenant_pilot_staff.name,
            flight_no="MFL202603211200",
        )
        foreign_media = self._create_media_file(
            flight_record=foreign_record,
            tenant=self.other_tenant,
            media_type=MediaType.PHOTO,
            file_name="IMG_FOREIGN.JPG",
        )

        list_response = self.client.get("/api/v1/media-files")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 0)

        retrieve_response = self.client.get(f"/api/v1/media-files/{foreign_media.id}")
        self.assertEqual(retrieve_response.status_code, 404)
        self.assertEqual(retrieve_response.json()["code"], "C0404")

        patch_response = self.client.patch(
            f"/api/v1/media-files/{foreign_media.id}",
            {"file_name": "越权修改"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 404)
        self.assertEqual(patch_response.json()["code"], "C0404")

        delete_response = self.client.delete(f"/api/v1/media-files/{foreign_media.id}")
        self.assertEqual(delete_response.status_code, 404)
        self.assertEqual(delete_response.json()["code"], "C0404")
        self.assertFalse(MediaFile.objects.get(id=foreign_media.id).is_deleted)

    def test_business_media_file_api_should_require_tenant_context(self):
        flight_record = self._create_record(flight_no="MFL202603211300")
        media_file = self._create_media_file(flight_record=flight_record, file_name="IMG_TENANT_REQUIRED.JPG")
        tenantless_client = self.new_client()
        self.authenticate_client(
            tenantless_client,
            username="media_file_live_admin",
            password="pass1234",
        )

        list_response = tenantless_client.get("/api/v1/media-files")
        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(list_response.json()["code"], "A0403")

        detail_response = tenantless_client.get(f"/api/v1/media-files/{media_file.id}")
        self.assertEqual(detail_response.status_code, 403)
        self.assertEqual(detail_response.json()["code"], "A0403")

        create_response = tenantless_client.post(
            "/api/v1/media-files",
            {
                "flight_record": flight_record.id,
                "media_type": MediaType.PHOTO,
                "file_name": "IMG_NO_TENANT.JPG",
                "file_url": "https://example.com/IMG_NO_TENANT.JPG",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(create_response.json()["code"], "A0403")

    def test_platform_admin_should_be_blocked_from_business_media_file_api_even_with_permission(self):
        platform_role, _ = Role.objects.update_or_create(
            code="platform_admin",
            defaults={"name": "平台管理员", "status": DirectoryStatus.ACTIVE},
        )
        grant_role_permissions(
            platform_role,
            {
                "media_file.view_media_file": ScopeType.ALL,
                "media_file.manage_media_file": ScopeType.ALL,
            },
            group_name="media-file-live-platform-group",
        )
        platform_user = User.objects.create_user(
            username="media_file_live_platform_admin",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        ensure_staff_profile(platform_user, name="平台媒体管理员")
        platform_client = self.new_client()
        self.authenticate_client(
            platform_client,
            username="media_file_live_platform_admin",
            password="pass1234",
            tenant_code=self.tenant.code,
        )

        response = platform_client.get("/api/v1/media-files")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "A0403")


class LiveMediaFilePilotScopeTests(LiveMediaFileApiTestCase):
    def setUp(self):
        super().setUp()
        pilot_role = self.pilot_member.role_bindings.get(system_role__code="pilot_operator").system_role
        grant_role_permissions(
            pilot_role,
            {
                "media_file.view_media_file": ScopeType.ASSIGNED,
                "media_file.manage_media_file": ScopeType.ASSIGNED,
            },
            group_name="media-file-live-pilot-group",
        )
        self.my_record = self._create_record(
            mission=self.mission,
            pilot=self.pilot_member,
            pilot_name=self.pilot_staff.name,
            flight_no="MFL202603211400",
        )
        self.other_record = self._create_record(
            mission=self.other_mission,
            pilot=self.other_pilot_member,
            pilot_name=self.other_pilot_staff.name,
            flight_no="MFL202603211401",
        )
        self.my_media = self._create_media_file(flight_record=self.my_record, file_name="MY_SCOPE.JPG")
        self.other_media = self._create_media_file(flight_record=self.other_record, file_name="OTHER_SCOPE.JPG")
        self.pilot_client = self.new_client()
        self.authenticate_client(
            self.pilot_client,
            username="media_file_live_pilot",
            password="pass1234",
            tenant_code=self.tenant.code,
        )

    def test_assigned_scope_pilot_should_only_list_and_retrieve_own_media_files(self):
        list_response = self.pilot_client.get("/api/v1/media-files")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 1)
        self.assertEqual(list_response.json()["data"]["list"][0]["id"], self.my_media.id)

        own_detail_response = self.pilot_client.get(f"/api/v1/media-files/{self.my_media.id}")
        self.assertEqual(own_detail_response.status_code, 200)
        self.assertEqual(own_detail_response.json()["data"]["id"], self.my_media.id)

        other_detail_response = self.pilot_client.get(f"/api/v1/media-files/{self.other_media.id}")
        self.assertEqual(other_detail_response.status_code, 404)
        self.assertEqual(other_detail_response.json()["code"], "C0404")

    def test_assigned_scope_pilot_should_only_create_media_for_own_flight_record(self):
        response = self.pilot_client.post(
            "/api/v1/media-files",
            {
                "flight_record": self.other_record.id,
                "media_type": MediaType.PHOTO,
                "file_name": "CROSS_SCOPE.JPG",
                "file_url": "https://example.com/CROSS_SCOPE.JPG",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "B0001")
        self.assertIn("flight_record", response.json()["data"])

    def test_assigned_scope_pilot_should_not_mutate_other_media_files(self):
        patch_response = self.pilot_client.patch(
            f"/api/v1/media-files/{self.other_media.id}",
            {"file_name": "越权修改文件名.JPG"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 404)
        self.assertEqual(patch_response.json()["code"], "C0404")

        delete_response = self.pilot_client.delete(f"/api/v1/media-files/{self.other_media.id}")
        self.assertEqual(delete_response.status_code, 404)
        self.assertEqual(delete_response.json()["code"], "C0404")
        self.assertFalse(MediaFile.objects.get(id=self.other_media.id).is_deleted)
