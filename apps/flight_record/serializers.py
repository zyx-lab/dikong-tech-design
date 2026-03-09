from rest_framework import serializers

from apps.flight_record.models import FlightRecord


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

        start_time = attrs.get("start_time")
        end_time = attrs.get("end_time")
        mission = attrs.get("mission")
        drone = attrs.get("drone")
        pilot = attrs.get("pilot")

        if start_time and end_time and end_time < start_time:
            raise serializers.ValidationError({"end_time": "结束时间不能早于开始时间"})

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
            validated_data.setdefault("pilot_name", pilot.name)

        if start_time and end_time and validated_data.get("flight_duration") is None:
            duration_seconds = int((end_time - start_time).total_seconds())
            validated_data["flight_duration"] = max(duration_seconds, 0)

        return super().create(validated_data)

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
