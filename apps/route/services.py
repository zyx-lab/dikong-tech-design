from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from xml.sax.saxutils import escape

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.waypoint.models import Waypoint


def _waypoint_attr(item, key: str):
    if isinstance(item, dict):
        return item[key]
    return getattr(item, key)


def replace_route_waypoints(route, waypoints):
    ordered_waypoints = sorted(waypoints, key=lambda item: item["sequence"])
    Waypoint.objects.filter(route=route).delete()
    Waypoint.objects.bulk_create(
        [
            Waypoint(
                route=route,
                sequence=item["sequence"],
                latitude=item["latitude"],
                longitude=item["longitude"],
                altitude=item["altitude"],
            )
            for item in ordered_waypoints
        ]
    )
    route.waypoint_count = len(ordered_waypoints)
    route.save(update_fields=["waypoint_count", "updated_at"])
    return ordered_waypoints


def build_route_kmz(route, waypoints: Iterable):
    ordered_waypoints = sorted(waypoints, key=lambda item: _waypoint_attr(item, "sequence"))
    coordinates = " ".join(
        f"{_waypoint_attr(point, 'longitude')},{_waypoint_attr(point, 'latitude')},{_waypoint_attr(point, 'altitude')}"
        for point in ordered_waypoints
    )
    placemarks = "\n".join(
        (
            "<Placemark>"
            f"<name>{_waypoint_attr(point, 'sequence')}</name>"
            "<Point>"
            f"<coordinates>{_waypoint_attr(point, 'longitude')},{_waypoint_attr(point, 'latitude')},{_waypoint_attr(point, 'altitude')}</coordinates>"
            "</Point>"
            "</Placemark>"
        )
        for point in ordered_waypoints
    )
    kml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<kml xmlns=\"http://www.opengis.net/kml/2.2\">"
        "<Document>"
        f"<name>{escape(route.name)}</name>"
        "<Placemark>"
        f"<name>{escape(route.name)}</name>"
        "<LineString>"
        f"<coordinates>{coordinates}</coordinates>"
        "</LineString>"
        "</Placemark>"
        f"{placemarks}"
        "</Document>"
        "</kml>"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("doc.kml", kml)

    return SimpleUploadedFile(
        name=f"route-{route.id}.kmz",
        content=buffer.getvalue(),
        content_type="application/vnd.google-earth.kmz",
    )
