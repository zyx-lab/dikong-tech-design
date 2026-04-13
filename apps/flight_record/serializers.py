from rest_framework import serializers

from apps.access.models import ScopeType
from apps.access.validation import (
    TenantMemberValidationMessages,
    validate_relation_belongs_to_tenant,
    validate_tenant_member_as_pilot,
)
from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.api_v1.tenant_scope import require_request_tenant
from apps.flight_record.models import FlightRecord


def _pilot_display_name(pilot_member) -> str:
    staff = getattr(pilot_member.user, "staff_profile", None)
    if staff is not None and staff.name:
        return staff.name
    if pilot_member.display_name:
        return pilot_member.display_name
    return pilot_member.user.username


class FlightRecordReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = FlightRecord
        fields = [
            "id",
            "flight_no",
            "mission",
            "mission_name",
            "route_name",
            "airport_name",
            "drone",
            "drone_name",
            "pilot",
            "pilot_name",
            "start_time",
            "end_time",
            "flight_duration",
            "photo_count",
            "video_count",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class FlightRecordWriteSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    def _validate_assigned_scope_target(self, *, mission, pilot):
        request = self.context.get("request")
        decision = getattr(request, "_authz_decision", None) if request is not None else None
        if decision is None or decision.scope != ScopeType.ASSIGNED:
            return

        tenant_member_id = decision.tenant_member_id
        effective_pilot_id = pilot.id if pilot is not None else None
        if effective_pilot_id is None and mission is not None:
            effective_pilot_id = mission.pilot_id

        if effective_pilot_id != tenant_member_id:
            raise serializers.ValidationError({"pilot": "ASSIGNED 范围下只能操作当前飞手自己的飞行记录"})

    def validate(self, attrs):
        attrs = super().validate(attrs)

        current_tenant = require_request_tenant(self.context)
        instance = getattr(self, "instance", None)
        if instance is not None and "status" in self.initial_data:
            raise serializers.ValidationError({"status": "status 不可通过更新接口直接修改，请使用状态动作接口"})

        flight_no = attrs.get("flight_no", instance.flight_no if instance is not None else None)
        start_time = attrs.get("start_time", instance.start_time if instance is not None else None)
        end_time = attrs.get("end_time", instance.end_time if instance is not None else None)
        mission = attrs.get("mission", instance.mission if instance is not None else None)
        drone = attrs.get("drone", instance.drone if instance is not None else None)
        pilot = attrs.get("pilot", instance.pilot if instance is not None else None)

        if flight_no and FlightRecord.objects.filter(
            tenant=current_tenant,
            flight_no=flight_no,
        ).exclude(pk=getattr(instance, "pk", None)).exists():
            raise serializers.ValidationError({"flight_no": "当前租户下已存在相同架次编号"})

        if start_time and end_time and end_time < start_time:
            raise serializers.ValidationError({"end_time": "结束时间不能早于开始时间"})

        validate_relation_belongs_to_tenant(
            related_obj=mission,
            tenant_id=current_tenant.id,
            field_name="mission",
            mismatch_message="仅允许绑定当前租户下的任务",
            error_cls=serializers.ValidationError,
        )
        validate_relation_belongs_to_tenant(
            related_obj=drone,
            tenant_id=current_tenant.id,
            field_name="drone",
            mismatch_message="仅允许绑定当前租户下的无人机",
            error_cls=serializers.ValidationError,
        )
        validate_tenant_member_as_pilot(
            tenant_member=pilot,
            tenant_id=current_tenant.id,
            field_name="pilot",
            messages=TenantMemberValidationMessages(
                tenant_mismatch="仅允许绑定当前租户下的成员",
                inactive_member="仅允许绑定 ACTIVE 成员",
                missing_staff_profile="pilot 对应账号必须存在 staff_profile",
                inactive_employment="仅允许绑定在职飞手",
                missing_role="仅允许绑定当前租户下的飞手类型（pilot_operator）",
            ),
            error_cls=serializers.ValidationError,
        )

        if mission and drone and mission.drone_id and mission.drone_id != drone.id:
            raise serializers.ValidationError({"drone": "drone 与 mission 绑定关系不一致"})
        if mission and pilot and mission.pilot_id and mission.pilot_id != pilot.id:
            raise serializers.ValidationError({"pilot": "pilot 与 mission 绑定关系不一致"})
        self._validate_assigned_scope_target(mission=mission, pilot=pilot)

        return attrs

    def create(self, validated_data):
        mission = validated_data.get("mission")
        drone = validated_data.get("drone")
        pilot = validated_data.get("pilot")
        start_time = validated_data.get("start_time")
        end_time = validated_data.get("end_time")

        if mission:
            validated_data.setdefault("mission_name", mission.name)
            if mission.route_name:
                validated_data.setdefault("route_name", mission.route_name)
            elif mission.route_id and getattr(mission, "route", None):
                validated_data.setdefault("route_name", mission.route.name)
            if mission.drone_name:
                validated_data.setdefault("drone_name", mission.drone_name)
            if mission.pilot_name:
                validated_data.setdefault("pilot_name", mission.pilot_name)

        if drone:
            validated_data.setdefault("drone_name", drone.name)
        if pilot:
            validated_data.setdefault("pilot_name", _pilot_display_name(pilot))

        if start_time and end_time and validated_data.get("flight_duration") is None:
            duration_seconds = int((end_time - start_time).total_seconds())
            validated_data["flight_duration"] = max(duration_seconds, 0)

        return super().create(validated_data)

    def update(self, instance, validated_data):
        mission = validated_data.get("mission", instance.mission)
        drone = validated_data.get("drone", instance.drone)
        pilot = validated_data.get("pilot", instance.pilot)
        start_time = validated_data.get("start_time", instance.start_time)
        end_time = validated_data.get("end_time", instance.end_time)

        validated_data["mission_name"] = mission.name if mission else ""
        if mission:
            if mission.route_name:
                validated_data["route_name"] = mission.route_name
            elif mission.route_id and getattr(mission, "route", None):
                validated_data["route_name"] = mission.route.name
            else:
                validated_data["route_name"] = ""
        else:
            validated_data["route_name"] = ""

        validated_data["drone_name"] = drone.name if drone else ""
        validated_data["pilot_name"] = _pilot_display_name(pilot) if pilot else ""

        if start_time and end_time and "flight_duration" not in validated_data:
            duration_seconds = int((end_time - start_time).total_seconds())
            validated_data["flight_duration"] = max(duration_seconds, 0)

        return super().update(instance, validated_data)

    class Meta:
        model = FlightRecord
        fields = [
            "flight_no",
            "mission",
            "drone",
            "pilot",
            "start_time",
            "end_time",
            "flight_duration",
            "photo_count",
            "video_count",
            "status",
            "airport_name",
        ]
        extra_kwargs = {
            "flight_no": {"help_text": "架次编号，租户内唯一。重复提交时会返回 C0101。"},
            "mission": {"help_text": "关联任务 ID，可为空；必须属于当前租户。"},
            "drone": {"help_text": "执行无人机 ID，可为空；若 mission 已绑定 drone，则必须保持一致。"},
            "pilot": {"help_text": "执行飞手成员 ID，可为空；必须为当前租户 ACTIVE 飞手。"},
            "start_time": {"help_text": "飞行开始时间，可为空。"},
            "end_time": {"help_text": "飞行结束时间，可为空；若填写不得早于 start_time。"},
            "flight_duration": {"help_text": "飞行时长，单位秒；若同时提供开始和结束时间，可由系统自动回填。"},
            "photo_count": {"help_text": "本次飞行拍摄照片数量，默认 0。"},
            "video_count": {"help_text": "本次飞行录制视频数量，默认 0。"},
            "status": {"help_text": "飞行记录状态；创建时可显式指定，更新接口不允许直接修改，请使用状态动作接口。"},
            "airport_name": {"help_text": "执行机场名称，可为空。"},
        }
