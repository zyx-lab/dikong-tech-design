from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET

from django.core.files.uploadedfile import SimpleUploadedFile


def build_route_kmz_from_xml(route):
    if not route.xml_file or not route.xml_file.name:
        raise ValueError("route xml file missing")

    try:
        with route.xml_file.open("rb") as file_obj:
            raw = file_obj.read()
    except OSError as exc:
        raise ValueError("route xml file missing") from exc

    try:
        ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("stored route xml is invalid") from exc

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("route.xml", raw)

    return SimpleUploadedFile(
        name=f"route-{route.id}.kmz",
        content=buffer.getvalue(),
        content_type="application/vnd.google-earth.kmz",
    )
