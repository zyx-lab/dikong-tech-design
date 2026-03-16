from rest_framework import serializers

from apps.access.models import DirectoryStatus, EmploymentStatus, TenantMemberRoleStatus, TenantMemberStatus
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


class FlightRecordWriteSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        unknown_fields = sorted(set(self.initial_data.keys()) - set(self.fields.keys()))
        if unknown_fields:
            raise serializers.ValidationError({field: "该字段在此接口不可写" for field in unknown_fields})

        current_tenant = require_request_tenant(self.context)
        instance = getattr(self, "instance", None)
        if instance is not None and "status" in self.initial_data:
            raise serializers.ValidationError({"status": "status 不可通过 PATCH 直接修改，请使用状态动作接口"})

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

        if mission is not None and mission.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"mission": "仅允许绑定当前租户下的任务"})
        if drone is not None and drone.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"drone": "仅允许绑定当前租户下的无人机"})
        if pilot is not None and pilot.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"pilot": "仅允许绑定当前租户下的成员"})
        if pilot is not None and pilot.status != TenantMemberStatus.ACTIVE:
            raise serializers.ValidationError({"pilot": "仅允许绑定 ACTIVE 成员"})
        if pilot is not None:
            staff = getattr(pilot.user, "staff_profile", None)
            if staff is None:
                raise serializers.ValidationError({"pilot": "pilot 对应账号必须存在 staff_profile"})
            if staff.employment_status != EmploymentStatus.ACTIVE:
                raise serializers.ValidationError({"pilot": "仅允许绑定在职飞手"})
            if not pilot.role_bindings.filter(
                system_role__code="pilot_operator",
                system_role__status=DirectoryStatus.ACTIVE,
                status=TenantMemberRoleStatus.GRANTED,
            ).exists():
                raise serializers.ValidationError({"pilot": "仅允许绑定当前租户下的飞手类型（pilot_operator）"})

        if mission and drone and mission.drone_id and mission.drone_id != drone.id:
            raise serializers.ValidationError({"drone": "drone 与 mission 绑定关系不一致"})
        if mission and pilot and mission.pilot_id and mission.pilot_id != pilot.id:
            raise serializers.ValidationError({"pilot": "pilot 与 mission 绑定关系不一致"})

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
