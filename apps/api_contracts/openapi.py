from drf_spectacular.utils import OpenApiResponse, inline_serializer
from rest_framework import serializers


STANDARD_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": "业务码。成功固定为 00000，失败按 A/B/C/E 码段区分。",
            "example": "00000",
        },
        "msg": {
            "type": "string",
            "description": "响应消息。成功通常为 success，失败为可直接展示的错误提示。",
            "example": "success",
        },
        "data": {"nullable": True},
    },
    "required": ["code", "msg", "data"],
}

GENERIC_OBJECT_SCHEMA = {
    "type": "object",
    "additionalProperties": {},
}


def list_data_serializer(name, item_serializer_class):
    return inline_serializer(
        name=name,
        fields={
            "list": item_serializer_class(many=True),
            "total": serializers.IntegerField(),
        },
    )


def standard_empty_response(description="操作成功。"):
    return OpenApiResponse(response=STANDARD_RESPONSE_SCHEMA, description=description)


def generic_object_response(description="操作成功。返回 DJI 上游透传数据。"):
    return OpenApiResponse(response=GENERIC_OBJECT_SCHEMA, description=description)


class V2SessionUserContextSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()


class V2MeContextSerializer(serializers.Serializer):
    user = V2SessionUserContextSerializer()
    department = serializers.DictField()
    roles = serializers.ListField(child=serializers.CharField())
    permissions = serializers.ListField(child=serializers.CharField())
    dataScopes = serializers.ListField(child=serializers.CharField())


class V2DiscoveredDeviceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    resourceId = serializers.IntegerField()
    resourceType = serializers.CharField()
    djiConnectionId = serializers.IntegerField()
    deviceSn = serializers.CharField()
    name = serializers.CharField()
    model = serializers.CharField()
    onlineStatus = serializers.BooleanField()


class V2DiscoveredPayloadSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    resourceId = serializers.IntegerField()
    resourceType = serializers.CharField()
    djiConnectionId = serializers.IntegerField()
    payloadSn = serializers.CharField()
    name = serializers.CharField()
    model = serializers.CharField()
    payloadType = serializers.CharField()
    onlineStatus = serializers.BooleanField()


class V2DjiConnectionDiscoverSerializer(serializers.Serializer):
    connectionId = serializers.IntegerField()
    drones = V2DiscoveredDeviceSerializer(many=True)
    docks = V2DiscoveredDeviceSerializer(many=True)
    gateways = V2DiscoveredDeviceSerializer(many=True)
    payloads = V2DiscoveredPayloadSerializer(many=True)


class V2ResourceTypeSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    online = serializers.IntegerField()
    available = serializers.IntegerField()
    occupied = serializers.IntegerField()


class V2PilotSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    active = serializers.IntegerField()
    disabled = serializers.IntegerField()


class V2DepartmentResourceSummarySerializer(serializers.Serializer):
    departmentId = serializers.IntegerField()
    departmentName = serializers.CharField()
    departmentPath = serializers.CharField()
    drones = serializers.IntegerField()
    docks = serializers.IntegerField()
    gateways = serializers.IntegerField()
    payloads = serializers.IntegerField()
    pilots = serializers.IntegerField()


class V2ResourceSummarySerializer(serializers.Serializer):
    drones = V2ResourceTypeSummarySerializer()
    docks = V2ResourceTypeSummarySerializer()
    gateways = V2ResourceTypeSummarySerializer()
    payloads = V2ResourceTypeSummarySerializer()
    pilots = V2PilotSummarySerializer()
    departments = V2DepartmentResourceSummarySerializer(many=True)


class V2MediaRefreshSerializer(serializers.Serializer):
    synced = serializers.IntegerField()
    photoCount = serializers.IntegerField()
    videoCount = serializers.IntegerField()
