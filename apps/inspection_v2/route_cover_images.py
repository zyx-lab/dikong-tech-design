import base64
import binascii
from pathlib import Path
from typing import Final

from django.core.files.uploadedfile import SimpleUploadedFile
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

ROUTE_COVER_ALLOWED_EXTENSIONS: Final = {".jpg", ".jpeg", ".png", ".webp"}
ROUTE_COVER_ALLOWED_CONTENT_TYPES: Final = {"image/jpeg", "image/png", "image/webp"}
ROUTE_COVER_MAX_BYTES: Final = 5 * 1024 * 1024
ROUTE_COVER_VALIDATION_MESSAGE: Final = "只支持上传 jpg/jpeg/png/webp 图片，且大小不能超过 5MB"
ROUTE_COVER_BASE64_SCHEMA: Final = {
    "type": "string",
    "nullable": True,
    "description": "Base64 图片字符串，支持 data:image/jpeg;base64,...、data:image/png;base64,...、data:image/webp;base64,... 或原始标准 Base64。",
    "example": "data:image/png;base64,iVBORw0KGgo=",
}

_ASCII_WHITESPACE: Final = {ord(char): None for char in " \t\n\r\f\v"}
_DATA_URL_PREFIXES: Final = {
    "data:image/jpeg;base64,": "image/jpeg",
    "data:image/png;base64,": "image/png",
    "data:image/webp;base64,": "image/webp",
}
_MIME_EXTENSIONS: Final = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _validation_error() -> serializers.ValidationError:
    return serializers.ValidationError(ROUTE_COVER_VALIDATION_MESSAGE)


def _split_base64_payload(value: str) -> tuple[str, str | None]:
    for prefix, mime_type in _DATA_URL_PREFIXES.items():
        if value.startswith(prefix):
            return value[len(prefix) :], mime_type
    return value, None


def _estimated_decoded_size(payload: str) -> int:
    if not payload:
        return 0
    if len(payload) % 4 != 0:
        return ((len(payload) + 3) // 4) * 3
    padding = len(payload) - len(payload.rstrip("="))
    return (len(payload) // 4) * 3 - padding


def _decode_base64_payload(payload: str) -> bytes:
    normalized_payload = payload.translate(_ASCII_WHITESPACE)
    if _estimated_decoded_size(normalized_payload) > ROUTE_COVER_MAX_BYTES:
        raise _validation_error()
    try:
        return base64.b64decode(normalized_payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _validation_error() from exc


def _detect_mime_type(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def parse_route_cover_base64(value: str) -> SimpleUploadedFile:
    payload, declared_mime_type = _split_base64_payload(value)
    content = _decode_base64_payload(payload)
    if len(content) == 0 or len(content) > ROUTE_COVER_MAX_BYTES:
        raise _validation_error()

    detected_mime_type = _detect_mime_type(content)
    if detected_mime_type is None:
        raise _validation_error()
    if declared_mime_type is not None and declared_mime_type != detected_mime_type:
        raise _validation_error()

    extension = _MIME_EXTENSIONS[detected_mime_type]
    return SimpleUploadedFile(
        name=f"cover{extension}",
        content=content,
        content_type=detected_mime_type,
    )


def validate_route_cover_upload(value):
    extension = Path(str(getattr(value, "name", "") or "")).suffix.lower()
    content_type = str(getattr(value, "content_type", "") or "").lower()
    size = int(getattr(value, "size", 0) or 0)
    if (
        extension not in ROUTE_COVER_ALLOWED_EXTENSIONS
        or (content_type and content_type not in ROUTE_COVER_ALLOWED_CONTENT_TYPES)
        or size <= 0
        or size > ROUTE_COVER_MAX_BYTES
    ):
        raise serializers.ValidationError(ROUTE_COVER_VALIDATION_MESSAGE)
    return value


@extend_schema_field(ROUTE_COVER_BASE64_SCHEMA)
class RouteCoverImageField(serializers.FileField):
    def to_internal_value(self, data):
        if isinstance(data, str):
            return parse_route_cover_base64(data)
        return super().to_internal_value(data)
