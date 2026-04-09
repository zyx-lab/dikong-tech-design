from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework import serializers

from apps.access.models import EmploymentStatus, TenantMemberStatus
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding
from apps.access.validation import (
    TenantMemberValidationMessages,
    validate_relation_belongs_to_tenant,
    validate_tenant_member_as_pilot,
)
from apps.drone.models import Drone

User = get_user_model()


class AccessValidationHelperTests(TestCase):
    def setUp(self):
        self.owner_user = User.objects.create_user(username="access_validation_owner", password="pass1234", status=1)
        ensure_staff_profile(self.owner_user, name="校验所有者", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, _role = ensure_tenant_role_binding(
            self.owner_user,
            tenant_code="access_validation_tenant",
            role_code="access_validation_role",
            role_name="校验角色",
        )

    def test_validate_relation_belongs_to_tenant_should_raise_serializer_validation_error(self):
        other_user = User.objects.create_user(username="access_validation_other", password="pass1234", status=1)
        ensure_staff_profile(other_user, name="其他租户用户", employment_status=EmploymentStatus.ACTIVE)
        other_tenant, _other_member, _other_role = ensure_tenant_role_binding(
            other_user,
            tenant_code="access_validation_other_tenant",
            role_code="access_validation_other_role",
            role_name="其他租户角色",
        )
        other_drone = Drone.objects.create(
            tenant=other_tenant,
            code="ACCESS-HELPER-DRONE-001",
            name="跨租户无人机",
            model="M30",
            device_sn="ACCESS-HELPER-SN-001",
        )

        with self.assertRaises(serializers.ValidationError) as exc_info:
            validate_relation_belongs_to_tenant(
                related_obj=other_drone,
                tenant_id=self.tenant.id,
                field_name="drone",
                mismatch_message="仅允许绑定当前租户下的无人机",
                error_cls=serializers.ValidationError,
            )

        self.assertEqual(str(exc_info.exception.detail["drone"]), "仅允许绑定当前租户下的无人机")

    def test_validate_tenant_member_as_pilot_should_raise_django_validation_error(self):
        pilot_user = User.objects.create_user(username="access_validation_pilot", password="pass1234", status=1)
        ensure_staff_profile(pilot_user, name="非飞手成员", employment_status=EmploymentStatus.ACTIVE)
        _tenant, tenant_member, _role = ensure_tenant_role_binding(
            pilot_user,
            tenant=self.tenant,
            role_code="access_validation_member_role",
            role_name="普通成员角色",
        )
        tenant_member.status = TenantMemberStatus.ACTIVE
        tenant_member.save(update_fields=["status", "updated_at"])

        with self.assertRaises(ValidationError) as exc_info:
            validate_tenant_member_as_pilot(
                tenant_member=tenant_member,
                tenant_id=self.tenant.id,
                field_name="pilot",
                messages=TenantMemberValidationMessages(
                    tenant_mismatch="pilot 必须属于当前 tenant",
                    inactive_member="仅允许绑定 ACTIVE 成员",
                    missing_staff_profile="pilot 对应账号必须存在 staff_profile",
                    inactive_employment="仅允许绑定在职飞手",
                    missing_role="仅允许绑定当前租户下的飞手类型（pilot_operator）",
                ),
                error_cls=ValidationError,
            )

        self.assertEqual(exc_info.exception.message_dict, {"pilot": ["仅允许绑定当前租户下的飞手类型（pilot_operator）"]})
