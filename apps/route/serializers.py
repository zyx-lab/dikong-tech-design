import xml.etree.ElementTree as ET

from rest_framework import serializers

from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.route.models import Route


class RouteReadSerializer(serializers.ModelSerializer):
    is_published = serializers.SerializerMethodField()

    class Meta:
        model = Route
        fields = [
            "id",
            "name",
            "is_published",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_is_published(self, obj) -> bool:
        return bool(getattr(getattr(obj, "dji_index", None), "is_published", False))


class RouteWriteSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    xml_file = serializers.FileField(required=True, allow_empty_file=False)

    class Meta:
        model = Route
        fields = ["name", "xml_file"]
        extra_kwargs = {
            "name": {"help_text": "航线名称。"},
            "xml_file": {"help_text": "航线 XML 文件。"},
        }

    def validate_xml_file(self, value):
        try:
            value.seek(0)
            ET.fromstring(value.read())
        except (ET.ParseError, TypeError, ValueError):
            raise serializers.ValidationError("上传文件必须是可解析 XML")
        finally:
            value.seek(0)
        return value


class RouteCreateSerializer(RouteWriteSerializer):
    pass


class RouteUpdateSerializer(RouteWriteSerializer):
    pass
