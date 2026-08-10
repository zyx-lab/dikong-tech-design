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
