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
    clientId = serializers.CharField(max_length=128, required=False, allow_null=True, allow_blank=False)
    expireSec = StrictIntegerField(min_value=1800, max_value=86400, required=False, default=3600)
    osdFrequency = StrictIntegerField(min_value=1, max_value=30, required=False, default=10)
    hsiFrequency = StrictIntegerField(min_value=1, max_value=30, required=False, default=5)


class DrcExitSerializer(StrictSerializer):
    dockId = StrictIntegerField(min_value=1)
    clientId = serializers.CharField(min_length=1, max_length=128)


class DrcMqttSerializer(serializers.Serializer):
    address = serializers.CharField()
    username = serializers.CharField()
    password = serializers.CharField()
    clientId = serializers.CharField()
    expireTime = serializers.IntegerField()
    enableTls = serializers.BooleanField()


class DrcConnectResponseSerializer(serializers.Serializer):
    dockId = serializers.IntegerField()
    droneId = serializers.IntegerField()
    mqtt = DrcMqttSerializer()
    publishTopic = serializers.CharField()
    subscribeTopic = serializers.CharField()


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
