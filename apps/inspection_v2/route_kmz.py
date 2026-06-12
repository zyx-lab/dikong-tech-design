from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import zipfile
from xml.etree import ElementTree

from rest_framework import serializers

from apps.inspection_v2.models import WaylineType


TEMPLATE_PATH = "wpmz/template.kml"
WAYLINES_PATH = "wpmz/waylines.wpml"

TEMPLATE_TYPE_TO_WAYLINE_TYPE = {
    "waypoint": WaylineType.WAYPOINT,
    "mapping2d": WaylineType.MAPPING_2D,
    "mapping3d": WaylineType.MAPPING_3D,
    "mappingstrip": WaylineType.MAPPING_STRIP,
}


@dataclass(frozen=True)
class ParsedRouteKmz:
    wayline_type: WaylineType
    default_altitude: Decimal | None
    default_speed: Decimal | None
    waypoints: list[dict]


def parse_route_kmz(file_obj) -> ParsedRouteKmz:
    try:
        file_obj.seek(0)
        with zipfile.ZipFile(file_obj) as archive:
            template_xml = _read_required_file(archive, TEMPLATE_PATH)
            waylines_xml = _read_required_file(archive, WAYLINES_PATH)
    except zipfile.BadZipFile as exc:
        raise serializers.ValidationError({"kmzFile": ["请上传有效的 KMZ/ZIP 文件"]}) from exc
    finally:
        _seek_start(file_obj)

    try:
        template_root = ElementTree.fromstring(template_xml)
        waylines_root = ElementTree.fromstring(waylines_xml)
    except ElementTree.ParseError as exc:
        raise serializers.ValidationError({"kmzFile": ["KMZ 内 WPML XML 格式不正确"]}) from exc

    template_type = _first_text(template_root, "templateType")
    wayline_type = TEMPLATE_TYPE_TO_WAYLINE_TYPE.get(str(template_type or "").strip().lower())
    if wayline_type is None:
        raise serializers.ValidationError({"kmzFile": ["KMZ templateType 不支持或缺失"]})

    waypoints = _parse_waypoints(waylines_root)
    if not waypoints:
        raise serializers.ValidationError({"kmzFile": ["KMZ 未解析到可用航点"]})

    return ParsedRouteKmz(
        wayline_type=wayline_type,
        default_altitude=_common_decimal([item["altitude"] for item in waypoints]),
        default_speed=_default_speed(waylines_root, waypoints),
        waypoints=waypoints,
    )


def _read_required_file(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except KeyError as exc:
        raise serializers.ValidationError({"kmzFile": [f"KMZ 缺少 {name}"]}) from exc


def _parse_waypoints(root: ElementTree.Element) -> list[dict]:
    parsed = []
    seen_sequences = set()
    for folder in _iter_by_local_name(root, "Folder"):
        folder_speed = _decimal_or_none(_first_text(folder, "autoFlightSpeed"))
        for placemark in _iter_by_local_name(folder, "Placemark"):
            coordinates = _point_coordinates(placemark)
            if coordinates is None:
                continue
            longitude, latitude, altitude_from_coordinates = coordinates
            altitude = _decimal_or_none(_first_text(placemark, "executeHeight")) or altitude_from_coordinates
            if altitude is None:
                raise serializers.ValidationError({"kmzFile": ["KMZ 航点缺少高度"]})
            sequence = _sequence(placemark, fallback=len(parsed) + 1)
            if sequence in seen_sequences:
                sequence = len(parsed) + 1
            seen_sequences.add(sequence)
            parsed.append(
                {
                    "sequence": sequence,
                    "latitude": latitude,
                    "longitude": longitude,
                    "altitude": altitude,
                    "speed": _decimal_or_none(_first_text(placemark, "waypointSpeed")) or folder_speed,
                    "heading": _decimal_or_none(_first_text(placemark, "waypointHeadingAngle")),
                    "hoverSeconds": 0,
                }
            )
    return sorted(parsed, key=lambda item: item["sequence"])


def _point_coordinates(placemark: ElementTree.Element) -> tuple[Decimal, Decimal, Decimal | None] | None:
    point = _first_element(placemark, "Point")
    if point is None:
        return None
    raw_coordinates = _first_text(point, "coordinates")
    if not raw_coordinates:
        return None
    first_coordinate = raw_coordinates.strip().split()[0]
    parts = [part.strip() for part in first_coordinate.split(",")]
    if len(parts) < 2:
        raise serializers.ValidationError({"kmzFile": ["KMZ 航点坐标格式不正确"]})
    longitude = _decimal_or_error(parts[0], "KMZ 航点经度格式不正确")
    latitude = _decimal_or_error(parts[1], "KMZ 航点纬度格式不正确")
    altitude = _decimal_or_error(parts[2], "KMZ 航点高度格式不正确") if len(parts) >= 3 and parts[2] else None
    return longitude, latitude, altitude


def _sequence(placemark: ElementTree.Element, *, fallback: int) -> int:
    raw_index = _first_text(placemark, "index")
    if raw_index in (None, ""):
        return fallback
    try:
        return int(str(raw_index).strip()) + 1
    except ValueError as exc:
        raise serializers.ValidationError({"kmzFile": ["KMZ 航点 index 格式不正确"]}) from exc


def _default_speed(root: ElementTree.Element, waypoints: list[dict]) -> Decimal | None:
    root_speed = _decimal_or_none(_first_text(root, "autoFlightSpeed"))
    if root_speed is not None:
        return root_speed
    speeds = [item["speed"] for item in waypoints if item.get("speed") is not None]
    if len(speeds) == len(waypoints):
        return _common_decimal(speeds)
    return None


def _common_decimal(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    first = values[0]
    if all(value == first for value in values):
        return first
    return None


def _decimal_or_none(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _decimal_or_error(value, message: str) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise serializers.ValidationError({"kmzFile": [message]}) from exc


def _first_text(root: ElementTree.Element, local_name: str) -> str | None:
    element = _first_element(root, local_name)
    if element is None or element.text is None:
        return None
    return element.text.strip()


def _first_element(root: ElementTree.Element, local_name: str) -> ElementTree.Element | None:
    return next(_iter_by_local_name(root, local_name), None)


def _iter_by_local_name(root: ElementTree.Element, local_name: str):
    for element in root.iter():
        if _local_name(element.tag) == local_name:
            yield element


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _seek_start(file_obj) -> None:
    try:
        file_obj.seek(0)
    except Exception:  # noqa: BLE001 - upload wrappers expose inconsistent seek behavior.
        pass
