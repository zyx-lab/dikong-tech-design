from rest_framework import serializers

from apps.access.api_base import StrictSerializer


class StrictIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if isinstance(data, bool):
            self.fail("invalid")
        return super().to_internal_value(data)


class DrcCapabilityQuerySerializer(StrictSerializer):
    dockId = StrictIntegerField(min_value=1)


class DrcConnectSerializer(StrictSerializer):
    dockId = StrictIntegerField(min_value=1)
    expireSec = StrictIntegerField(min_value=1800, max_value=86400, required=False, default=3600)
    osdFrequency = StrictIntegerField(min_value=1, max_value=30, required=False, default=10)
    hsiFrequency = StrictIntegerField(min_value=1, max_value=30, required=False, default=5)


class DrcExitSerializer(StrictSerializer):
    sessionId = serializers.UUIDField()


DOCK_DEBUG_ACTIONS = (
    "debug_mode_open",
    "cover_open",
    "cover_close",
    "debug_mode_close",
)


class DrcDockDebugActionSerializer(StrictSerializer):
    dockId = StrictIntegerField(min_value=1)
    action = serializers.ChoiceField(choices=DOCK_DEBUG_ACTIONS)


class DrcPointSerializer(StrictSerializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    height = serializers.FloatField(min_value=2, max_value=10000)


DRC_FLIGHT_ACTION_FIELDS = {
    "takeoff_to_point": {
        "targetLatitude": "target_latitude",
        "targetLongitude": "target_longitude",
        "targetHeight": "target_height",
        "securityTakeoffHeight": "security_takeoff_height",
        "rthMode": "rth_mode",
        "rthAltitude": "rth_altitude",
        "rcLostAction": "rc_lost_action",
        "commanderModeLostAction": "commander_mode_lost_action",
        "commanderFlightMode": "commander_flight_mode",
        "commanderFlightHeight": "commander_flight_height",
        "maxSpeed": "max_speed",
    },
    "fly_to_point": {"maxSpeed": "max_speed", "points": "points"},
    "fly_to_point_update": {"maxSpeed": "max_speed", "points": "points"},
    "fly_to_point_stop": {},
}
DRC_FLIGHT_ACTION_OPTIONAL_FIELDS = {
    "takeoff_to_point": {"flightSafetyAdvanceCheck": "flight_safety_advance_check"},
}


class DrcFlightActionSerializer(StrictSerializer):
    dockId = StrictIntegerField(min_value=1)
    action = serializers.ChoiceField(choices=list(DRC_FLIGHT_ACTION_FIELDS))
    targetLatitude = serializers.FloatField(required=False, min_value=-90, max_value=90)
    targetLongitude = serializers.FloatField(required=False, min_value=-180, max_value=180)
    targetHeight = serializers.FloatField(required=False, min_value=2, max_value=1500)
    securityTakeoffHeight = serializers.FloatField(required=False, min_value=20, max_value=1500)
    rthMode = serializers.ChoiceField(choices=[1], required=False)
    rthAltitude = serializers.FloatField(required=False, min_value=2, max_value=1500)
    rcLostAction = serializers.ChoiceField(choices=[0, 1, 2], required=False)
    commanderModeLostAction = serializers.ChoiceField(choices=[0, 1], required=False)
    commanderFlightMode = serializers.ChoiceField(choices=[0, 1], required=False)
    commanderFlightHeight = serializers.FloatField(required=False, min_value=2, max_value=3000)
    flightSafetyAdvanceCheck = serializers.BooleanField(required=False)
    maxSpeed = StrictIntegerField(required=False, min_value=1, max_value=15)
    points = DrcPointSerializer(many=True, required=False, min_length=1, max_length=1)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        action = attrs["action"]
        action_fields = DRC_FLIGHT_ACTION_FIELDS[action]
        missing = [field for field in action_fields if field not in attrs]
        if missing:
            raise serializers.ValidationError({field: [f"{action} 需要该字段"] for field in missing})
        action_optional_fields = DRC_FLIGHT_ACTION_OPTIONAL_FIELDS.get(action, {})
        input_fields = {
            field for fields in (*DRC_FLIGHT_ACTION_FIELDS.values(), *DRC_FLIGHT_ACTION_OPTIONAL_FIELDS.values())
            for field in fields
        }
        unexpected = sorted(input_fields.intersection(attrs) - set(action_fields) - set(action_optional_fields))
        if unexpected:
            raise serializers.ValidationError({field: [f"{action} 不接受该字段"] for field in unexpected})
        attrs["_dji_data"] = {dji_name: attrs[field] for field, dji_name in action_fields.items()}
        attrs["_dji_data"].update({
            dji_name: attrs[field]
            for field, dji_name in action_optional_fields.items()
            if field in attrs
        })
        if action == "takeoff_to_point":
            attrs["_dji_data"]["exit_wayline_when_rc_lost"] = 0
        return attrs


class DrcConnectResponseSerializer(serializers.Serializer):
    sessionId = serializers.UUIDField()
    dockId = serializers.IntegerField()
    droneId = serializers.IntegerField()
    expiresAt = serializers.DateTimeField()
    webSocketPath = serializers.CharField()


class DrcCapabilityResponseSerializer(serializers.Serializer):
    dockId = serializers.IntegerField()
    droneId = serializers.IntegerField(allow_null=True)
    supported = serializers.BooleanField()
    available = serializers.BooleanField()
    blockers = serializers.ListField(child=serializers.DictField())
    control = serializers.DictField()
    payloads = serializers.ListField(child=serializers.DictField())


class DrcExitResponseSerializer(serializers.Serializer):
    status = serializers.CharField()


class DrcFlightActionResponseSerializer(serializers.Serializer):
    action = serializers.CharField()
    status = serializers.CharField()
    dockId = serializers.IntegerField()
    droneId = serializers.IntegerField()
    upstream = serializers.JSONField()


class DrcDockDebugActionResponseSerializer(serializers.Serializer):
    action = serializers.CharField()
    status = serializers.CharField()
    dockId = serializers.IntegerField()
    upstream = serializers.JSONField()
